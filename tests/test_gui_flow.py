"""GUI 端到端测试：真的跑一遍"主窗口 → 后台线程 → 审核弹窗 → 回到流水线"。

这一条专门盯着曾出现的崩溃：跨线程信号用 `Signal(str, dict)` 时，PySide 会尝试把
payload 转成 QVariantMap，遇到 threading.Event / 自定义对象就静默失败，
表现为"审核窗口不弹、步骤 4 报 NoneType 不可迭代"。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def test_worker_signal_passes_python_objects(qapp):
    """信号必须原样传 dict（含 threading.Event / 自定义对象）。"""
    from PySide6.QtCore import QCoreApplication

    from tf.gui.main_window import PipelineWorker

    received: list[dict] = []
    worker = PipelineWorker(cfg=None)
    worker.event.connect(lambda kind, payload: received.append((kind, payload)))
    marker = object()
    worker.event.emit("review_nouns", {"entries": [1, 2], "holder": {"x": marker}, "done": None})
    QCoreApplication.processEvents()
    assert received and received[0][0] == "review_nouns"
    payload = received[0][1]
    assert payload["entries"] == [1, 2]
    assert payload["holder"]["x"] is marker      # 没被转换/丢弃


def test_gui_run_with_review_dialogs(qapp, tmp_path, monkeypatch):
    from tf.gui.main_window import MainWindow

    work = tmp_path / "work"
    monkeypatch.setenv("WORK_DIR", str(work))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("COST_LOG_PATH", str(tmp_path / "logs" / "cost_log.csv"))
    monkeypatch.setenv("PROGRESS_PATH", str(tmp_path / "logs" / "progress.json"))

    win = MainWindow()
    win.e_source.setText(str(ROOT / "samples" / "source_sample.txt"))
    win.e_output.setText(str(tmp_path / "out" / "译文.docx"))
    win.e_nouns.setText(str(tmp_path / "data" / "noun_table.json"))
    win.e_style.setText(str(ROOT / "samples" / "style_sample.txt"))
    win.s_start.setValue(1)
    win.s_count.setValue(1)
    win.c_mock.setChecked(True)      # 不花钱
    win.c_auto.setChecked(False)     # 必须经过审核窗口
    win.c_skip.setChecked(False)

    handled = {"nouns": 0}

    def drive():
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            return
        if hasattr(dlg, "_on_approve") and hasattr(dlg, "_on_submit"):
            dlg._on_approve()        # 通过一条
            dlg._on_submit()         # F6 等价
            handled["nouns"] += 1
        elif isinstance(dlg, QDialog):
            dlg.accept()

    from PySide6.QtCore import QTimer

    timer = QTimer()
    timer.timeout.connect(drive)
    timer.start(150)

    win._on_start()
    deadline = time.time() + 90
    while time.time() < deadline:
        QApplication.processEvents()
        if win.worker is not None and not win.worker.isRunning():
            break
        time.sleep(0.05)
    timer.stop()

    assert win.worker is not None
    assert not win.worker.isRunning(), "流水线超时未结束"
    assert handled["nouns"] >= 1, "审核窗口没有被打开（跨线程信号或桥接有问题）"
    assert win.l_err.count() == 0, [win.l_err.item(i).text() for i in range(win.l_err.count())]
    assert (tmp_path / "out" / "译文.docx").exists()
    assert "完成" in win.l_state.text() or "已结束" in win.l_state.text()
    win.close()


def test_chapters_dialog_is_scrollable(qapp, tmp_path):
    from tf.doc_parser import parse_document
    from tf.gui.main_window import ChaptersDialog

    chapters = parse_document(ROOT / "samples" / "source_sample.txt")
    dlg = ChaptersDialog(chapters)
    assert dlg.list.count() == len(chapters)
    dlg.search.setText("101")
    assert dlg.list.count() == 1
    dlg.search.setText("不存在的内容")
    assert dlg.list.count() == 0
    dlg.search.clear()
    dlg.list.setCurrentRow(1)
    assert dlg.selected_index() == 2
    dlg.close()


def test_main_window_has_step_model_controls(qapp, tmp_path, monkeypatch):
    from tf.gui.main_window import AI_STEPS, MainWindow

    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("COST_LOG_PATH", str(tmp_path / "logs" / "cost_log.csv"))
    monkeypatch.setenv("PROGRESS_PATH", str(tmp_path / "logs" / "progress.json"))
    win = MainWindow()
    for step, _label in AI_STEPS:
        combo = win.model_combos[step]
        assert combo.count() >= 2
        assert combo.currentData() in (win.cfg.MODEL_FLASH, win.cfg.MODEL_PRO)
    win.model_combos[6].setCurrentIndex(win.model_combos[6].findData(win.cfg.MODEL_FLASH))
    win.thinking_boxes[6].setChecked(True)
    cfg = win._collect_cfg()
    assert cfg["MODEL_STEP6"] == win.cfg.MODEL_FLASH
    assert 6 in cfg["THINKING_STEPS"]
    win.close()
