"""审核窗口测试（验收标准 §12.12）。

offscreen 平台下用 QTest 模拟按键，验证 Space / Enter / F6 行为。
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication

from tf.noun_table import NounTable


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize("case", ["nouns"])
def test_space_approves_and_moves_next(qapp, tmp_path, case):
    from tf.review.noun_review_dialog import NounReviewDialog

    global_table = NounTable(tmp_path / "t.json", [{"source": "Firestar", "target": "火星"}])
    entries = [
        {"source": "Thistleclaw", "target": "蓟掌"},
        {"source": "Frostfur", "target": "霜毛"},
    ]
    dlg = NounReviewDialog(entries, global_table)
    assert dlg.table.currentRow() == 0

    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    assert dlg.table.currentRow() == 1
    approved = dlg.approved_entries()
    assert approved == [{"source": "Thistleclaw", "target": "蓟掌"}]

    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    assert len(dlg.approved_entries()) == 2


def test_space_toggles_off(qapp, tmp_path):
    from tf.review.noun_review_dialog import NounReviewDialog

    dlg = NounReviewDialog(
        [{"source": "Thistleclaw", "target": "蓟掌"}], NounTable(tmp_path / "t.json", [])
    )
    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    assert len(dlg.approved_entries()) == 1
    dlg.table.setCurrentCell(0, 1)
    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    assert dlg.approved_entries() == []


def test_enter_starts_editing_then_saves(qapp, tmp_path):
    from tf.review.noun_review_dialog import NounReviewDialog

    dlg = NounReviewDialog(
        [{"source": "Thistleclaw", "target": "蓟掌"}], NounTable(tmp_path / "t.json", [])
    )
    QTest.keyClick(dlg.table, Qt.Key.Key_Return)
    assert dlg.table.state() == QAbstractItemView.State.EditingState

    # 编辑并提交
    dlg.table.item(0, 1).setText("蓟爪")
    QTest.keyClick(dlg.table, Qt.Key.Key_Return)
    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    approved = dlg.approved_entries()
    assert approved and approved[0]["target"] in ("蓟爪", "蓟掌")


def test_arrow_keys_navigate(qapp, tmp_path):
    from tf.review.noun_review_dialog import NounReviewDialog

    dlg = NounReviewDialog(
        [{"source": "Aaa", "target": "甲"}, {"source": "Bbb", "target": "乙"}],
        NounTable(tmp_path / "t.json", []),
    )
    dlg.table.setCurrentCell(0, 1)
    QTest.keyClick(dlg.table, Qt.Key.Key_Right)
    assert dlg.table.currentRow() == 1
    QTest.keyClick(dlg.table, Qt.Key.Key_Left)
    assert dlg.table.currentRow() == 0


def test_f6_submits_independently_of_enter(qapp, tmp_path):
    from tf.review.noun_review_dialog import NounReviewDialog

    dlg = NounReviewDialog(
        [{"source": "Thistleclaw", "target": "蓟掌"}], NounTable(tmp_path / "t.json", [])
    )
    dlg.show()
    QTest.keyClick(dlg.table, Qt.Key.Key_F6)
    assert dlg.submitted is True
    assert dlg.result() == 1
    dlg.close()


def test_skip_all_button_flag(qapp, tmp_path):
    from tf.review.noun_review_dialog import NounReviewDialog

    dlg = NounReviewDialog(
        [{"source": "Thistleclaw", "target": "蓟掌"}], NounTable(tmp_path / "t.json", [])
    )
    dlg.btn_skip_all.click()
    assert dlg.skip_all is True
    assert dlg.approved_entries() == []


def test_preview_shows_merged_table(qapp, tmp_path):
    from tf.review.noun_review_dialog import NounReviewDialog

    global_table = NounTable(tmp_path / "t.json", [{"source": "Firestar", "target": "火星"}])
    dlg = NounReviewDialog([{"source": "Thistleclaw", "target": "蓟掌"}], global_table)
    assert "Firestar->火星" in dlg.preview.toPlainText()
    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    text = dlg.preview.toPlainText()
    assert "Thistleclaw->蓟掌" in text


def test_polish_dialog_flags_missing_source_and_previews(qapp):
    from tf.review.polish_review_dialog import PolishReviewDialog

    translated = "两人陷入了沉默。\n\n另一段落。"
    entries = [
        {"source": "两人陷入了沉默。", "target": "两猫陷入了沉默。", "context": "两人→两猫"},
        {"source": "不存在的句子。", "target": "替换", "context": "x"},
    ]
    dlg = PolishReviewDialog(entries, translated)
    assert dlg.table.rowCount() == 2
    QTest.keyClick(dlg.table, Qt.Key.Key_Space)
    assert dlg.approved_entries() == [
        {"source": "两人陷入了沉默。", "target": "两猫陷入了沉默。", "context": "两人→两猫"}
    ]
    assert "两猫陷入了沉默。" in dlg.preview.toPlainText()


def test_title_dialog_selection(qapp):
    from tf.review.title_dialog import TitleChooserDialog

    titles = [
        {"style": "意象象征", "title": "星池寒镜", "reason": "a"},
        {"style": "谶语悬念", "title": "无头蛇谶", "reason": "b"},
    ]
    dlg = TitleChooserDialog(titles)
    dlg.table.selectRow(1)
    dlg._accept_current()
    assert dlg.selected["title"] == "无头蛇谶"


def test_auto_reviewer_skips_missing_entries():
    from tf.review import AutoReviewer

    reviewer = AutoReviewer()
    translated = "两人陷入了沉默。"
    approved = reviewer.review_polish(
        [{"source": "两人陷入了沉默。", "target": "两猫陷入了沉默。"},
         {"source": "不存在。", "target": "x"}],
        translated,
    )
    assert len(approved) == 1
    assert reviewer.stats["polish_skipped"] == 1
