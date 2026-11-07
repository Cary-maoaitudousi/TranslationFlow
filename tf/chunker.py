"""长文本分块与 token 估算（需求书 §5.5）。

设计原则：
* 不引入 tiktoken 等重型依赖，用可解释的估算式；估算偏"保守"即可，真正的
  计费用 ``response.usage`` 的真实数字；
* 分块一定沿段落/行/句边界切，**绝不在句子中间断开**；
* 块间不重叠翻译，只把"上一块最后一段译文"作为参考上下文传给下一块。
"""

from __future__ import annotations

import math
import re
from typing import Iterable

SENTENCE_END = "。！？!?；;…\n"
PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "\u3400" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")


def estimate_tokens(text: str, chars_per_token_en: float = 4.0) -> int:
    """粗略估算 token 数。

    经验值：中文 1 字 ≈ 1 token；英文/标点约 4 字符 ≈ 1 token。
    估算误差在 ±20% 内即可，用于分块预算与成本预估。
    """
    if not text:
        return 0
    cjk = _cjk_count(text)
    other = len(text) - cjk
    return int(math.ceil(cjk * 1.0 + other / max(chars_per_token_en, 1.0))) + 1


def split_paragraphs(text: str) -> list[str]:
    """按空行切段；保留段内换行。"""
    blocks = [b.strip("\n") for b in PARA_SPLIT_RE.split(text.replace("\r\n", "\n"))]
    return [b for b in blocks if b.strip()]


def _split_by_lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.strip()]


def _split_by_sentences(text: str) -> list[str]:
    """按句末标点切分，标点留在句尾。"""
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in SENTENCE_END:
            piece = "".join(buf).strip()
            if piece:
                out.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _hard_split(text: str, max_tokens: int, chars_per_token_en: float) -> list[str]:
    """最后兜底：按字符数硬切（极少发生）。"""
    approx_chars = max(int(max_tokens * min(chars_per_token_en, 1.5)), 200)
    return [text[i: i + approx_chars] for i in range(0, len(text), approx_chars)]


def _split_oversized_unit(unit: str, max_tokens: int, chars_per_token_en: float) -> list[str]:
    """单段落超预算：先按行，再按句，最后硬切。"""
    for splitter in (_split_by_lines, _split_by_sentences):
        pieces = splitter(unit)
        if len(pieces) > 1:
            packed = pack_units(pieces, max_tokens, chars_per_token_en, joiner="\n" if splitter is _split_by_lines else "")
            if all(estimate_tokens(p, chars_per_token_en) <= max_tokens for p in packed):
                return packed
    return _hard_split(unit, max_tokens, chars_per_token_en)


def pack_units(units: Iterable[str], max_tokens: int, chars_per_token_en: float = 4.0, joiner: str = "\n\n") -> list[str]:
    """把若干"单元"（段落/行/句）按预算装箱，返回若干块。"""
    chunks: list[str] = []
    buf: list[str] = []
    for unit in units:
        candidate = joiner.join(buf + [unit]) if buf else unit
        if buf and estimate_tokens(candidate, chars_per_token_en) > max_tokens:
            chunks.append(joiner.join(buf))
            buf = [unit]
        else:
            buf.append(unit)
    if buf:
        chunks.append(joiner.join(buf))
    return chunks


def chunk_text(text: str, max_tokens: int, chars_per_token_en: float = 4.0) -> list[str]:
    """把长文本切成若干块，每块估算 token 数不超过 max_tokens。"""
    if estimate_tokens(text, chars_per_token_en) <= max_tokens:
        return [text] if text.strip() else []

    paragraphs = split_paragraphs(text) or [text]
    chunks: list[str] = []
    buf: list[str] = []
    for para in paragraphs:
        if estimate_tokens(para, chars_per_token_en) > max_tokens:
            if buf:
                chunks.append("\n\n".join(buf))
                buf = []
            chunks.extend(_split_oversized_unit(para, max_tokens, chars_per_token_en))
            continue
        candidate = "\n\n".join(buf + [para])
        if buf and estimate_tokens(candidate, chars_per_token_en) > max_tokens:
            chunks.append("\n\n".join(buf))
            buf = [para]
        else:
            buf.append(para)
    if buf:
        chunks.append("\n\n".join(buf))
    return [c for c in chunks if c.strip()]


def truncate_style_sample(
    text: str, max_tokens: int, chars_per_token_en: float = 4.0
) -> tuple[str, bool]:
    """文风样本超预算时，摘取代表性段落（开头 / 对话 / 战斗 / 心理描写）。

    返回 (样本文本, 是否为节选)。
    """
    if estimate_tokens(text, chars_per_token_en) <= max_tokens:
        return text, False

    paragraphs = split_paragraphs(text) or [text]
    picked: list[str] = []
    used: set[int] = set()

    def take(predicate, label: str) -> None:
        for i, para in enumerate(paragraphs):
            if i in used:
                continue
            if predicate(para):
                used.add(i)
                picked.append(f"【{label}】{para}")
                return

    take(lambda p: True, "开头")
    take(lambda p: p.count("“") + p.count('"') >= 2 or "！" in p or "？" in p, "对话")
    take(lambda p: any(k in p for k in ("爪", "牙齿", "鲜血", "撕", "扑", "吼")), "战斗")
    take(lambda p: any(k in p for k in ("心里", "想", "念头", "记忆", "感觉", "仿佛")), "心理")

    # 预算内继续按原顺序补足
    for i, para in enumerate(paragraphs):
        if i in used:
            continue
        picked.append(para)
        used.add(i)
        if estimate_tokens("\n\n".join(picked), chars_per_token_en) >= max_tokens:
            break

    sample = "\n\n".join(picked)
    # 兜底：仍超预算时按段落回退，最后按字符硬截
    while picked and estimate_tokens(sample, chars_per_token_en) > max_tokens:
        picked.pop()
        sample = "\n\n".join(picked)
    if estimate_tokens(sample, chars_per_token_en) > max_tokens:
        approx_chars = max(int(max_tokens * chars_per_token_en * 0.9), 100)
        sample = sample[:approx_chars]
    return sample, True


def join_chunks(chunks: Iterable[str]) -> str:
    """按顺序拼接译文块：块间保留一个空行，去掉块首尾多余空行。"""
    cleaned = [c.strip("\n") for c in chunks if c and c.strip()]
    return "\n\n".join(cleaned)
