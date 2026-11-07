"""验收标准自检（需求书 §12）。

这些测试直接对着"禁止事项/硬性要求"做静态检查，防止后续改动越界。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tf.config import DEFAULTS, load_config, save_config, soft_warnings, validate_config
from tf import prompts
from tf.fandom import FandomProfile, load_profile
from tf.prompts import JSON_SCHEMAS

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


def _all_system_prompts(profile) -> dict[str, str]:
    return {
        "nouns": prompts.nouns_system(profile),
        "translate": prompts.translate_system(profile, "样本正文", False),
        "polish_phrase": prompts.polish_system(profile),
        "polish_sentence": prompts.polish_sentence_system(profile),
        "titles": prompts.titles_system(profile),
    }


@pytest.mark.parametrize("profile_path", ["profiles/warriors.json", "profiles/generic.json"])
def test_prompts_require_json(profile_path):
    """每个 system prompt 都必须含 json 字样、JSON 样例与"只输出 JSON"。"""
    profile = load_profile(ROOT / profile_path)
    for name, system_prompt in _all_system_prompts(profile).items():
        lowered = system_prompt.lower()
        assert "json" in lowered, name
        assert "{" in system_prompt, name
        assert "只输出" in system_prompt, name


def test_prompts_follow_profile_not_hardcoded_fandom():
    """核心提示词不能写死某个作品：猫武士档案有猫族规则，通用档案没有。"""
    warriors = _all_system_prompts(load_profile(ROOT / "profiles" / "warriors.json"))
    generic = _all_system_prompts(load_profile(ROOT / "profiles" / "generic.json"))

    joined_w = " ".join(warriors.values())
    joined_g = " ".join(generic.values())

    # 猫武士档案：命名规则与文化示例都出现了
    assert "xxxxkit" in joined_w and "XX崽" in joined_w
    assert "xxxxpaw" in joined_w and "XX爪" in joined_w
    assert "xxxxstar" in joined_w and "XX星" in joined_w
    assert "Thistleclaw" in warriors["nouns"] and "蓟掌" in warriors["nouns"]
    assert "量词" in joined_w
    assert "猫" in joined_w

    # 通用档案：不出现作品专属设定，改用档案里的通用规则
    assert "xxxxkit" not in joined_g
    assert "蓟掌" not in joined_g
    assert "猫" not in joined_g
    assert "角色" in joined_g
    assert "四字" in generic["titles"]


def test_profile_missing_falls_back_to_generic(tmp_path):
    profile = load_profile(tmp_path / "nope.json")
    assert profile.is_fallback is True
    text = prompts.nouns_system(profile)
    assert "通用作品" in text and "人名" in text


def test_profile_fields_reach_prompts():
    profile = FandomProfile(
        name="测试作品",
        subject_singular="机兵",
        naming_rules=["头衔放在名字后面"],
        culture_rules=["把“人”改成“机兵”"],
        title_rule="四字章节名",
        translation_brief="专有名词使用给定中文",
    )
    prompts_map = _all_system_prompts(profile)
    assert "测试作品" in prompts_map["nouns"]
    assert "头衔放在名字后面" in prompts_map["nouns"]
    assert "把“人”改成“机兵”" in prompts_map["polish_phrase"]
    assert "专有名词使用给定中文" in prompts_map["translate"]


def test_json_schema_keys_present():
    assert set(JSON_SCHEMAS) >= {"nouns", "translate", "polish", "titles"}
    assert '"entries"' in JSON_SCHEMAS["nouns"]
    assert '"translation"' in JSON_SCHEMAS["translate"]
    assert '"context"' in JSON_SCHEMAS["polish"]
    assert '"titles"' in JSON_SCHEMAS["titles"]
    assert '"phrase"' in JSON_SCHEMAS["polish_phrase"]


def test_prompts_keep_business_constraints():
    """提示词里那些与作品无关的硬约束不能丢。"""
    profile = load_profile(ROOT / "profiles" / "warriors.json")
    p = _all_system_prompts(profile)

    assert "已经是中文" in p["nouns"]
    assert "同一" in p["nouns"] or "前后一致" in p["nouns"]

    assert "逐字符一致" in p["polish_phrase"]
    assert "逐字符一致" in p["polish_sentence"]

    assert "四字" in p["titles"] and "5" in p["titles"]

    assert "文风参考样本" in p["translate"]
    assert "不引入外部无关信息" in p["translate"]
    assert "保留" in p["translate"]


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
