"""CLI 冒烟测试：子命令能跑、提示词按档案渲染、不产生费用。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(tmp_path, monkeypatch, argv, capsys):
    from cli import main

    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("COST_LOG_PATH", str(tmp_path / "logs" / "cost_log.csv"))
    monkeypatch.setenv("PROGRESS_PATH", str(tmp_path / "logs" / "progress.json"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_prompts_uses_profile(tmp_path, monkeypatch, capsys):
    rc, out = _run(
        tmp_path, monkeypatch,
        ["prompts", "-c", str(ROOT / "config.example.json"), "--name", "nouns"], capsys,
    )
    assert rc == 0
    assert "IP 档案" in out
    assert "人名" in out            # 通用档案的 noun_scope
    assert "json" in out.lower()


def test_prompts_warriors_profile_contains_cat_rules(tmp_path, monkeypatch, capsys):
    rc, out = _run(
        tmp_path, monkeypatch,
        ["prompts", "-c", str(ROOT / "config.json"), "--name", "nouns"], capsys,
    )
    assert rc == 0
    assert "xxxxkit" in out and "XX崽" in out


def test_chapters_lists_document(tmp_path, monkeypatch, capsys):
    import json

    cfg = tmp_path / "config.json"
    data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    data["SOURCE_DOC_PATH"] = str(ROOT / "samples" / "source_sample.txt")
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc, out = _run(tmp_path, monkeypatch, ["chapters", "-c", str(cfg)], capsys)
    assert rc == 0
    assert "共 2 章" in out


def test_pricing_and_holidays_run(tmp_path, monkeypatch, capsys):
    for cmd in ("pricing", "holidays", "report", "cache"):
        rc, out = _run(tmp_path, monkeypatch, [cmd, "-c", str(ROOT / "config.example.json")], capsys)
        assert rc == 0, cmd
        assert out.strip(), cmd
