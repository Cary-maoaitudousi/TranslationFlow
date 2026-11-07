"""四个 AI 步骤的业务封装（需求书 §6）。

这一层只做三件事：拼输入、调客户端、校验输出。任何"便宜的事"（分块、去重、
过滤、格式校验）都在本地做，不消耗 token。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import prompts
from .chunker import chunk_text, estimate_tokens, join_chunks, truncate_style_sample
from .config import Config, step_model, step_thinking
from .cost import estimate_call_cost_cny, tier_of
from .deepseek_client import DeepSeekClient
from .logger import get_logger
from .replacer import has_cjk
from .fandom import profile_for
from .sizing import budget_for, plan_translation, summarize

log = get_logger("deepseek")

ProgressFn = Callable[[int, str], None]


def _noop(step: int, msg: str) -> None:  # pragma: no cover
    return None


@dataclass
class StepReport:
    """单步的统计信息，进 chapter_summary.json。"""

    step: int
    name: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    retries: int = 0
    fallback: bool = False
    chunks: int = 0
    items: int = 0
    warnings: list[str] = field(default_factory=list)

    def absorb(self, result, note: str = "") -> None:
        self.calls += 1
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        self.cost_cny = round(self.cost_cny + result.cost_cny, 6)
        self.retries += result.retry_count
        self.fallback = self.fallback or not result.json_parse_ok
        if note:
            self.warnings.append(note)

    def as_dict(self) -> dict:
        return {
            "step": self.step, "name": self.name, "calls": self.calls,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "cost_cny": self.cost_cny, "retries": self.retries, "fallback": self.fallback,
            "chunks": self.chunks, "items": self.items, "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# 步骤 3：识别未翻译的新专有名词
# ---------------------------------------------------------------------------
def recognize_new_nouns(
    client: DeepSeekClient,
    cfg: Config,
    text: str,
    known_sources: set[str],
    chapter_index: int,
    progress: ProgressFn = _noop,
) -> tuple[list[dict[str, str]], StepReport]:
    report = StepReport(step=3, name="人名识别")
    chunks = chunk_text(text, int(cfg.MAX_INPUT_TOKENS_PER_CHUNK), float(cfg.CHARS_PER_TOKEN_EN))
    report.chunks = len(chunks)
    thinking = step_thinking(cfg, 3)
    model = step_model(cfg, 3)
    profile = profile_for(cfg)

    seen: dict[str, dict[str, str]] = {}
    for i, chunk in enumerate(chunks, start=1):
        progress(3, f"识别新专有名词 第 {i}/{len(chunks)} 块")
        est = estimate_tokens(chunk, float(cfg.CHARS_PER_TOKEN_EN))
        result = client.call_json(
            step_name="3-人名识别", chapter_index=chapter_index,
            system=prompts.nouns_system(profile), user=prompts.render_nouns_user(chunk),
            schema_key="nouns", model=model, temperature=0.1,
            max_tokens=budget_for(est, cfg, ratio=1.2, floor=1500, thinking=thinking),
            thinking=thinking,
        )
        report.absorb(result)

        raw_entries = result.data.get("entries", []) if isinstance(result.data, dict) else []
        if not isinstance(raw_entries, list):
            report.warnings.append(f"第 {i} 块 entries 不是数组，已忽略")
            continue
        for item in raw_entries:
            entry = _clean_entry(item)
            if entry is None:
                continue
            src = entry["source"]
            if has_cjk(src):
                log.info(f"[步骤3] 过滤：source 已是中文 {src!r}")
                continue
            if src in known_sources:
                log.info(f"[步骤3] 过滤：专有名词表已存在 {src!r}")
                continue
            if src in seen:
                log.info(f"[步骤3] 去重：{src!r} 已在更早的块中出现")
                continue
            seen[src] = entry

    entries = list(seen.values())
    report.items = len(entries)
    log.info(f"[步骤3] 共识别新专有名词 {len(entries)} 条（{len(chunks)} 块）")
    return entries, report


# ---------------------------------------------------------------------------
# 步骤 6：全文翻译
# ---------------------------------------------------------------------------
def load_style_sample(cfg: Config) -> tuple[str, bool]:
    """读取文风样本并按预算截取，返回 (样本, 是否节选)。"""
    path = cfg.path("STYLE_SAMPLE_PATH")
    if not path.exists():
        log.warning(f"文风样本不存在，按零样本翻译：{path}")
        return "", False
    text = path.read_text(encoding="utf-8", errors="replace")
    return truncate_style_sample(
        text, int(cfg.MAX_STYLE_SAMPLE_TOKENS), float(cfg.CHARS_PER_TOKEN_EN)
    )


def translate_chapter(
    client: DeepSeekClient,
    cfg: Config,
    text: str,
    chapter_index: int,
    style_sample: str | None = None,
    style_is_excerpt: bool = False,
    progress: ProgressFn = _noop,
) -> tuple[str, list[str], StepReport]:
    report = StepReport(step=6, name="翻译")
    if style_sample is None:
        style_sample, style_is_excerpt = load_style_sample(cfg)

    profile = profile_for(cfg)
    system = prompts.translate_system(profile, style_sample, style_is_excerpt)
    thinking = step_thinking(cfg, 6)
    model = step_model(cfg, 6)
    chunks, plans = plan_translation(text, cfg, thinking)
    report.chunks = len(chunks)
    log.info(f"[步骤6] 分块计划：{summarize(plans)}")

    translations: list[str] = []
    all_notes: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        plan = plans[i - 1]
        progress(6, f"翻译 第 {i}/{len(chunks)} 块（{plan.chars} 字符，输出额度 {plan.max_output_tokens}）")
        prev = translations[-1] if translations else ""
        max_tokens = plan.max_output_tokens
        result = client.call_json(
            step_name=f"6-翻译[{i}/{len(chunks)}]", chapter_index=chapter_index,
            system=system, user=prompts.render_translate_user(chunk, prev),
            schema_key="translate", model=model, temperature=0.3,
            max_tokens=max_tokens, thinking=thinking,
            reasoning_effort=cfg.REASONING_EFFORT if thinking else None,
        )
        report.absorb(result)
        piece = result.data.get("translation") if isinstance(result.data, dict) else None
        if not isinstance(piece, str) or not piece.strip():
            report.warnings.append(f"第 {i} 块译文为空，已跳过")
            log.warning(f"[步骤6] 第 {i} 块译文为空，跳过")
            continue
        translations.append(piece.strip())
        notes = result.data.get("notes") if isinstance(result.data, dict) else None
        if isinstance(notes, list):
            all_notes.extend(str(n) for n in notes if str(n).strip())

    translation = join_chunks(translations)
    report.items = len(translations)
    if all_notes:
        log.info(f"[步骤6] 模型备注 {len(all_notes)} 条：" + "；".join(all_notes[:5]))
    log.info(f"[步骤6] 翻译完成：{len(chunks)} 块，输出 {len(translation)} 字")
    return translation, all_notes, report


# ---------------------------------------------------------------------------
# 步骤 7：检测文化不适配表达
# ---------------------------------------------------------------------------
def expand_phrase(
    text: str,
    phrase: str,
    replacement: str,
    context: str = "",
    window: int = 14,
    max_hits: int = 2,
) -> list[dict[str, str]]:
    """把"短语 → 改法"展开成"带上下文的 source → target"（模式 B 可直接替换）。

    上下文长度会自动加长到能在译文中唯一定位为止，避免短片段误替换。
    """
    if not phrase or phrase not in text or phrase == replacement:
        return []
    out: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    start, hits = 0, 0
    while hits < max_hits:
        pos = text.find(phrase, start)
        if pos < 0:
            break
        hits += 1
        start = pos + len(phrase)
        win = window
        src = tgt = ""
        for _ in range(4):  # 逐步加长，直到 source 在全文唯一
            s = max(0, pos - win)
            e = min(len(text), pos + len(phrase) + win)
            src = text[s:e]
            tgt = src[: pos - s] + replacement + src[pos - s + len(phrase):]
            if text.count(src) == 1 or win >= 60:
                break
            win += 12
        if not src or src == tgt or src in seen_sources:
            continue
        seen_sources.add(src)
        out.append({"source": src, "target": tgt, "context": context})
    return out


def detect_cultural_issues(
    client: DeepSeekClient,
    cfg: Config,
    translated_text: str,
    chapter_index: int,
    progress: ProgressFn = _noop,
) -> tuple[list[dict[str, str]], StepReport]:
    report = StepReport(step=7, name="润色检测")
    chunks = chunk_text(translated_text, int(cfg.MAX_INPUT_TOKENS_PER_CHUNK), float(cfg.CHARS_PER_TOKEN_EN))
    report.chunks = len(chunks)
    thinking = step_thinking(cfg, 7)
    model = step_model(cfg, 7)
    profile = profile_for(cfg)
    max_entries = int(cfg.get("MAX_POLISH_ENTRIES", 30))
    mode = str(cfg.get("POLISH_OUTPUT_MODE", "phrase"))
    window = int(cfg.get("POLISH_CONTEXT_WINDOW", 14))

    seen: dict[str, dict[str, str]] = {}
    filtered_same = 0
    for i, chunk in enumerate(chunks, start=1):
        if len(seen) >= max_entries:
            break
        progress(7, f"检测文化不适配表达 第 {i}/{len(chunks)} 块（{mode} 模式）")
        est = estimate_tokens(chunk, float(cfg.CHARS_PER_TOKEN_EN))
        if mode == "phrase":
            system, user, schema_key = (
                prompts.polish_system(profile), prompts.render_polish_phrase_user(chunk),
                "polish_phrase",
            )
            max_out = budget_for(est, cfg, ratio=0.6, floor=1500, thinking=thinking)
        else:
            system, user, schema_key = (
                prompts.polish_sentence_system(profile), prompts.render_polish_user(chunk), "polish",
            )
            max_out = budget_for(est, cfg, ratio=2.4, floor=2000, thinking=thinking)

        result = client.call_json(
            step_name=f"7-润色检测[{i}/{len(chunks)}]", chapter_index=chapter_index,
            system=system, user=user, schema_key=schema_key, model=model,
            temperature=0.1, max_tokens=max_out, thinking=thinking,
            reasoning_effort=cfg.REASONING_EFFORT if thinking else None,
        )
        report.absorb(result)

        raw_entries = result.data.get("entries", []) if isinstance(result.data, dict) else []
        if not isinstance(raw_entries, list):
            report.warnings.append(f"第 {i} 块 entries 不是数组，已忽略")
            continue

        for item in raw_entries:
            if len(seen) >= max_entries:
                report.warnings.append(f"润色候选达到上限 {max_entries} 条，后续已忽略")
                break
            if not isinstance(item, dict):
                continue
            if mode == "phrase":
                phrase = item.get("phrase") or item.get("source") or ""
                replacement = item.get("replacement") or item.get("target") or ""
                if not isinstance(phrase, str) or not isinstance(replacement, str):
                    continue
                if phrase.strip() == replacement.strip():
                    filtered_same += 1
                    continue
                expanded = expand_phrase(
                    chunk, phrase, replacement, str(item.get("context", "") or ""), window
                )
                if not expanded:
                    warn = f"短语在译文中找不到（待人工确认）：{phrase[:30]!r}"
                    log.warning(f"[步骤7] {warn}")
                    report.warnings.append(warn)
                    entry = {
                        "source": phrase, "target": replacement,
                        "context": str(item.get("context", "") or ""), "_missing": "1",
                    }
                    seen.setdefault(phrase, entry)
                    continue
                for entry in expanded:
                    if len(seen) >= max_entries or entry["source"] in seen:
                        continue
                    seen[entry["source"]] = entry
                continue

            entry = _clean_entry(item, require_strict=True)
            if entry is None:
                continue
            if entry["target"].strip() == entry["source"].strip():
                filtered_same += 1
                continue
            src = entry["source"]
            if src in seen:
                log.info(f"[步骤7] 去重：{src[:24]!r}…")
                continue
            if src not in translated_text:
                entry["_missing"] = "1"
                warn = f"source 在译文中不存在（待人工确认）：{src[:30]!r}"
                log.warning(f"[步骤7] {warn}")
                report.warnings.append(warn)
            elif translated_text.count(src) > 1:
                log.info(f"[步骤7] source 命中多处，替换将全部生效：{src[:24]!r}…")
            seen[src] = entry

    if filtered_same:
        log.info(f"[步骤7] 过滤 source==target 的空条目 {filtered_same} 条")
    entries = list(seen.values())
    report.items = len(entries)
    log.info(f"[步骤7] 共检测润色候选 {len(entries)} 条（{mode} 模式）")
    return entries, report


# ---------------------------------------------------------------------------
# 步骤 10：推荐章节名称
# ---------------------------------------------------------------------------
FOUR_HANZI = re.compile(r"^[\u4e00-\u9fff]{4}$")


def recommend_titles(
    client: DeepSeekClient,
    cfg: Config,
    final_chinese: str,
    chapter_index: int,
) -> tuple[list[dict[str, str]], StepReport]:
    report = StepReport(step=10, name="章名推荐")
    profile = profile_for(cfg)
    result = client.call_json(
        step_name="10-章名推荐", chapter_index=chapter_index,
        system=prompts.titles_system(profile),
        user=prompts.render_titles_user(final_chinese, profile.name, int(cfg.get("TITLE_INPUT_CHAR_LIMIT", 6000))),
        schema_key="titles", model=step_model(cfg, 10), temperature=0.7,
        max_tokens=800, thinking=step_thinking(cfg, 10),
    )
    report.absorb(result)
    report.chunks = 1

    raw = result.data.get("titles", []) if isinstance(result.data, dict) else []
    titles: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            entry = {
                "style": str(item.get("style", "")).strip(),
                "title": title,
                "reason": str(item.get("reason", "")).strip(),
            }
            if not FOUR_HANZI.match(title):
                entry["_invalid"] = "1"
                report.warnings.append(f"章名不是四字：{title!r}")
                log.warning(f"[步骤10] 章名不是四字汉字：{title!r}")
            titles.append(entry)

    if len(titles) < 5:
        report.warnings.append(f"章名候选不足 5 条（实际 {len(titles)} 条）")
        log.warning(f"[步骤10] 章名候选只有 {len(titles)} 条")
    report.items = len(titles)
    return titles, report


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _clean_entry(item, require_strict: bool = False) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    src = item.get("source")
    tgt = item.get("target")
    if not isinstance(src, str) or not isinstance(tgt, str):
        return None
    src = src if require_strict else src.strip()
    if not src.strip() or not tgt.strip():
        return None
    entry = {"source": src, "target": tgt}
    if not require_strict:
        # 专有名词不允许带空白污染
        entry["source"] = src.strip()
    context = item.get("context")
    if isinstance(context, str) and context.strip():
        entry["context"] = context.strip()
    return entry


def preflight_cost_estimate(
    cfg: Config, text: str, translated_len: int | None = None
) -> dict[str, float]:
    """运行前给用户一个"这章大概要多少钱"的估算（不调用 API）。

    与真实调用走同一套分块计划、同一套每步模型/思考开关，并按"输出打满额度"
    取保守上界；system prompt 里的文风样本按 ``MAX_STYLE_SAMPLE_TOKENS`` 计入每块输入。
    金额按当前峰谷档位计（`PRICING_TIER=auto` 时看现在是不是高峰）。
    """
    chunks, plans = plan_translation(text, cfg, step_thinking(cfg, 6))
    in_tok = sum(p.est_input_tokens for p in plans)
    style_tok = min(int(cfg.MAX_STYLE_SAMPLE_TOKENS), 2000) if cfg.path("STYLE_SAMPLE_PATH").exists() else 0
    out_tok = translated_len or int(in_tok * 1.2)
    pt = cfg.PRICE_TABLE
    tier = tier_of(cfg)
    max_chunk_in = max((p.est_input_tokens for p in plans), default=in_tok)

    step3 = estimate_call_cost_cny(
        step_model(cfg, 3), in_tok,
        budget_for(max_chunk_in, cfg, ratio=1.2, floor=1500, thinking=step_thinking(cfg, 3)),
        pt, tier,
    )
    step6 = sum(
        estimate_call_cost_cny(
            step_model(cfg, 6), p.est_input_tokens + style_tok, p.max_output_tokens, pt, tier
        )
        for p in plans
    )
    step7_ratio, step7_floor = (
        (0.6, 1500) if str(cfg.get("POLISH_OUTPUT_MODE", "phrase")) == "phrase" else (2.4, 2000)
    )
    step7 = estimate_call_cost_cny(
        step_model(cfg, 7), out_tok,
        budget_for(out_tok, cfg, ratio=step7_ratio, floor=step7_floor,
                   thinking=step_thinking(cfg, 7)),
        pt, tier,
    )
    step10 = estimate_call_cost_cny(
        step_model(cfg, 10), min(in_tok, int(cfg.TITLE_INPUT_CHAR_LIMIT / 2)), 800, pt, tier
    )
    est = {
        f"步骤3 人名识别（{step_model(cfg, 3)}）": round(step3, 6),
        f"步骤6 翻译（{step_model(cfg, 6)}）": round(step6, 6),
        f"步骤7 润色检测（{step_model(cfg, 7)}）": round(step7, 6),
        f"步骤10 章名推荐（{step_model(cfg, 10)}）": round(step10, 6),
    }
    est["合计"] = round(sum(est.values()), 6)
    est["_chunks"] = float(len(chunks))
    est["_est_input_tokens"] = float(in_tok)
    return est
