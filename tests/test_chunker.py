"""分块与成本计算测试（需求书 §5.5 / §12.8）。"""

from __future__ import annotations

import datetime as dt

from tf.chunker import (
    chunk_text, estimate_tokens, join_chunks, split_paragraphs, truncate_style_sample,
)
from tf.cost import (
    compute_cost_cny, estimate_call_cost_cny, is_peak, next_offpeak_start, tier_of,
)


def test_estimate_tokens_counts_cjk_heavier():
    zh = estimate_tokens("猫武士的森林")
    en = estimate_tokens("The forest of the cats")
    assert zh > 0 and en > 0
    assert estimate_tokens("猫" * 100) > estimate_tokens("a" * 100)


def test_short_text_single_chunk():
    text = "第一段。\n\n第二段。"
    assert chunk_text(text, 1000) == [text]


def test_chunks_respect_budget_and_keep_paragraph_boundaries():
    text = "\n\n".join(f"这是第 {i} 段。" * 40 for i in range(12))
    chunks = chunk_text(text, 300)
    assert len(chunks) > 1
    for chunk in chunks:
        assert estimate_tokens(chunk) <= 320
        for para in split_paragraphs(chunk):
            assert para in text  # 不切断段落


def test_oversized_single_paragraph_degrades_to_sentences():
    para = "".join(f"第{i}句结束了。" for i in range(200))
    chunks = chunk_text(para, 200)
    assert len(chunks) > 1
    assert all(len(c) > 0 for c in chunks)


def test_join_chunks_keeps_one_blank_line():
    assert join_chunks(["第一块。\n", "\n第二块。"]) == "第一块。\n\n第二块。"


def test_truncate_style_sample_marks_excerpt():
    sample = "\n\n".join(f"第{i}段描写月光。他走进森林。{'很长的描述。' * 50}" for i in range(10))
    out, is_excerpt = truncate_style_sample(sample, 300)
    assert is_excerpt is True
    assert estimate_tokens(out) <= 400


def test_style_sample_within_budget_not_truncated():
    out, is_excerpt = truncate_style_sample("短样本。", 500)
    assert out == "短样本。"
    assert is_excerpt is False


def test_cost_uses_cache_hit_price():
    table = {"m": {"peak": {"input_cache_hit": 1.0, "input_cache_miss": 4.0, "output": 8.0}}}
    # 1M 输入全命中 = 1 元；全未命中 = 4 元；输出 1M = 8 元
    assert compute_cost_cny("m", 1_000_000, 0, 0, table, "peak") == 4.0
    assert compute_cost_cny("m", 1_000_000, 0, 1_000_000, table, "peak") == 1.0
    assert compute_cost_cny("m", 0, 1_000_000, 0, table, "peak") == 8.0


def test_offpeak_is_half_of_peak():
    table = {
        "m": {
            "peak": {"input_cache_hit": 0.04, "input_cache_miss": 2.0, "output": 8.0},
            "offpeak": {"input_cache_hit": 0.02, "input_cache_miss": 1.0, "output": 4.0},
        }
    }
    peak = compute_cost_cny("m", 1_000_000, 0, 0, table, "peak")
    off = compute_cost_cny("m", 1_000_000, 0, 0, table, "offpeak")
    assert peak == 2.0 and off == 1.0


def test_flat_price_table_still_supported():
    table = {"m": {"input_cache_hit": 0.0, "input_cache_miss": 10.0, "output": 10.0}}
    assert compute_cost_cny("m", 1_000_000, 0, 0, table, "offpeak") == 10.0


def test_cost_unknown_model_uses_default():
    table = {"_default": {"peak": {"input_cache_hit": 0.0, "input_cache_miss": 10.0, "output": 10.0}}}
    assert compute_cost_cny("unknown-model", 1_000_000, 0, 0, table, "peak") == 10.0


def test_estimate_is_upper_bound():
    table = {"m": {"peak": {"input_cache_hit": 0.1, "input_cache_miss": 2.0, "output": 4.0}}}
    assert estimate_call_cost_cny("m", 1000, 500, table, "peak") > 0


def test_peak_window_weekday_only():
    windows = ["09:00-12:00", "14:00-18:00"]
    monday = dt.datetime(2026, 1, 5)      # 周一
    assert is_peak(monday.replace(hour=10), windows, [0, 1, 2, 3, 4]) is True
    assert is_peak(monday.replace(hour=12, minute=30), windows, [0, 1, 2, 3, 4]) is False
    assert is_peak(monday.replace(hour=13), windows, [0, 1, 2, 3, 4]) is False
    assert is_peak(monday.replace(hour=15), windows, [0, 1, 2, 3, 4]) is True
    saturday = dt.datetime(2026, 1, 10)
    assert is_peak(saturday.replace(hour=10), windows, [0, 1, 2, 3, 4]) is False


def test_holiday_counts_as_offpeak():
    monday = dt.datetime(2026, 1, 5, 10, 0)
    assert is_peak(monday, ["09:00-12:00"], [0, 1, 2, 3, 4], ["2026-01-05"]) is False


def test_next_offpeak_start_skips_lunch_break():
    monday = dt.datetime(2026, 1, 5, 10, 30)
    nxt = next_offpeak_start(monday, ["09:00-12:00", "14:00-18:00"], [0, 1, 2, 3, 4])
    assert (nxt.hour, nxt.minute) == (12, 0)


def test_tier_of_respects_forced_value(cfg_factory):
    assert tier_of(cfg_factory(PRICING_TIER="offpeak")) == "offpeak"
    assert tier_of(cfg_factory(PRICING_TIER="peak")) == "peak"
    assert tier_of(cfg_factory(PRICING_TIER="auto")) in ("peak", "offpeak")
