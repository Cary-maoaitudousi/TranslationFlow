"""测试公共夹具。

约定：
* 所有测试都在临时目录里跑，不污染项目的 work/ 与 output/；
* MOCK_MODE 默认开启，端到端测试零成本、不联网；
* Qt 测试使用 offscreen 平台，无需真实显示器。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tf.config import DEFAULTS, Config  # noqa: E402


def make_config(tmp_path: Path, **overrides) -> Config:
    cfg = Config()
    for key, value in DEFAULTS.items():
        cfg[key] = json.loads(json.dumps(value)) if isinstance(value, (list, dict)) else value
    cfg["_project_root"] = str(tmp_path)
    cfg["_config_path"] = str(tmp_path / "config.json")
    cfg["WORK_DIR"] = str(tmp_path / "work")
    cfg["LOG_DIR"] = str(tmp_path / "logs")
    cfg["COST_LOG_PATH"] = str(tmp_path / "logs" / "cost_log.csv")
    cfg["PROGRESS_PATH"] = str(tmp_path / "logs" / "progress.json")
    cfg["CACHE_DIR"] = str(tmp_path / "cache")
    cfg["SOURCE_DOC_PATH"] = str(ROOT / "samples" / "source_sample.txt")
    cfg["STYLE_SAMPLE_PATH"] = str(ROOT / "samples" / "style_sample.txt")
    cfg["FANDOM_PROFILE_PATH"] = str(ROOT / "profiles" / "warriors.json")
    cfg["GLOBAL_NOUN_TABLE_PATH"] = str(tmp_path / "data" / "noun_table.json")
    cfg["OUTPUT_DOC_PATH"] = str(tmp_path / "output" / "译文.docx")
    cfg["MOCK_MODE"] = True
    cfg["AUTO_APPROVE"] = True
    cfg["SKIP_COMPLETED"] = True
    cfg["CLEAN_INTERMEDIATES"] = False   # 测试要检查中间产物，默认不清理
    cfg["MAX_INPUT_TOKENS_PER_CHUNK"] = 3000
    cfg["_api_key_present"] = True
    cfg["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
    cfg.update(overrides)
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _isolate_logs(tmp_path_factory):
    """把所有模块日志重定向到临时目录，避免测试往项目 logs/ 里写垃圾。"""
    from tf.logger import setup_logging

    setup_logging(tmp_path_factory.mktemp("logs"), "CRITICAL", console=False)
    yield


@pytest.fixture()
def cfg_factory(tmp_path):
    def _factory(**overrides) -> Config:
        return make_config(tmp_path, **overrides)

    return _factory


@pytest.fixture()
def noun_table_file(tmp_path):
    path = tmp_path / "data" / "noun_table.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "entries": [
                {"source": "Firestar", "target": "火星"},
                {"source": "Sandstorm", "target": "沙风"},
            ]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
