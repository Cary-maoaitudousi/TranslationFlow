"""GUI 冒烟测试：offscreen 下能建出主窗口与配置控件。"""

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_builds(qapp, tmp_path, monkeypatch):
    from tf.gui.main_window import STEP_NAMES, MainWindow

    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("COST_LOG_PATH", str(tmp_path / "logs" / "cost_log.csv"))
    monkeypatch.setenv("PROGRESS_PATH", str(tmp_path / "logs" / "progress.json"))
    win = MainWindow()
    assert "流水线" in win.windowTitle()
    assert win.l_steps.count() == len(STEP_NAMES) == 11
    assert win.s_count.value() >= 1
    win.close()


def test_reviewer_bridge_times_out():
    from tf.gui.reviewer_bridge import GuiThreadReviewer
    from tf.review import ReviewCancelled

    reviewer = GuiThreadReviewer(lambda _kind, _payload: None, timeout_s=0.01)
    with pytest.raises(ReviewCancelled):
        reviewer.review_nouns([], None)


def test_reviewer_bridge_returns_value():
    from tf.gui.reviewer_bridge import GuiThreadReviewer

    def fake_emit(kind, payload):
        payload["holder"]["value"] = [{"source": "X", "target": "某"}]
        payload["done"].set()

    reviewer = GuiThreadReviewer(fake_emit, timeout_s=1)
    assert reviewer.review_nouns([], None) == [{"source": "X", "target": "某"}]
