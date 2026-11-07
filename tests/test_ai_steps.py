"""AI 步骤封装测试（分块、去重、校验、上下文衔接）。"""

from __future__ import annotations

from tf import ai_steps
from tf.deepseek_client import LLMResult


class StubClient:
    """记录调用参数并返回预设 JSON 的假客户端。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def call_json(self, **kwargs) -> LLMResult:
        self.calls.append(kwargs)
        data = self.responder(len(self.calls), kwargs)
        return LLMResult(data=data, prompt_tokens=10, completion_tokens=5, cost_cny=0.0)


def test_translate_chunks_in_order_with_previous_context(cfg_factory):
    cfg = cfg_factory(MAX_INPUT_TOKENS_PER_CHUNK=60)
    text = "\n\n".join(f"Paragraph {i} has enough words to take up budget space." for i in range(12))

    stub = StubClient(lambda n, _kw: {"translation": f"第{n}块译文。", "notes": []})
    translation, notes, report = ai_steps.translate_chapter(
        stub, cfg, text, chapter_index=1, style_sample="样本正文", style_is_excerpt=False
    )

    assert report.chunks >= 2
    assert len(stub.calls) == report.chunks
    assert translation.startswith("第1块译文。")
    assert translation.count("\n\n") == report.chunks - 1     # 块间一个空行
    # 第二块起必须带"上一块译文"作为衔接参考，并明确不要重复翻译
    second_user = stub.calls[1]["user"]
    assert "参考上下文" in second_user
    assert "第1块译文" in second_user
    assert "不要重复输出" in second_user or "不要翻译" in second_user
    # 每块都不得把整章重复塞进去
    assert len(stub.calls[1]["user"]) < len(text)


def test_translate_uses_thinking_and_pro_model(cfg_factory):
    cfg = cfg_factory(THINKING_STEPS=[6, 7])
    stub = StubClient(lambda n, _kw: {"translation": "译文"})
    ai_steps.translate_chapter(stub, cfg, "One short paragraph.", 1, "样本", False)
    call = stub.calls[0]
    assert call["model"] == cfg.MODEL_PRO
    assert call["thinking"] is True
    assert call["reasoning_effort"] == "high"
    assert call["schema_key"] == "translate"
    assert call["max_tokens"] > 500


def test_step_model_override(cfg_factory):
    cfg = cfg_factory(MODEL_STEP6="deepseek-flash", THINKING_STEPS=[])
    stub = StubClient(lambda n, _kw: {"translation": "译文"})
    ai_steps.translate_chapter(stub, cfg, "One short paragraph.", 1, "样本", False)
    assert stub.calls[0]["model"] == "deepseek-flash"
    assert stub.calls[0]["thinking"] is False


def test_nouns_dedupe_and_filter_known(cfg_factory):
    cfg = cfg_factory(MAX_INPUT_TOKENS_PER_CHUNK=60)
    text = "\n\n".join(f"Frostfur watched Thistleclaw near the ridge number {i}." for i in range(12))

    def responder(_n, _kw):
        return {"entries": [
            {"source": "Thistleclaw", "target": "蓟掌"},
            {"source": "Frostfur", "target": "霜毛"},
            {"source": "长尾", "target": "长尾"},
            {"source": "", "target": "空"},
            {"source": "Firestar", "target": "火星"},
        ]}

    stub = StubClient(responder)
    entries, report = ai_steps.recognize_new_nouns(
        stub, cfg, text, known_sources={"Firestar"}, chapter_index=1
    )
    sources = [e["source"] for e in entries]
    assert sources.count("Thistleclaw") == 1        # 跨块去重
    assert sources.count("Frostfur") == 1
    assert "Firestar" not in sources                # 已在全局表
    assert all(s != "长尾" for s in sources)         # 已是中文
    assert report.chunks >= 2


def test_polish_flags_missing_source(cfg_factory):
    cfg = cfg_factory()
    translated = "两人陷入了沉默。"
    stub = StubClient(lambda n, _kw: {"entries": [
        {"source": "两人陷入了沉默。", "target": "两猫陷入了沉默。", "context": "两人→两猫"},
        {"source": "根本不存在的句子。", "target": "x"},
    ]})
    entries, report = ai_steps.detect_cultural_issues(stub, cfg, translated, chapter_index=1)
    by_src = {e["source"]: e for e in entries}
    assert "两人陷入了沉默。" in by_src
    assert by_src["根本不存在的句子。"]["_missing"] == "1"
    assert any("不存在" in w for w in report.warnings)


def test_polish_dedupes_same_source(cfg_factory):
    cfg = cfg_factory()
    stub = StubClient(lambda n, _kw: {"entries": [
        {"source": "两人", "target": "两猫"},
    ]})
    entries, _ = ai_steps.detect_cultural_issues(stub, cfg, "两人在这里", chapter_index=1)
    assert len(entries) == 1


def test_titles_warn_when_not_four_hanzi(cfg_factory):
    cfg = cfg_factory()
    stub = StubClient(lambda n, _kw: {"titles": [
        {"style": "意象", "title": "星池寒镜", "reason": "a"},
        {"style": "谶语", "title": "太长了标题", "reason": "b"},
        {"style": "场景", "title": "Moon", "reason": "c"},
    ]})
    titles, report = ai_steps.recommend_titles(stub, cfg, "章节译文", chapter_index=1)
    assert len(titles) == 3
    invalid = [t for t in titles if t.get("_invalid") == "1"]
    assert len(invalid) == 2
    assert any("不是四字" in w for w in report.warnings)
    assert any("不足 5 条" in w for w in report.warnings)


def test_style_sample_truncated_when_too_long(cfg_factory, tmp_path):
    long_text = "\n\n".join("他走进森林。" * 200 for _ in range(10))
    path = tmp_path / "style.txt"
    path.write_text(long_text, encoding="utf-8")
    cfg = cfg_factory(STYLE_SAMPLE_PATH=str(path), MAX_STYLE_SAMPLE_TOKENS=200)
    sample, is_excerpt = ai_steps.load_style_sample(cfg)
    assert is_excerpt is True
    assert len(sample) < len(long_text)


def test_style_sample_missing_is_tolerated(cfg_factory, tmp_path):
    cfg = cfg_factory(STYLE_SAMPLE_PATH=str(tmp_path / "nope.txt"))
    sample, is_excerpt = ai_steps.load_style_sample(cfg)
    assert sample == ""
    assert is_excerpt is False


def test_expand_phrase_builds_unique_context():
    text = "他走进空地。两人并肩站了一会儿，谁也没说话。"
    entries = ai_steps.expand_phrase(text, "两人", "两猫", "两人→两猫", window=6)
    assert entries
    for e in entries:
        assert e["source"] in text
        assert "两猫" in e["target"]
        assert "两人" not in e["target"]
        assert text.count(e["source"]) == 1
        assert e["source"] != e["target"]
        assert e["context"] == "两人→两猫"


def test_expand_phrase_returns_empty_when_not_found():
    assert ai_steps.expand_phrase("两人在这里", "四目", "目光") == []


def test_phrase_mode_filters_noop_and_expands(cfg_factory):
    cfg = cfg_factory(POLISH_OUTPUT_MODE="phrase")
    text = "两人并肩走进空地。众猫面面相觑，谁也不敢先开口。"
    stub = StubClient(lambda n, _kw: {"entries": [
        {"phrase": "两人", "replacement": "两猫", "context": "两边"},
        {"phrase": "两人", "replacement": "两人", "context": "无改动"},
        {"phrase": "根本没出现", "replacement": "x", "context": "缺失"},
    ]})
    entries, report = ai_steps.detect_cultural_issues(stub, cfg, text, chapter_index=1)
    real = [e for e in entries if e.get("_missing") != "1"]
    assert len(real) == 1
    assert "两猫" in real[0]["target"]
    assert real[0]["source"] in text
    assert any("找不到" in w for w in report.warnings)


def test_phrase_mode_uses_cheap_output_budget(cfg_factory):
    cfg = cfg_factory(POLISH_OUTPUT_MODE="phrase", THINKING_STEPS=[])
    stub = StubClient(lambda n, _kw: {"entries": []})
    ai_steps.detect_cultural_issues(stub, cfg, "两人在这里。" * 50, chapter_index=1)
    call = stub.calls[0]
    assert call["schema_key"] == "polish_phrase"
    assert call["max_tokens"] <= 3000


def test_sentence_mode_still_supported(cfg_factory):
    cfg = cfg_factory(POLISH_OUTPUT_MODE="sentence")
    text = "两人陷入了沉默。"
    stub = StubClient(lambda n, _kw: {"entries": [
        {"source": "两人陷入了沉默。", "target": "两猫陷入了沉默。", "context": "两人→两猫"},
        {"source": "两人陷入了沉默。", "target": "两人陷入了沉默。", "context": "空条目"},
    ]})
    entries, _ = ai_steps.detect_cultural_issues(stub, cfg, text, chapter_index=1)
    assert len(entries) == 1
    assert stub.calls[0]["schema_key"] == "polish"


def test_preflight_estimate_is_positive(cfg_factory):
    cfg = cfg_factory()
    est = ai_steps.preflight_cost_estimate(cfg, "Some English prose. " * 100)
    assert est["合计"] > 0
    keys = " ".join(est)
    for step in ("步骤3", "步骤6", "步骤7", "步骤10"):
        assert step in keys
    # 估算项里带上了实际使用的模型名，便于核对
    assert cfg.MODEL_PRO in keys or cfg.MODEL_FLASH in keys
