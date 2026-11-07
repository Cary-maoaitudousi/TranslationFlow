"""替换引擎测试（需求书 §4 / 验收标准 §12.5、§12.6）。"""

from __future__ import annotations

from tf.replacer import (
    mode_a_replace, mode_b_replace, preview_changes, has_cjk, is_punct,
)


# ---------------------------------------------------------------------------
# 模式 A
# ---------------------------------------------------------------------------
def test_whole_word_does_not_hurt_longer_names():
    entries = [{"source": "Fire", "target": "火"}]
    out = mode_a_replace("Fire and Firestar walked.", entries).text_out
    assert out == "火 and Firestar walked."


def test_case_sensitive_by_default():
    entries = [{"source": "Fire", "target": "火"}]
    out = mode_a_replace("fire and Fire", entries).text_out
    assert out == "fire and 火"


def test_case_insensitive_option():
    entries = [{"source": "Fire", "target": "火"}]
    out = mode_a_replace("fire and Fire", entries, case_sensitive=False).text_out
    assert out == "火 and 火"


def test_ascii_boundary_in_mixed_text():
    """中英混排时 \\b 不可靠，必须用 ASCII 边界断言。"""
    entries = [{"source": "Firestar", "target": "火星"}]
    out = mode_a_replace("火星Firestar的尾巴", entries).text_out
    assert out == "火星火星的尾巴"
    # 中文紧邻不应阻断匹配
    assert "Firestar" not in out


def test_chained_replacement_applies_token_to_previous_output():
    entries = [
        {"source": "Alpha", "target": "Beta"},
        {"source": "Beta", "target": "Gamma"},
    ]
    out = mode_a_replace("Alpha", entries, chained=True, sort_by_length_desc=False).text_out
    assert out == "Gamma"


def test_non_chained_does_not_cascade():
    entries = [
        {"source": "Alpha", "target": "Beta"},
        {"source": "Beta", "target": "Gamma"},
    ]
    out = mode_a_replace("Alpha", entries, chained=False, sort_by_length_desc=False).text_out
    assert out == "Beta"


def test_empty_target_deletes_match():
    entries = [{"source": "tail", "target": ""}]
    out = mode_a_replace("Long tail here", entries).text_out
    assert out == "Long  here"


def test_sort_by_length_desc_prevents_short_word_stealing():
    entries = [
        {"source": "Thistle", "target": "蓟"},
        {"source": "Thistleclaw", "target": "蓟掌"},
    ]
    out = mode_a_replace("Thistleclaw attacked.", entries, chained=False).text_out
    assert out == "蓟掌 attacked."


def test_report_counts_and_missing():
    entries = [{"source": "Firestar", "target": "火星"}, {"source": "Nobody", "target": "无人"}]
    report = mode_a_replace("Firestar Firestar", entries)
    by_src = {r.source: r for r in report.entries}
    assert by_src["Firestar"].count == 2
    assert by_src["Nobody"].status == "missing"
    assert report.total_replacements == 2


def test_idempotent_same_input_same_output():
    entries = [{"source": "Firestar", "target": "火星"}]
    a = mode_a_replace("Firestar and Sandstorm", entries).text_out
    b = mode_a_replace("Firestar and Sandstorm", entries).text_out
    assert a == b


def test_invalid_entries_skipped():
    entries = [{"source": "", "target": "x"}, {"source": "Firestar"}, {"source": "Firestar", "target": "火星"}]
    report = mode_a_replace("Firestar", entries)
    assert report.text_out == "火星"


# ---------------------------------------------------------------------------
# 模式 B
# ---------------------------------------------------------------------------
def test_mode_b_exact_substring_only():
    entries = [{"source": "两年", "target": "两猫"}]
    out = mode_b_replace("两年后，两人来了", entries).text_out
    assert out == "两猫后，两人来了"


def test_mode_b_missing_source_is_warned_and_skipped():
    entries = [{"source": "不存在的句子。", "target": "x"}]
    report = mode_b_replace("完全不同的译文。", entries)
    assert report.text_out == "完全不同的译文。"
    assert report.missing_sources == ["不存在的句子。"]
    assert report.entries[0].status == "missing"


def test_mode_b_length_desc_prevents_short_breaks():
    entries = [
        {"source": "两人", "target": "两猫"},
        {"source": "两人陷入了沉默", "target": "两猫陷入了沉默"},
    ]
    out = mode_b_replace("两人陷入了沉默。", entries).text_out
    assert out == "两猫陷入了沉默。"


def test_mode_b_chained_mode():
    entries = [
        {"source": "A", "target": "B"},
        {"source": "B", "target": "C"},
    ]
    non_chained = mode_b_replace("A", entries, chained=False).text_out
    chained = mode_b_replace("A", entries, chained=True).text_out
    assert non_chained == "B"
    assert chained == "C"


def test_mode_b_multiple_occurrences_replaced():
    entries = [{"source": "两人", "target": "两猫"}]
    report = mode_b_replace("两人说话，两人沉默", entries)
    assert report.text_out == "两猫说话，两猫沉默"
    assert report.entries[0].count == 2


def test_mode_b_punctuation_hint_logged():
    entries = [{"source": "他来了。", "target": "他来了"}]
    report = mode_b_replace("他来了。", entries)
    assert report.text_out == "他来了"


def test_mode_b_preserves_unrelated_text_exactly():
    text = "“可毕竟他们人太多了，我双拳难敌四手！”\n\n夜色很深。"
    entries = [{"source": "“可毕竟他们人太多了，我双拳难敌四手！”",
                "target": "“可毕竟他们猫太多了，我双爪难敌四掌！”"}]
    out = mode_b_replace(text, entries).text_out
    assert out.startswith("“可毕竟他们猫太多了，我双爪难敌四掌！”")
    assert out.endswith("夜色很深。")


def test_preview_changes_returns_only_affected_paragraphs():
    text = "第一段。\n\n两人陷入了沉默。\n\n第三段。"
    entries = [{"source": "两人陷入了沉默。", "target": "两猫陷入了沉默。"}]
    pairs = preview_changes(text, entries)
    assert len(pairs) == 1
    assert pairs[0][0].startswith("两人")
    assert pairs[0][1].startswith("两猫")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def test_has_cjk_and_is_punct():
    assert has_cjk("长尾")
    assert not has_cjk("Longtail")
    assert is_punct("。") and is_punct("，") and is_punct(",")
    assert not is_punct("猫")
