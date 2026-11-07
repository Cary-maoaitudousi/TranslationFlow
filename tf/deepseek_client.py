"""统一 DeepSeek 客户端（需求书 §5）。

全流程**只**在这里实例化 ``OpenAI``，其它模块一律通过 ``call_json`` 调用，
这样重试策略、JSON 校验、成本记录只有一处实现。

关键行为：
* 429 → 指数退避 + 抖动（上限 RETRY_BACKOFF_MAX，最多 MAX_RETRIES 次）；
* 超时 / 5xx → 同样退避，最多 3 次；
* 401 / 400 → 不重试，直接抛 ``ApiFatalError``；
* JSON 解析失败 → 先降级重问（temperature=0 并重申 schema），再降级为
  ``->`` 行文本宽松解析，失败行进 manual_review；
* 空 content 视为失败（DeepSeek 可能返回全空格且 finish_reason=stop）；
* 每次调用（含重试）都写一行 cost_log.csv；
* 相同请求默认走本地缓存，重跑同一章不再花钱。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .cost import (
    BudgetGuard, CostLog, CostRow, compute_cost_cny, estimate_call_cost_cny, tier_of,
)
from .logger import get_logger
from .prompts import JSON_SCHEMAS, render_json_reminder

log = get_logger("deepseek")


class ApiFatalError(RuntimeError):
    """401/400 等不可重试错误：终止当前章节。"""


class ApiTransientError(RuntimeError):
    """重试耗尽。"""


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw: str = ""
    json_parse_ok: bool = True
    fallback_used: bool = False
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cost_cny: float = 0.0
    cache_source: str = "api"       # api / local / mock
    retry_count: int = 0
    latency_ms: int = 0
    manual_review: list[str] = field(default_factory=list)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"```\s*$", "", t)
    return t.strip()


def parse_json_loose(content: str) -> tuple[dict | None, list[str], bool]:
    """解析模型输出。

    返回 ``(data, unmatched_lines, strict_ok)``：
    先按严格 JSON 解析；失败则逐行找 ``->`` 降级，认不出的行交给人工。
    """
    cleaned = _strip_code_fence(content)
    if cleaned:
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                return obj, [], True
        except json.JSONDecodeError:
            # 尝试截取第一个 { 到最后一个 }
            lo, hi = cleaned.find("{"), cleaned.rfind("}")
            if lo >= 0 and hi > lo:
                try:
                    obj = json.loads(cleaned[lo: hi + 1])
                    if isinstance(obj, dict):
                        return obj, [], True
                except json.JSONDecodeError:
                    pass

    entries: list[dict[str, str]] = []
    unmatched: list[str] = []
    for line in content.splitlines():
        line = line.strip().strip("-•*").strip()
        if not line:
            continue
        for sep in ("->", "→", "=>"):
            if sep in line:
                src, tgt = line.split(sep, 1)
                src, tgt = src.strip().strip('"“”'), tgt.strip().strip('"“”')
                if src and tgt:
                    entries.append({"source": src, "target": tgt})
                break
        else:
            if len(line) > 2:
                unmatched.append(line)
    if entries:
        return {"entries": entries}, unmatched, False
    return None, unmatched, False


class DeepSeekClient:
    def __init__(
        self,
        cfg: Config,
        cost_log: CostLog | None = None,
        budget_guard: BudgetGuard | None = None,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.cost_log = cost_log or CostLog(Path(cfg.path("WORK_DIR")) / "cost_log.csv")
        self.budget = budget_guard or BudgetGuard(
            self.cost_log, cfg.BUDGET_TOTAL_CNY, cfg.BUDGET_PER_CHAPTER_CNY
        )
        self._client = client
        self._sleep = sleep
        self._mock = bool(cfg.MOCK_MODE)
        self._cache_dir = cfg.path("CACHE_DIR")
        if cfg.CACHE_ENABLED:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # -- 客户端 ------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise ApiFatalError("缺少 openai 包：pip install openai") from exc
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            if not api_key:
                raise ApiFatalError("DEEPSEEK_API_KEY 未设置（请写入 .env）")
            self._client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                timeout=float(self.cfg.REQUEST_TIMEOUT_SEC),
                max_retries=0,  # 重试由本模块控制，避免双重重试放大费用
            )
        return self._client

    # -- 缓存 -------------------------------------------------
    def _cache_key(self, model: str, system: str, user: str, temperature: float, thinking: bool) -> str:
        h = hashlib.sha256()
        for part in (model, str(temperature), str(thinking), system, user):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def _cache_read(self, key: str) -> dict | None:
        if not self.cfg.CACHE_ENABLED:
            return None
        p = self._cache_dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            return payload.get("data")
        except (OSError, json.JSONDecodeError):
            log.warning(f"缓存文件损坏，忽略：{p}")
            return None

    def _cache_write(self, key: str, data: dict) -> None:
        if not self.cfg.CACHE_ENABLED:
            return
        try:
            (self._cache_dir / f"{key}.json").write_text(
                json.dumps({"data": data}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # pragma: no cover
            log.warning(f"写入缓存失败：{exc}")

    # -- 峰谷档位 ---------------------------------------------
    def _tier(self) -> str:
        """当前计费档位（peak / offpeak），可被 PRICING_TIER 强制。"""
        return tier_of(self.cfg)

    # -- 主入口 -----------------------------------------------
    def call_json(
        self,
        *,
        step_name: str,
        chapter_index: int,
        system: str,
        user: str,
        schema_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        thinking: bool = False,
        reasoning_effort: str | None = None,
        use_cache: bool = True,
    ) -> LLMResult:
        """发起一次受约束的 JSON 调用。"""
        schema_hint = JSON_SCHEMAS.get(schema_key, "{}")
        cache_key = self._cache_key(model, system, user, temperature, thinking)

        if use_cache:
            cached = self._cache_read(cache_key)
            if cached is not None:
                log.info(f"[步骤{step_name}] 命中本地缓存，跳过 API 调用（零成本）")
                self._write_cost_row(
                    chapter_index=chapter_index, step_name=step_name, model=model,
                    thinking=thinking, prompt=0, completion=0, cache_hit=0, latency=0,
                    retry_count=0, finish_reason="cache_hit", json_ok=True,
                    cost=0.0, cache_source="local",
                )
                return LLMResult(data=cached, raw="", json_parse_ok=True, cache_source="local")

        if self._mock:
            return self._mock_call(step_name, chapter_index, model, thinking, user, schema_key)

        prompt_est = estimate_call_cost_cny(
            model, _rough_tokens(system) + _rough_tokens(user), max_tokens,
            self.cfg.PRICE_TABLE, self._tier(),
        )
        self.budget.preflight(chapter_index, prompt_est, step_name)

        attempt = 0
        phase = 0          # 0=正常, 1=JSON 加固重问
        retry_count = 0
        truncation_retries = 0
        max_trunc_retries = int(self.cfg.get("TRUNCATION_RETRIES", 2))
        hard_cap = int(self.cfg.get("MAX_TOKENS_HARD_CAP", 16000))
        started = time.perf_counter()
        messages_system: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        while True:
            attempt += 1
            t0 = time.perf_counter()
            try:
                resp = self._create(
                    model=model,
                    messages=messages_system,
                    temperature=0.0 if phase else temperature,
                    max_tokens=max_tokens,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                )
            except Exception as exc:  # noqa: BLE001 - 需要按状态码分流
                kind, status = classify_error(exc)
                if kind == "fatal":
                    log.error(f"[步骤{step_name}] 不可重试错误 {status}：{exc}")
                    raise ApiFatalError(f"HTTP {status}: {exc}") from exc
                limit = int(self.cfg.MAX_RETRIES) if kind == "rate" else 3
                if retry_count >= limit:
                    log.error(f"[步骤{step_name}] 重试 {retry_count} 次仍失败：{exc}")
                    raise ApiTransientError(f"重试耗尽（{kind}）：{exc}") from exc
                delay = backoff_delay(retry_count, float(self.cfg.RETRY_BACKOFF_BASE), float(self.cfg.RETRY_BACKOFF_MAX))
                log.warning(
                    f"[步骤{step_name}] {kind} 错误（{status}），{delay:.1f}s 后第 {retry_count + 1} 次重试"
                )
                retry_count += 1
                self._sleep(delay)
                continue

            latency_ms = int((time.perf_counter() - t0) * 1000)
            text = self._extract_content(resp)
            finish_reason = self._finish_reason(resp)
            usage = self._extract_usage(resp)
            tier = self._tier()
            cost = compute_cost_cny(
                model, usage["prompt_tokens"], usage["completion_tokens"],
                usage["cache_hit_tokens"], self.cfg.PRICE_TABLE, tier,
            )

            if not text.strip():
                log.warning(f"[步骤{step_name}] 返回空 content（finish_reason={finish_reason}），按解析失败处理")
                data, unmatched, strict_ok = None, [], False
            else:
                data, unmatched, strict_ok = parse_json_loose(text)

            json_ok = strict_ok and data is not None
            self._write_cost_row(
                chapter_index=chapter_index, step_name=step_name, model=model,
                thinking=thinking, prompt=usage["prompt_tokens"],
                completion=usage["completion_tokens"], cache_hit=usage["cache_hit_tokens"],
                latency=latency_ms, retry_count=retry_count, finish_reason=finish_reason,
                json_ok=json_ok, cost=cost, cache_source="api", tier=tier,
            )
            self.budget.add_spend(chapter_index, cost)

            # 输出被截断：思考模式的推理 token 会吃掉额度，或译文比预估更长。
            # 处理方式不是重问（重问也会被截断），而是扩容后重试；最后一次关掉思考兜底。
            if finish_reason == "length" and data is None and truncation_retries < max_trunc_retries:
                truncation_retries += 1
                old_max = max_tokens
                max_tokens = min(hard_cap, max(max_tokens * 2, max_tokens + 2000))
                if truncation_retries >= 2 and thinking:
                    thinking = False
                    log.warning(
                        f"[步骤{step_name}] 连续截断，本次重试关闭思考模式以腾出输出额度"
                    )
                retry_count += 1
                log.warning(
                    f"[步骤{step_name}] 输出被截断（finish_reason=length，"
                    f"输出 {usage['completion_tokens']} tok），"
                    f"max_tokens {old_max} → {max_tokens} 后重试"
                )
                continue

            if finish_reason == "length" and data is not None:
                log.warning(f"[步骤{step_name}] 输出达到 max_tokens 上限，JSON 虽可解析但可能不完整")

            if data is not None:
                if unmatched:
                    log.warning(f"[步骤{step_name}] 宽松解析：{len(unmatched)} 行无法解析，转入人工复核")
                if json_ok:
                    log.info(
                        f"[步骤{step_name}] 成功 | 模型={model} | 思考={'on' if thinking else 'off'}"
                        f" | 输入={usage['prompt_tokens']}tok(缓存命中={usage['cache_hit_tokens']})"
                        f" | 输出={usage['completion_tokens']}tok | 重试={retry_count}"
                        f" | finish={finish_reason} | json_ok={json_ok}"
                        f" | 档位={tier} | ¥{cost:.6f} | {latency_ms}ms"
                    )
                    if use_cache:
                        self._cache_write(cache_key, data)
                else:
                    log.warning(f"[步骤{step_name}] JSON 解析降级（行文本宽松解析）")
                return LLMResult(
                    data=data, raw=text, json_parse_ok=json_ok, fallback_used=not json_ok,
                    finish_reason=finish_reason, prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    cache_hit_tokens=usage["cache_hit_tokens"], cost_cny=cost,
                    retry_count=retry_count, latency_ms=latency_ms,
                    manual_review=unmatched,
                )

            # 解析失败：先加固重问一次，再放弃
            if phase == 0:
                phase = 1
                retry_count += 1
                log.warning(f"[步骤{step_name}] JSON 解析失败，temperature=0 重申 JSON 格式后重试")
                messages_system.append({
                    "role": "user",
                    "content": render_json_reminder(schema_key),
                })
                continue
            log.error(f"[步骤{step_name}] JSON 仍无法解析，整段内容转入人工复核")
            return LLMResult(
                data={}, raw=text, json_parse_ok=False, fallback_used=True,
                finish_reason=finish_reason, prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                cache_hit_tokens=usage["cache_hit_tokens"], cost_cny=cost,
                retry_count=retry_count, latency_ms=latency_ms,
                manual_review=[text[:2000]],
            )

    # -- 底层调用 ---------------------------------------------
    def _create(self, *, model: str, messages: list[dict], temperature: float,
                max_tokens: int, thinking: bool, reasoning_effort: str | None):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            # DeepSeek 思考模式开关
            "extra_body": {"thinking": {"type": "enabled" if thinking else "disabled"}},
        }
        if thinking and reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        return self.client.chat.completions.create(**kwargs)

    @staticmethod
    def _extract_content(resp: Any) -> str:
        try:
            return resp.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            return ""

    @staticmethod
    def _finish_reason(resp: Any) -> str:
        try:
            return resp.choices[0].finish_reason or ""
        except (AttributeError, IndexError, TypeError):
            return ""

    @staticmethod
    def _extract_usage(resp: Any) -> dict[str, int]:
        usage = getattr(resp, "usage", None)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
        if not hit:
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                hit = int(getattr(details, "cached_tokens", 0) or 0)
        return {"prompt_tokens": prompt, "completion_tokens": completion, "cache_hit_tokens": hit}

    def _write_cost_row(self, *, chapter_index: int, step_name: str, model: str,
                        thinking: bool, prompt: int, completion: int, cache_hit: int,
                        latency: int, retry_count: int, finish_reason: str, json_ok: bool,
                        cost: float, cache_source: str, is_estimate: bool = False,
                        tier: str = "") -> None:
        import datetime as dt
        row = CostRow(
            timestamp=dt.datetime.now().isoformat(timespec="seconds"),
            chapter_index=chapter_index, step_name=step_name, model=model,
            thinking_enabled=thinking, prompt_tokens=prompt, completion_tokens=completion,
            cache_hit_tokens=cache_hit, latency_ms=latency, retry_count=retry_count,
            finish_reason=finish_reason, json_parse_ok=json_ok,
            prompt_cache_miss_tokens=max(0, prompt - cache_hit), cost_cny=cost,
            cache_source=cache_source, is_estimate=is_estimate,
            price_tier=tier or self._tier(),
        )
        try:
            self.cost_log.append(row)
        except OSError as exc:  # pragma: no cover
            log.warning(f"写成本日志失败：{exc}")

    # -- Mock -------------------------------------------------
    def _mock_call(self, step_name: str, chapter_index: int, model: str,
                   thinking: bool, user: str, schema_key: str) -> LLMResult:
        """不联网的确定性桩，用于流程联调与端到端测试。"""
        data = MOCK_RESPONSES.get(schema_key, lambda u: {"entries": []})(user)
        self._write_cost_row(
            chapter_index=chapter_index, step_name=step_name, model=f"{model} (mock)",
            thinking=thinking, prompt=_rough_tokens(user), completion=0, cache_hit=0,
            latency=0, retry_count=0, finish_reason="mock", json_ok=True,
            cost=0.0, cache_source="mock",
        )
        log.info(f"[步骤{step_name}] MOCK 模式：返回本地桩数据")
        return LLMResult(data=data, raw=json.dumps(data, ensure_ascii=False), cache_source="mock")


# ---------------------------------------------------------------------------
# 错误分类与工具函数
# ---------------------------------------------------------------------------
def classify_error(exc: Exception) -> tuple[str, int | None]:
    """返回 (kind, status)：kind ∈ fatal / rate / transient。"""
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    name = type(exc).__name__.lower()
    if status in (400, 401, 403, 404, 422):
        return "fatal", int(status)
    if status == 429:
        return "rate", 429
    if status is not None and 500 <= int(status) < 600:
        return "transient", int(status)
    if "timeout" in name or "connection" in name or status is None:
        return "transient", status
    return "transient", status


def backoff_delay(retry_index: int, base: float, cap: float) -> float:
    """指数退避 + 抖动，上限 cap。"""
    raw = base * (2 ** retry_index)
    jitter = random.uniform(0, base)
    return min(cap, raw + jitter)


def _rough_tokens(text: str) -> int:
    """本地快速估算（与 chunker.estimate_tokens 口径一致，避免循环导入）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
    return int((len(text) - cjk) / 4 + cjk) + 1


# ---------------------------------------------------------------------------
# Mock 数据生成（仅在 MOCK_MODE 下使用）
# ---------------------------------------------------------------------------
def _mock_nouns(user_text: str) -> dict:
    names: list[str] = []
    for m in re.finditer(r"(?<![A-Za-z])([A-Z][a-z]{2,})(?![A-Za-z])", user_text):
        word = m.group(1)
        if word not in MOCK_STOPWORDS and word not in names:
            names.append(word)
        if len(names) >= 8:
            break
    return {"entries": [{"source": n, "target": f"模拟译名{i + 1}"} for i, n in enumerate(names)]}


def _mock_translate(user_text: str) -> dict:
    body = user_text.split("<<<")[-1].rsplit(">>>", 1)[0] if "<<<" in user_text else user_text
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    return {
        "translation": "\n\n".join(f"（模拟译文第{i + 1}段）这里放模拟译文，仅用于流程联调。" for i in range(len(paras))) or "（模拟译文）",
        "notes": [],
    }


def _mock_polish(user_text: str) -> dict:
    body = user_text.split("<<<")[-1].rsplit(">>>", 1)[0] if "<<<" in user_text else user_text
    entries = []
    for m in re.finditer(r".{0,6}二人.{0,6}", body):
        snippet = m.group(0)
        entries.append({"source": snippet, "target": snippet.replace("二人", "两猫"), "context": "二人→两猫"})
        if len(entries) >= 5:
            break
    return {"entries": entries}


def _mock_titles(_user_text: str) -> dict:
    styles = ["意象象征", "谶语悬念", "场景叙事", "反讽对照", "主题提炼"]
    titles = ["风起长夜", "孤灯照影", "暗潮将至", "旧盟新裂", "殊途同归"]
    reasons = [
        "以长夜与风声起笔，笼罩全章的不安。",
        "扣住悬而未决的线索，留下伏笔。",
        "以关键场景为叙事支点，点明推进。",
        "昔日盟约与当下处境形成反差。",
        "凝练本章的核心主题。",
    ]
    return {"titles": [{"style": s, "title": t, "reason": r} for s, t, r in zip(styles, titles, reasons)]}


MOCK_RESPONSES: dict[str, Callable[[str], dict]] = {
    "nouns": _mock_nouns,
    "translate": _mock_translate,
    "polish": _mock_polish,
    "titles": _mock_titles,
}

MOCK_STOPWORDS = {
    "The", "And", "But", "Chapter", "Text", "Notes", "She", "They", "Then", "When",
    "There", "This", "That", "With", "For", "His", "Her", "Its", "Not", "You", "All",
    "One", "Two", "Was", "Were", "Have", "Had", "From", "Into", "Over", "Some", "What",
}
