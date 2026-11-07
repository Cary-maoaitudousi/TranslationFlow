"""替换引擎（需求书 §4）。

两种模式严格分离，由调用方显式选择：

* 模式 A —— 专有名词替换：全词匹配 + 大小写敏感 + 连锁 + 长词优先；
* 模式 B —— 润色替换：精确子串匹配，source 必须与译文逐字符一致。

两者都返回结构化报告，供日志、审核窗口与章节汇总使用。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .logger import get_logger

log = get_logger("replacer")

Entry = Mapping[str, str]


def is_punct(ch: str) -> bool:
    """是否标点（含中英文标点）。"""
    if not ch:
        return False
    return unicodedata.category(ch).startswith("P")


def has_cjk(text: str) -> bool:
    """是否含中日韩字符（用于判断 source 是否已是中文）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


@dataclass
class EntryResult:
    source: str
    target: str
    count: int = 0
    status: str = "ok"            # ok / missing / skipped / empty_source
    context: str = ""
    hints: list[str] = field(default_factory=list)

    @property
    def replaced(self) -> bool:
        return self.count > 0


@dataclass
class ReplaceReport:
    mode: str
    text_out: str
    entries: list[EntryResult] = field(default_factory=list)
    total_replacements: int = 0

    @property
    def missing_sources(self) -> list[str]:
        return [r.source for r in self.entries if r.status == "missing"]


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------
def normalize_entries(entries: Iterable[Entry]) -> list[dict[str, str]]:
    """过滤非法条目：source/target 必须是字符串。

    * ``source`` 去首尾空白后为空 → 丢弃；
    * 缺少 ``target`` 键 → 丢弃（视为非法条目）；
    * ``target`` 存在但为空字符串 → 保留，语义是"删除匹配项"（§4.1）。
    """
    out: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        if "target" not in item:
            continue
        src = item.get("source", "")
        tgt = item.get("target", "")
        if not isinstance(src, str) or not isinstance(tgt, str):
            continue
        if not src.strip():
            continue
        out.append(
            {
                "source": src,
                "target": tgt,
                "context": str(item.get("context", "") or ""),
            }
        )
    return out


def _sorted_entries(
    entries: Sequence[dict[str, str]], sort_by_length_desc: bool
) -> list[dict[str, str]]:
    if not sort_by_length_desc:
        return list(entries)
    # 稳定排序：长 source 优先，等长时保持表内原顺序
    return sorted(entries, key=lambda e: -len(e["source"]))


# ---------------------------------------------------------------------------
# 模式 A：专有名词替换
# ---------------------------------------------------------------------------
def build_mode_a_pattern(source: str, whole_word: bool = True, case_sensitive: bool = True) -> re.Pattern:
    """构造模式 A 的匹配正则。

    ``\\b`` 在中英混排（"Firestar的"）时行为不可靠，因此用 ASCII 字母边界断言：
    前一个字符不是字母、后一个字符不是字母，即视为整词。
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    body = re.escape(source)
    if whole_word:
        body = r"(?<![A-Za-z])" + body + r"(?![A-Za-z])"
    return re.compile(body, flags)


def mode_a_replace(
    text: str,
    entries: Iterable[Entry],
    *,
    whole_word: bool = True,
    case_sensitive: bool = True,
    chained: bool = True,
    sort_by_length_desc: bool = True,
    collapse_spaces_on_delete: bool = False,
) -> ReplaceReport:
    """模式 A：专有名词替换。

    * ``chained=True``：逐条执行，后一条作用于前一条的输出；
    * ``sort_by_length_desc=True``：先按 source 长度降序排序再执行（长词优先，避免 Firestar→X 后 Fire 再命中）；
    * ``target == ""`` 等同于删除匹配项。
    """
    if not chained and not sort_by_length_desc:
        log.warning("模式 A 非连锁替换建议开启 sort_by_length_desc，否则短词可能先吃掉长词")

    table = _sorted_entries(normalize_entries(entries), sort_by_length_desc)
    report = ReplaceReport(mode="A", text_out=text)
    current = text

    if not chained:
        # 非连锁：在原文上一次性定位所有匹配，长词优先占位
        report.text_out, results = _apply_non_chained(text, table, case_sensitive, whole_word)
        report.entries = results
        report.total_replacements = sum(r.count for r in results)
        return report

    for entry in table:
        pattern = build_mode_a_pattern(entry["source"], whole_word, case_sensitive)
        matches = pattern.findall(current)
        count = len(matches)
        result = EntryResult(
            source=entry["source"], target=entry["target"], count=count,
            status="ok" if count else "missing", context=entry.get("context", ""),
        )
        if count:
            current = pattern.sub(lambda _m, t=entry["target"]: t, current)
            if entry["target"] == "" and collapse_spaces_on_delete:
                current = re.sub(r"[ \t]{2,}", " ", current)
        else:
            log.debug(f"模式A：source 未命中，跳过：{entry['source']}")
        report.entries.append(result)

    report.text_out = current
    report.total_replacements = sum(r.count for r in report.entries)
    return report


def _apply_non_chained(
    text: str, table: list[dict[str, str]], case_sensitive: bool, whole_word: bool
) -> tuple[str, list[EntryResult]]:
    """非连锁模式 A：所有规则同时作用于原文，重叠时更长者优先。"""
    occurrences: list[tuple[int, int, int]] = []
    for idx, entry in enumerate(table):
        pattern = build_mode_a_pattern(entry["source"], whole_word, case_sensitive)
        for m in pattern.finditer(text):
            occurrences.append((m.start(), m.end(), idx))

    occurrences.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    counts = [0] * len(table)
    pieces: list[str] = []
    cursor = 0
    for start, end, idx in occurrences:
        if start < cursor:
            continue
        pieces.append(text[cursor:start])
        pieces.append(table[idx]["target"])
        cursor = end
        counts[idx] += 1
    pieces.append(text[cursor:])

    results = [
        EntryResult(
            source=e["source"], target=e["target"], count=counts[i],
            status="ok" if counts[i] else "missing", context=e.get("context", ""),
        )
        for i, e in enumerate(table)
    ]
    return "".join(pieces), results


# ---------------------------------------------------------------------------
# 模式 B：润色替换
# ---------------------------------------------------------------------------
def mode_b_replace(
    text: str,
    entries: Iterable[Entry],
    *,
    exact_match: bool = True,
    sort_by_length_desc: bool = True,
    chained: bool = False,
) -> ReplaceReport:
    """模式 B：润色替换（精确子串）。

    * ``chained=False``（默认）：所有条目一次性作用于原文，重叠时更长者优先；
    * ``chained=True``：按表顺序逐条替换；
    * source 在译文中不存在 → WARNING，跳过，不中断；
    * 每条替换做标点连接检查，汇总成一条 INFO 提示。
    """
    table = _sorted_entries(normalize_entries(entries), sort_by_length_desc)
    report = ReplaceReport(mode="B", text_out=text)
    hints: list[str] = []

    if chained:
        current = text
        for entry in table:
            src, tgt = entry["source"], entry["target"]
            count = current.count(src)
            result = EntryResult(
                source=src, target=tgt, count=count,
                status="ok" if count else "missing", context=entry.get("context", ""),
            )
            if count:
                result.hints = _punct_hints(current, src, tgt)
                hints.extend(result.hints)
                current = current.replace(src, tgt)
            else:
                log.warning(f"模式B：source 在译文中不存在，已跳过：{src[:40]!r}")
            report.entries.append(result)
        report.text_out = current
    else:
        occurrences: list[tuple[int, int, int]] = []
        for idx, entry in enumerate(table):
            src = entry["source"]
            start = 0
            while True:
                pos = text.find(src, start)
                if pos < 0:
                    break
                occurrences.append((pos, pos + len(src), idx))
                start = pos + 1
        occurrences.sort(key=lambda t: (t[0], -(t[1] - t[0])))

        counts = [0] * len(table)
        pieces: list[str] = []
        cursor = 0
        for start, end, idx in occurrences:
            if start < cursor:
                continue
            pieces.append(text[cursor:start])
            pieces.append(table[idx]["target"])
            cursor = end
            counts[idx] += 1
        pieces.append(text[cursor:])
        report.text_out = "".join(pieces)

        for i, entry in enumerate(table):
            src, tgt = entry["source"], entry["target"]
            result = EntryResult(
                source=src, target=tgt, count=counts[i],
                status="ok" if counts[i] else "missing", context=entry.get("context", ""),
            )
            if counts[i]:
                result.hints = _punct_hints(text, src, tgt)
                hints.extend(result.hints)
            else:
                log.warning(f"模式B：source 在译文中不存在，已跳过：{src[:40]!r}")
            report.entries.append(result)

    report.total_replacements = sum(r.count for r in report.entries)
    if hints:
        shown = "；".join(hints[:5])
        more = f"（另有 {len(hints) - 5} 条）" if len(hints) > 5 else ""
        log.info(f"模式B 标点连接提示 {len(hints)} 处，建议人工复核：{shown}{more}")
    return report


def _punct_hints(text: str, source: str, target: str) -> list[str]:
    """标点连接检查：target 末尾字符与 source 后一个字符的标点属性是否一致。"""
    hints: list[str] = []
    start = 0
    while True:
        pos = text.find(source, start)
        if pos < 0:
            break
        after = text[pos + len(source): pos + len(source) + 1]
        tail = target[-1] if target else ""
        if tail and after:
            if is_punct(tail) == is_punct(after):
                kind = "标点" if is_punct(tail) else "非标点"
                hints.append(f"{source[:20]}… → {target[:20]}…（target 末尾与原文后续均为{kind}）")
        start = pos + 1
    return hints


# ---------------------------------------------------------------------------
# 审核窗口用：预览受影响段落
# ---------------------------------------------------------------------------
def preview_changes(text: str, entries: Iterable[Entry]) -> list[tuple[str, str]]:
    """对译文做一次内存中的模式 B 替换，返回被影响的 (原段落, 新段落) 对。"""
    report = mode_b_replace(text, entries)
    before = [p for p in text.split("\n") if p.strip()]
    after = [p for p in report.text_out.split("\n") if p.strip()]
    pairs: list[tuple[str, str]] = []
    for b, a in zip(before, after):
        if b != a:
            pairs.append((b, a))
    return pairs
