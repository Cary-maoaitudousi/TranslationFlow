"""DeepSeek 客户端测试（验收标准 §12.3）。

用假客户端替换真实网络调用，覆盖：正常 JSON、空 content、非法 JSON 降级、
HTTP 429 重试、HTTP 401 不重试、预算闸门、缓存命中、成本落账。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tf.cost import BudgetExceeded, CostLog
from tf.deepseek_client import ApiFatalError, DeepSeekClient, backoff_delay, parse_json_loose


class HTTPErr(Exception):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.status_code = code


def make_response(content: str, finish_reason: str = "stop", prompt: int = 120,
                  completion: int = 60, cache_hit: int = 40):
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, prompt_cache_hit_tokens=cache_hit
    )
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class FakeClient:
    """按脚本依次返回响应或抛异常；脚本耗尽后重复最后一项。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def build(cfg, script, **kwargs):
    cost_log = CostLog(cfg.path("WORK_DIR") / "cost_log.csv")
    fake = FakeClient(script)
    client = DeepSeekClient(cfg, cost_log=cost_log, client=fake, sleep=lambda _s: None, **kwargs)
    return client, fake, cost_log


def call(client, **overrides):
    params = dict(
        step_name="3-人名识别", chapter_index=1,
        system="你是翻译家，只输出 json", user="text",
        schema_key="nouns", model="deepseek-flash", temperature=0.1, max_tokens=2000,
    )
    params.update(overrides)
    return client.call_json(**params)


def test_normal_json_ok(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    payload = {"entries": [{"source": "Longtail", "target": "长尾"}]}
    client, fake, cost_log = build(cfg, [make_response(json.dumps(payload, ensure_ascii=False))])

    result = call(client)
    assert result.json_parse_ok is True
    assert result.data["entries"][0]["target"] == "长尾"
    assert len(fake.calls) == 1

    # 请求参数必须满足 JSON Output 硬性要求
    sent = fake.calls[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert "json" in sent["messages"][0]["content"].lower()
    assert sent["extra_body"]["thinking"]["type"] == "disabled"

    rows = cost_log.rows()
    assert len(rows) == 1
    row = rows[0]
    for field in ("timestamp", "chapter_index", "step_name", "model", "thinking_enabled",
                  "prompt_tokens", "completion_tokens", "cache_hit_tokens", "latency_ms",
                  "retry_count", "finish_reason", "json_parse_ok"):
        assert field in row
    assert int(row["prompt_tokens"]) == 120
    assert float(row["cost_cny"]) >= 0


def test_empty_content_triggers_json_hardening_then_fallback(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    client, fake, _ = build(cfg, [make_response("   \n  ", "stop")])

    result = call(client)
    assert result.json_parse_ok is False
    assert len(fake.calls) == 2                     # 第一次 + 加固重问
    assert fake.calls[1]["temperature"] == 0.0      # 重问使用 temperature=0
    assert "json" in fake.calls[1]["messages"][-1]["content"].lower()


def test_invalid_json_falls_back_to_line_parsing(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    body = "Longtail->长尾\nFrostfur->霜毛"
    client, fake, _ = build(cfg, [make_response(body)])

    result = call(client)
    assert result.json_parse_ok is False
    assert result.fallback_used is True
    assert result.data["entries"] == [
        {"source": "Longtail", "target": "长尾"},
        {"source": "Frostfur", "target": "霜毛"},
    ]
    assert result.manual_review == []
    assert len(fake.calls) == 1                     # 宽松解析成功，不再重问


def test_bad_json_without_arrow_lines_goes_to_manual_review(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    client, fake, _ = build(cfg, [make_response("完全不是 JSON，也没有箭头")])

    result = call(client)
    assert result.json_parse_ok is False
    assert result.data == {}
    assert result.manual_review
    assert len(fake.calls) == 2


def test_http_429_retries_with_backoff(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False, RETRY_BACKOFF_BASE=0.0)
    ok = make_response(json.dumps({"entries": []}))
    client, fake, cost_log = build(cfg, [HTTPErr(429), HTTPErr(429), ok])

    result = call(client)
    assert result.json_parse_ok is True
    assert result.retry_count == 2
    assert len(fake.calls) == 3


def test_http_401_fails_fast_without_retry(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    client, fake, _ = build(cfg, [HTTPErr(401), make_response("{}")])

    with pytest.raises(ApiFatalError):
        call(client)
    assert len(fake.calls) == 1


def test_http_500_retries_at_most_three_times(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False, RETRY_BACKOFF_BASE=0.0)
    client, fake, _ = build(cfg, [HTTPErr(500)])

    with pytest.raises(Exception):
        call(client)
    assert len(fake.calls) == 4      # 首次 + 3 次重试


def test_cache_hit_skips_api_call(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=True)
    payload = {"entries": [{"source": "Longtail", "target": "长尾"}]}
    client, fake, cost_log = build(cfg, [make_response(json.dumps(payload, ensure_ascii=False))])

    first = call(client)
    second = call(client)
    assert first.cache_source == "api"
    assert second.cache_source == "local"
    assert len(fake.calls) == 1
    assert json.loads(cost_log.rows()[-1]["cost_cny"] or 0) == 0


def test_budget_guard_blocks_before_calling(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False, BUDGET_PER_CHAPTER_CNY=0.0000001)
    client, fake, _ = build(cfg, [make_response(json.dumps({"entries": []}))])

    with pytest.raises(BudgetExceeded):
        call(client)
    assert fake.calls == []


def test_thinking_flag_and_reasoning_effort(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    client, fake, _ = build(cfg, [make_response(json.dumps({"translation": "译文"}))])
    call(client, step_name="6-翻译", schema_key="translate", model="deepseek-v4-pro",
         thinking=True, reasoning_effort="high", max_tokens=3000)
    sent = fake.calls[0]
    assert sent["extra_body"]["thinking"]["type"] == "enabled"
    assert sent["reasoning_effort"] == "high"


def test_missing_api_key_raises_fatal(cfg_factory, monkeypatch):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = DeepSeekClient(cfg)
    with pytest.raises(ApiFatalError):
        _ = client.client


def test_mock_mode_never_calls_network(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=True)
    client, fake, cost_log = build(cfg, [make_response("never used")])
    result = call(client)
    assert result.cache_source == "mock"
    assert fake.calls == []
    assert cost_log.rows()[-1]["cache_source"] == "mock"


def test_backoff_capped_and_jittered():
    for i in range(8):
        assert 0 <= backoff_delay(i, 1.0, 60.0) <= 61.0


def test_length_truncation_retries_with_larger_budget(cfg_factory):
    """finish_reason=length 且 content 为空 → 扩容 max_tokens 重试，而不是重问 JSON。"""
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False, TRUNCATION_RETRIES=1)
    truncated = make_response("", "length", prompt=100, completion=2000)
    ok = make_response(json.dumps({"translation": "译文"}), prompt=100, completion=200)
    client, fake, _ = build(cfg, [truncated, ok])

    result = call(client, step_name="6-翻译", schema_key="translate",
                  model="deepseek-v4-pro", max_tokens=2000, thinking=True)
    assert result.json_parse_ok is True
    assert len(fake.calls) == 2
    assert fake.calls[1]["max_tokens"] > fake.calls[0]["max_tokens"]


def test_truncation_gives_up_after_limit(cfg_factory):
    cfg = cfg_factory(MOCK_MODE=False, CACHE_ENABLED=False, TRUNCATION_RETRIES=1)
    truncated = make_response("", "length", prompt=100, completion=2000)
    client, fake, _ = build(cfg, [truncated])

    result = call(client, step_name="6-翻译", schema_key="translate",
                  model="deepseek-v4-pro", max_tokens=2000, thinking=True)
    assert result.json_parse_ok is False
    # 截断重试 1 次 + JSON 加固重问 1 次
    assert len(fake.calls) == 3


def test_parse_json_loose_handles_code_fence():
    data, unmatched, strict = parse_json_loose('```json\n{"entries": []}\n```')
    assert strict is True
    assert data == {"entries": []}
    assert unmatched == []
