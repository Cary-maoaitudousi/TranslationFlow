"""成本记录、峰谷计价与预算闸门（需求书 §5.6）。

DeepSeek 现行计价是**峰谷分时**：北京时间周一至周五 9:00-12:00、14:00-18:00
（不含法定节假日）为高峰，其余时间（含周末与节假日全天）为空闲，空闲价为高峰价的一半。
所以这里同时支持两种价目表结构：

    # 峰谷结构（推荐）
    {"deepseek-flash": {"peak": {...}, "offpeak": {...}}}
    # 扁平结构（兼容旧配置，两档同价）
    {"deepseek-flash": {"input_cache_hit": 0.02, ...}}

三件事：
1. **可见** —— 每次调用把真实 token 数写进 ``cost_log.csv``，可按章/按步骤汇总；
2. **可控** —— 调用前用估算值做预算预检，超预算直接抛 ``BudgetExceeded``；
3. **可省** —— 峰谷计价、缓存命中价、空闲时段等待（`WAIT_FOR_OFFPEAK`）。
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .logger import get_logger

log = get_logger("main")

CSV_FIELDS = [
    # —— 需求书 §5.6 规定的字段 ——
    "timestamp", "chapter_index", "step_name", "model", "thinking_enabled",
    "prompt_tokens", "completion_tokens", "cache_hit_tokens", "latency_ms",
    "retry_count", "finish_reason", "json_parse_ok",
    # —— 追加字段（向后兼容，仅排在末尾）——
    "prompt_cache_miss_tokens", "cost_cny", "cache_source", "is_estimate", "price_tier",
]

TIER_PEAK = "peak"
TIER_OFFPEAK = "offpeak"

DEFAULT_PEAK_WINDOWS = ["09:00-12:00", "14:00-18:00"]
DEFAULT_PEAK_WEEKDAYS = [0, 1, 2, 3, 4]   # 0=周一


class BudgetExceeded(RuntimeError):
    """预算闸门触发。属于 ERROR 级，终止当前章节。"""


# ---------------------------------------------------------------------------
# 峰谷判定
# ---------------------------------------------------------------------------
def _parse_hhmm(text: str) -> int:
    hh, mm = text.split(":", 1)
    return int(hh) * 60 + int(mm)


def in_window(minutes: int, window: str) -> bool:
    """判断分钟数是否落在窗口内（支持跨零点，如 "23:00-02:00"）。"""
    start_s, end_s = window.split("-", 1)
    start, end = _parse_hhmm(start_s.strip()), _parse_hhmm(end_s.strip())
    if start <= end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def is_peak(
    moment: dt.datetime | None = None,
    peak_windows: Iterable[str] = DEFAULT_PEAK_WINDOWS,
    peak_weekdays: Iterable[int] = DEFAULT_PEAK_WEEKDAYS,
    holidays: Iterable[str] = (),
) -> bool:
    """是否处于高峰时段（默认按北京时间的工作日上下午判定）。"""
    now = moment or dt.datetime.now()
    if now.strftime("%Y-%m-%d") in set(holidays):
        return False
    if now.weekday() not in set(peak_weekdays):
        return False
    minutes = now.hour * 60 + now.minute
    return any(in_window(minutes, w) for w in peak_windows)


def next_offpeak_start(
    moment: dt.datetime | None = None,
    peak_windows: Iterable[str] = DEFAULT_PEAK_WINDOWS,
    peak_weekdays: Iterable[int] = DEFAULT_PEAK_WEEKDAYS,
    holidays: Iterable[str] = (),
) -> dt.datetime:
    """返回下一个空闲时段起点（用于排队等待）。"""
    now = moment or dt.datetime.now()
    windows = list(peak_windows)
    if not windows:
        return now
    cursor = now.replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60):  # 最多往后找 8 天
        if not is_peak(cursor, windows, peak_weekdays, holidays):
            return cursor
        cursor += dt.timedelta(minutes=1)
    return now


def tier_of(cfg, moment: dt.datetime | None = None) -> str:
    """按配置决定计费档位：auto 看当前时间，也可强制 peak / offpeak。"""
    forced = str(cfg.get("PRICING_TIER", "auto") or "auto").lower()
    if forced in (TIER_PEAK, TIER_OFFPEAK):
        return forced
    peak = is_peak(
        moment,
        cfg.get("PEAK_WINDOWS", DEFAULT_PEAK_WINDOWS),
        cfg.get("PEAK_WEEKDAYS", DEFAULT_PEAK_WEEKDAYS),
        cfg.get("HOLIDAYS", []),
    )
    return TIER_PEAK if peak else TIER_OFFPEAK


# ---------------------------------------------------------------------------
# 计价
# ---------------------------------------------------------------------------
def price_for(model: str, price_table: Mapping[str, Mapping], tier: str = TIER_PEAK) -> dict[str, float]:
    """取某模型在某档位的单价（元/百万 tokens）。兼容扁平结构。"""
    entry = price_table.get(model) or price_table.get("_default") or {}
    if "peak" in entry or "offpeak" in entry:
        tier_entry = entry.get(tier) or entry.get(TIER_PEAK) or entry.get(TIER_OFFPEAK) or {}
        return {k: float(v) for k, v in tier_entry.items()}
    return {k: float(v) for k, v in entry.items() if isinstance(v, (int, float))}


def compute_cost_cny(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
    price_table: Mapping[str, Mapping] | None = None,
    tier: str = TIER_PEAK,
) -> float:
    """按 token 数与单价计算人民币成本。"""
    if not price_table:
        return 0.0
    price = price_for(model, price_table, tier)
    hit = max(0, min(int(cache_hit_tokens), int(prompt_tokens)))
    miss = max(0, int(prompt_tokens) - hit)
    cost = (
        hit / 1_000_000 * float(price.get("input_cache_hit", 0))
        + miss / 1_000_000 * float(price.get("input_cache_miss", 0))
        + int(completion_tokens) / 1_000_000 * float(price.get("output", 0))
    )
    return round(cost, 6)


def estimate_call_cost_cny(
    model: str,
    prompt_tokens: int,
    max_output_tokens: int,
    price_table: Mapping[str, Mapping],
    tier: str = TIER_PEAK,
) -> float:
    """调用前预估：输入全按未命中缓存、输出打满 max_tokens 的保守上界。"""
    return compute_cost_cny(model, prompt_tokens, max_output_tokens, 0, price_table, tier)


@dataclass
class CostRow:
    timestamp: str
    chapter_index: int
    step_name: str
    model: str
    thinking_enabled: bool
    prompt_tokens: int
    completion_tokens: int
    cache_hit_tokens: int
    latency_ms: int
    retry_count: int
    finish_reason: str
    json_parse_ok: bool
    prompt_cache_miss_tokens: int = 0
    cost_cny: float = 0.0
    cache_source: str = "api"      # api / local / mock
    is_estimate: bool = False
    price_tier: str = TIER_PEAK

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in CSV_FIELDS}


@dataclass
class CostSummary:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cost_cny: float = 0.0
    by_step: dict[str, float] = field(default_factory=dict)
    by_chapter: dict[int, float] = field(default_factory=dict)

    def describe(self) -> str:
        return (
            f"{self.calls} 次调用 | 输入 {self.prompt_tokens} tok（缓存命中 {self.cache_hit_tokens}）"
            f" | 输出 {self.completion_tokens} tok | 约 ¥{self.cost_cny:.4f}"
        )


class CostLog:
    """cost_log.csv 的读写与汇总。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8-sig") as fh:
                csv.DictWriter(fh, fieldnames=CSV_FIELDS).writeheader()

    def append(self, row: CostRow) -> None:
        with self.path.open("a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writerow(row.as_dict())

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))

    def summary(self, chapter_index: int | None = None) -> CostSummary:
        s = CostSummary()
        for raw in self.rows():
            try:
                ch = int(raw.get("chapter_index") or 0)
            except ValueError:
                ch = 0
            if chapter_index is not None and ch != chapter_index:
                continue
            s.calls += 1
            s.prompt_tokens += int(raw.get("prompt_tokens") or 0)
            s.completion_tokens += int(raw.get("completion_tokens") or 0)
            s.cache_hit_tokens += int(raw.get("cache_hit_tokens") or 0)
            cost = float(raw.get("cost_cny") or 0)
            s.cost_cny += cost
            step = raw.get("step_name") or "?"
            s.by_step[step] = round(s.by_step.get(step, 0.0) + cost, 6)
            s.by_chapter[ch] = round(s.by_chapter.get(ch, 0.0) + cost, 6)
        s.cost_cny = round(s.cost_cny, 6)
        return s


class BudgetGuard:
    """预算闸门：调用前预检，防止一次跑飞。"""

    def __init__(
        self,
        cost_log: CostLog,
        budget_total_cny: float = 0.0,
        budget_chapter_cny: float = 0.0,
    ):
        self.cost_log = cost_log
        self.budget_total = float(budget_total_cny or 0)
        self.budget_chapter = float(budget_chapter_cny or 0)
        self._chapter_spent: dict[int, float] = {}

    def add_spend(self, chapter_index: int, cost_cny: float) -> None:
        self._chapter_spent[chapter_index] = self._chapter_spent.get(chapter_index, 0.0) + float(cost_cny)

    def spent_on_chapter(self, chapter_index: int) -> float:
        return round(self._chapter_spent.get(chapter_index, 0.0), 6)

    def spent_total(self) -> float:
        return round(sum(self._chapter_spent.values()), 6)

    def preflight(self, chapter_index: int, est_cost_cny: float, step_name: str = "") -> None:
        if self.budget_chapter > 0:
            projected = self.spent_on_chapter(chapter_index) + est_cost_cny
            if projected > self.budget_chapter:
                raise BudgetExceeded(
                    f"本章预算 ¥{self.budget_chapter:.4f} 不足：已花 ¥{self.spent_on_chapter(chapter_index):.4f}，"
                    f"本步 {step_name} 预估 ¥{est_cost_cny:.4f}，预计合计 ¥{projected:.4f}"
                )
        if self.budget_total > 0:
            projected = self.spent_total() + est_cost_cny
            if projected > self.budget_total:
                raise BudgetExceeded(
                    f"总预算 ¥{self.budget_total:.4f} 不足：已花 ¥{self.spent_total():.4f}，"
                    f"本步 {step_name} 预估 ¥{est_cost_cny:.4f}，预计合计 ¥{projected:.4f}"
                )
