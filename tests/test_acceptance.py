"""验收标准自检（需求书 §12）。

这些测试直接对着"禁止事项/硬性要求"做静态检查，防止后续改动越界。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tf.config import DEFAULTS, load_config, save_config, soft_warnings, validate_config
from tf.prompts import JSON_SCHEMAS, NOUNS_SYSTEM, POLISH_SYSTEM, TITLES_SYSTEM, TRANSLATE_SYSTEM

ROOT = Path(__file__).resolve().parent.parent
def _is_project_file(path: Path) -> bool:
    parts = path.parts
    if any(part.startswith(".venv") or part == "site-packages" or part.startswith(".") for part in parts[:-1]):
        return False
    return path.name not in ("conftest.py", "test_acceptance.py")


SOURCE_FILES = sorted(p for p in ROOT.rglob("*.py") if _is_project_file(p))


def read_all_sources() -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8", errors="replace") for p in SOURCE_FILES}


def test_no_browser_automation_dependency():
    """§12.1：代码中不得出现浏览器自动化的 import。"""
    banned = ("playwright", "selenium", "pyppeteer", "webai2api", "undetected_chromedriver")
    for path, text in read_all_sources().items():
        lowered = text.lower()
        for word in banned:
            assert word not in lowered, f"{path} 含被禁止的依赖：{word}"


def test_openai_instantiated_only_in_client():
    """§12.2：只允许 deepseek_client 里实例化 OpenAI。"""
    hits = []
    for path, text in read_all_sources().items():
        if re.search(r"OpenAI\(", text):
            hits.append(path.relative_to(ROOT).as_posix())
    assert hits == ["tf/deepseek_client.py"], hits


def test_env_file_gitignored():
    """§12.10：.env 必须在 .gitignore 中。"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore.split()


def test_api_key_read_from_env_only():
    text = (ROOT / "tf" / "deepseek_client.py").read_text(encoding="utf-8")
    assert 'os.getenv("DEEPSEEK_API_KEY"' in text
    # 配置里不存密钥
    assert "DEEPSEEK_API_KEY" not in DEFAULTS


@pytest.mark.parametrize(
    "system_prompt",
    [NOUNS_SYSTEM, TRANSLATE_SYSTEM, POLISH_SYSTEM, TITLES_SYSTEM],
)
def test_prompts_require_json(system_prompt):
    """§5.3：system prompt 必须含 json 字样、schema 样例与"只输出 JSON"。"""
    lowered = system_prompt.lower()
    assert "json" in lowered
    assert "{" in system_prompt
    assert "只输出" in system_prompt


def test_json_schema_keys_present():
    assert set(JSON_SCHEMAS) >= {"nouns", "translate", "polish", "titles"}
    assert '"entries"' in JSON_SCHEMAS["nouns"]
    assert '"translation"' in JSON_SCHEMAS["translate"]
    assert '"context"' in JSON_SCHEMAS["polish"]
    assert '"titles"' in JSON_SCHEMAS["titles"]
    assert '"phrase"' in JSON_SCHEMAS["polish_phrase"]


def test_prompts_keep_business_constraints():
    """附录 A 的关键业务约束不得在 JSON 化过程中丢失。"""
    assert "xxxxkit" in NOUNS_SYSTEM and "XX崽" in NOUNS_SYSTEM
    assert "xxxxpaw" in NOUNS_SYSTEM and "XX爪" in NOUNS_SYSTEM
    assert "xxxxstar" in NOUNS_SYSTEM and "XX星" in NOUNS_SYSTEM
    assert "Thistleclaw" in NOUNS_SYSTEM and "蓟掌" in NOUNS_SYSTEM
    assert "已经是中文" in NOUNS_SYSTEM

    assert "人体肢体" in POLISH_SYSTEM
    assert "量词" in POLISH_SYSTEM
    assert "逐字符一致" in POLISH_SYSTEM

    assert "四字" in TITLES_SYSTEM and "5" in TITLES_SYSTEM

    assert "文风参考样本" in TRANSLATE_SYSTEM
    assert "不引入外部无关信息" in TRANSLATE_SYSTEM
    assert "不要改动" in TRANSLATE_SYSTEM


def test_validate_config_reports_missing_source(tmp_path):
    cfg = load_config(None)
    cfg["SOURCE_DOC_PATH"] = str(tmp_path / "missing.txt")
    problems = validate_config(cfg)
    assert any("源文档不存在" in p for p in problems)


def test_validate_config_ok_for_sample(tmp_path):
    cfg = load_config(None)
    cfg["SOURCE_DOC_PATH"] = str(ROOT / "samples" / "source_sample.txt")
    cfg["MOCK_MODE"] = True
    problems = validate_config(cfg)
    assert problems == []


def test_soft_warnings_mention_missing_style(tmp_path):
    cfg = load_config(None)
    cfg["STYLE_SAMPLE_PATH"] = str(tmp_path / "none.txt")
    cfg["GLOBAL_NOUN_TABLE_PATH"] = str(tmp_path / "none.json")
    warns = soft_warnings(cfg)
    assert any("文风样本" in w for w in warns)


def test_config_roundtrip(tmp_path):
    cfg = load_config(None)
    cfg["LOOP_COUNT"] = 3
    cfg["MOCK_MODE"] = True
    target = save_config(cfg, tmp_path / "config.json")
    assert target.exists()
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["LOOP_COUNT"] == 3
    assert "DEEPSEEK_API_KEY" not in json.dumps(data)


def test_config_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOP_COUNT", "7")
    monkeypatch.setenv("SKIP_COMPLETED", "false")
    cfg = load_config(None)
    assert cfg.LOOP_COUNT == 7
    assert cfg.SKIP_COMPLETED is False


def test_all_ai_calls_route_through_client():
    """§12.2：其它模块不得直接 import openai。"""
    for path, text in read_all_sources().items():
        if path.name in ("deepseek_client.py", "test_client.py"):
            continue
        assert "from openai" not in text, path
        assert "import openai" not in text, path


def test_appendix_b_filenames_used_in_pipeline():
    text = (ROOT / "tf" / "pipeline.py").read_text(encoding="utf-8")
    for name in (
        "raw.txt", "meta.json", "replaced.txt", "new_nouns.json", "approved_nouns.json",
        "final_mixed.txt", "translated.txt", "polish.json", "polish_approved.json",
        "final_chinese.txt", "titles.json", "summary.json",
    ):
        assert name in text, name
