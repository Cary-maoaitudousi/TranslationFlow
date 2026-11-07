"""配置管理（需求书 §10）。

优先级：环境变量 > 配置文件(config.json) > 代码默认值。

设计要点：
* 任何配置项都可以用同名环境变量覆盖（例如 ``LOOP_COUNT=3``）；
* 相对路径统一相对项目根目录解析，避免从不同 cwd 启动时产物散落；
* ``validate_config`` 返回"问题清单"而不是直接抛异常，方便 GUI 高亮提示。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    """运行根目录。

    * 源码运行：仓库根（``tf/`` 的上一级）；
    * PyInstaller 打包后：**exe 所在目录**，这样 config.json / data / logs / output
      都落在 exe 旁边，用户看得见、改得动。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()

# ---------------------------------------------------------------------------
# 价目表（元 / 百万 tokens），来源：DeepSeek 官方「模型 & 价格」页
#
# 峰谷分时：北京时间周一至周五 9:00-12:00、14:00-18:00 为高峰价，
# 其余时段（含周末与法定节假日全天）为空闲价（恰为高峰价的一半）。
# 节假日请填 HOLIDAYS（YYYY-MM-DD），默认空表 = 只按周末区分。
# ---------------------------------------------------------------------------
DEFAULT_PRICE_TABLE: dict[str, dict] = {
    "deepseek-flash": {
        "peak": {"input_cache_hit": 0.04, "input_cache_miss": 2.0, "output": 8.0},
        "offpeak": {"input_cache_hit": 0.02, "input_cache_miss": 1.0, "output": 4.0},
    },
    "deepseek-v4-pro": {
        "peak": {"input_cache_hit": 0.30, "input_cache_miss": 9.0, "output": 27.0},
        "offpeak": {"input_cache_hit": 0.15, "input_cache_miss": 4.5, "output": 13.5},
    },
    "_default": {
        "peak": {"input_cache_hit": 0.30, "input_cache_miss": 9.0, "output": 27.0},
        "offpeak": {"input_cache_hit": 0.15, "input_cache_miss": 4.5, "output": 13.5},
    },
}

DEFAULTS: dict[str, Any] = {
    # ---- 输入输出路径（§8.1）----
    "SOURCE_DOC_PATH": "",
    "OUTPUT_DOC_PATH": "output/译文.docx",
    "GLOBAL_NOUN_TABLE_PATH": "data/noun_table.json",
    "STYLE_SAMPLE_PATH": "data/style_sample.txt",
    "FANDOM_PROFILE_PATH": "profiles/generic.json",   # IP 档案：命名规则/称谓/文化适配
    # ---- 循环控制 ----
    "START_CHAPTER": 1,          # 从文档中第几个章头开始（1 基）
    "LOOP_COUNT": 1,             # 本次处理章数
    # ---- 解析与替换 ----
    "CHAPTER_HEADING_PATTERN": r"^\s*Chapter\s+(\d+)",
    "CHAPTER_LABEL_MODE": "doc",  # doc=用章头里的 Chapter N 数字；loop=用循环计数
    # ---- 模型与思考模式（§5.2；每一步都可单独指定）----
    "MODEL_FLASH": "deepseek-flash",
    "MODEL_PRO": "deepseek-v4-pro",
    "MODEL_STEP3": "",           # 留空 = MODEL_FLASH
    "MODEL_STEP6": "",           # 留空 = MODEL_PRO
    "MODEL_STEP7": "",           # 留空 = MODEL_POLISH / MODEL_PRO
    "MODEL_STEP10": "",          # 留空 = MODEL_FLASH
    "MODEL_POLISH": "",          # 兼容旧配置：步骤7 的默认模型
    "THINKING_STEPS": [],        # 开启思考模式的步骤号，如 [6, 7]
    "REASONING_EFFORT": "high",
    # ---- 重试（§5.4）----
    "MAX_RETRIES": 5,
    "RETRY_BACKOFF_BASE": 1.0,
    "RETRY_BACKOFF_MAX": 60.0,
    "REQUEST_TIMEOUT_SEC": 600,
    # ---- 分块与输出额度（§5.5）----
    "MAX_INPUT_TOKENS_PER_CHUNK": 6000,
    "MAX_STYLE_SAMPLE_TOKENS": 2000,
    "CHARS_PER_TOKEN_EN": 4.0,
    "TITLE_INPUT_CHAR_LIMIT": 6000,
    "OUTPUT_TOKENS_PER_INPUT": 1.7,
    "MIN_CHUNK_OUTPUT_TOKENS": 1200,
    "MAX_OUTPUT_TOKENS_CAP": 8000,
    "THINKING_TOKEN_HEADROOM": 2500,
    "MAX_TOKENS_HARD_CAP": 16000,
    "TRUNCATION_RETRIES": 2,
    # ---- 润色步骤 ----
    "MAX_POLISH_ENTRIES": 15,
    "POLISH_OUTPUT_MODE": "phrase",   # phrase=只报短语(省 token)；sentence=报整句
    "POLISH_CONTEXT_WINDOW": 14,
    # ---- 成本控制 ----
    "COST_PRIORITY": False,           # 成本优先：计费按峰谷自动分档
    "WAIT_FOR_OFFPEAK": False,        # 成本优先时是否真的等到空闲时段再开始
    "PRICING_TIER": "auto",           # auto / peak / offpeak（强制档位，便于对账）
    "PEAK_WINDOWS": ["09:00-12:00", "14:00-18:00"],
    "PEAK_WEEKDAYS": [0, 1, 2, 3, 4],  # 0=周一；周末全天空闲
    "HOLIDAYS": [],                   # 法定节假日（YYYY-MM-DD），节假日全天按空闲价
    "BUDGET_TOTAL_CNY": 0.0,          # 0 = 不限
    "BUDGET_PER_CHAPTER_CNY": 0.0,    # 0 = 不限
    "CACHE_ENABLED": True,            # 相同请求命中本地缓存，重跑零成本
    "CACHE_DIR": ".cache/deepseek",
    "PRICE_TABLE": DEFAULT_PRICE_TABLE,
    # ---- 幂等 ----
    "SKIP_COMPLETED": True,
    "CLEAN_INTERMEDIATES": True,      # 成功后删除该章中间产物（失败时保留，便于排查）
    "KEEP_CACHE": True,               # 保留请求缓存；关掉则每次重算（更贵但更"干净"）
    # ---- 审核 ----
    "AUTO_APPROVE": False,
    "TITLE_MODE": "auto",             # auto=取第一条；manual=弹窗让用户选
    # ---- 目录 ----
    "WORK_DIR": "work",                 # 仅用于"失败章节"的排查现场（成功后会清理）
    "LOG_DIR": "logs",
    "COST_LOG_PATH": "logs/cost_log.csv",   # 账单：每次调用的 token 与费用
    "PROGRESS_PATH": "logs/progress.json",  # 已完成章节记录（幂等跳过用）
    "LOG_LEVEL": "INFO",
    # ---- Word 排版（默认沿用 Word 默认样式）----
    "WORD_TITLE_TEMPLATE": "第 {label} 章  {title}",
    "WORD_FONT_NAME": "",
    "WORD_FONT_SIZE": 0,
    "WORD_LINE_SPACING": 0,
    # ---- 调试 ----
    "MOCK_MODE": False,
}

_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE = {"0", "false", "no", "off", "n", "f", ""}


class Config(dict):
    """支持 ``cfg.LOOP_COUNT`` 与 ``cfg['LOOP_COUNT']`` 两种写法。"""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover - 防御
            raise AttributeError(item) from exc

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    # -- 路径工具 -----------------------------------------------------------
    def path(self, key: str) -> Path:
        """把配置里的路径解析为绝对路径（相对项目根目录）。"""
        raw = str(self.get(key, "") or "")
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def as_dict(self) -> dict[str, Any]:
        return dict(self)


def _coerce(value: Any, default: Any) -> Any:
    """把环境变量/命令行传来的字符串按默认值类型转换。"""
    if default is None:
        return value
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in _BOOL_TRUE:
            return True
        if s in _BOOL_FALSE:
            return False
        raise ValueError(f"无法解析布尔值: {value!r}")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(float(value))
    if isinstance(default, float):
        return float(value)
    if isinstance(default, (list, dict)):
        if isinstance(value, (list, dict)):
            return value
        text = str(value).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [int(x) for x in text.replace("，", ",").split(",") if x.strip()]
    return value


def load_dotenv_file(path: Path | None = None) -> None:
    """加载 .env（若 python-dotenv 可用）。已存在的环境变量优先，不会被覆盖。"""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    target = path or (PROJECT_ROOT / ".env")
    if target.exists():
        load_dotenv(dotenv_path=str(target), override=False)


def load_config(config_path: str | Path | None = None) -> Config:
    """加载并合并配置。"""
    load_dotenv_file()
    cfg = Config()

    # 1) 代码默认值
    for key, value in DEFAULTS.items():
        cfg[key] = json.loads(json.dumps(value)) if isinstance(value, (list, dict)) else value

    # 2) 配置文件
    cfg_path = Path(config_path) if config_path else (PROJECT_ROOT / "config.json")
    if not cfg_path.is_absolute():
        cfg_path = (PROJECT_ROOT / cfg_path).resolve()
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
        for key, value in user_cfg.items():
            if key in DEFAULTS:
                cfg[key] = _coerce(value, DEFAULTS[key])
            else:
                cfg[key] = value  # 允许自定义扩展项

    # 3) 环境变量覆盖（同名）
    for key, default in DEFAULTS.items():
        env_val = os.getenv(key)
        if env_val is not None and env_val != "":
            try:
                cfg[key] = _coerce(env_val, default)
            except (ValueError, json.JSONDecodeError):
                pass  # 非法值忽略，保留上层取值

    cfg["_config_path"] = str(cfg_path)
    cfg["_project_root"] = str(PROJECT_ROOT)
    cfg["DEEPSEEK_BASE_URL"] = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    cfg["_api_key_present"] = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    return cfg


def step_model(cfg: Config, step: int) -> str:
    """解析某一步实际使用的模型（空值回退到默认分级）。"""
    if step == 3:
        return str(cfg.get("MODEL_STEP3") or cfg.MODEL_FLASH)
    if step == 6:
        return str(cfg.get("MODEL_STEP6") or cfg.MODEL_PRO)
    if step == 7:
        return str(cfg.get("MODEL_STEP7") or cfg.get("MODEL_POLISH") or cfg.MODEL_PRO)
    if step == 10:
        return str(cfg.get("MODEL_STEP10") or cfg.MODEL_FLASH)
    return str(cfg.MODEL_PRO)


def step_thinking(cfg: Config, step: int) -> bool:
    return step in (cfg.get("THINKING_STEPS") or [])


def validate_config(cfg: Config) -> list[str]:
    """启动前校验（§10）。返回问题列表，空列表表示通过。"""
    problems: list[str] = []

    if not str(cfg.SOURCE_DOC_PATH).strip():
        problems.append("SOURCE_DOC_PATH 未设置：请指定源文档路径")
    else:
        src = cfg.path("SOURCE_DOC_PATH")
        if not src.exists():
            problems.append(f"源文档不存在：{src}")
        elif src.suffix.lower() not in {".txt", ".docx", ".md"}:
            problems.append(f"源文档格式不支持（仅 .txt/.docx/.md）：{src.suffix}")

    if not str(cfg.OUTPUT_DOC_PATH).strip():
        problems.append("OUTPUT_DOC_PATH 未设置：请指定输出 Word 路径")

    if not cfg.MOCK_MODE and not cfg.get("_api_key_present"):
        problems.append("DEEPSEEK_API_KEY 未设置：请在主控界面填写，或复制 .env.example 为 .env")

    if int(cfg.START_CHAPTER) < 1:
        problems.append("START_CHAPTER 必须 >= 1")
    if int(cfg.LOOP_COUNT) < 1:
        problems.append("LOOP_COUNT 必须 >= 1")
    if int(cfg.MAX_INPUT_TOKENS_PER_CHUNK) < 200:
        problems.append("MAX_INPUT_TOKENS_PER_CHUNK 过小（建议 >= 1000）")
    if int(cfg.MAX_RETRIES) < 1:
        problems.append("MAX_RETRIES 必须 >= 1")
    if cfg.TITLE_MODE not in ("auto", "manual"):
        problems.append("TITLE_MODE 只能是 auto 或 manual")
    if cfg.CHAPTER_LABEL_MODE not in ("doc", "loop"):
        problems.append("CHAPTER_LABEL_MODE 只能是 doc 或 loop")
    if str(cfg.PRICING_TIER) not in ("auto", "peak", "offpeak"):
        problems.append("PRICING_TIER 只能是 auto / peak / offpeak")

    try:
        import re
        re.compile(str(cfg.CHAPTER_HEADING_PATTERN))
    except re.error as exc:
        problems.append(f"CHAPTER_HEADING_PATTERN 不是合法正则：{exc}")

    return problems


def soft_warnings(cfg: Config) -> list[str]:
    """不阻断启动的提醒项（GUI 里以黄色显示）。"""
    warns: list[str] = []
    style_path = cfg.path("STYLE_SAMPLE_PATH")
    if not style_path.exists():
        warns.append(f"文风样本缺失，翻译将退化为零样本：{style_path}")
    if not cfg.path("GLOBAL_NOUN_TABLE_PATH").exists():
        warns.append("全局专有名词表不存在，将按空表启动")
    if float(cfg.BUDGET_TOTAL_CNY) <= 0:
        warns.append("未设置 BUDGET_TOTAL_CNY，本次运行没有总预算闸门")
    if cfg.MOCK_MODE:
        warns.append("MOCK_MODE 已开启：不调用真实 API，仅用于流程联调")
    if not cfg.get("HOLIDAYS"):
        warns.append("HOLIDAYS 为空：法定节假日会按工作日作息计价，可能高估成本")
    return warns


def save_config(cfg: Config, path: str | Path | None = None) -> Path:
    """保存配置（只写业务参数，不写密钥、不写内部字段）。"""
    target = Path(path) if path else Path(cfg.get("_config_path") or (PROJECT_ROOT / "config.json"))
    if not target.is_absolute():
        target = (PROJECT_ROOT / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        k: v for k, v in cfg.items()
        if not k.startswith("_") and k not in ("DEEPSEEK_BASE_URL",)
    }
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return target


def save_secrets(api_key: str, base_url: str = "", path: str | Path | None = None) -> Path:
    """把密钥写入 .env（GUI 用）。文件已在 .gitignore 中。"""
    target = Path(path) if path else (PROJECT_ROOT / ".env")
    key = api_key.strip()
    url = base_url.strip() or "https://api.deepseek.com"
    lines = [
        "# 由主控界面写入；本文件已在 .gitignore 中，请勿提交",
        f"DEEPSEEK_API_KEY={key}",
        f"DEEPSEEK_BASE_URL={url}",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["DEEPSEEK_API_KEY"] = key
    os.environ["DEEPSEEK_BASE_URL"] = url
    return target
