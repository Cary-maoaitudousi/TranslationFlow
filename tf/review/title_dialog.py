"""步骤 10 的章名选择窗口（TITLE_MODE=manual 时弹出）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class TitleChooserDialog(QDialog):
    def __init__(self, titles: list[dict[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择章名（步骤 10）")
        self.resize(900, 380)
        self.titles = titles
        self.selected: dict[str, str] | None = None

        self.table = QTableWidget(len(titles), 3, self)
        self.table.setHorizontalHeaderLabels(["风格", "章节名", "理由"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, item in enumerate(titles):
            self.table.setItem(row, 0, QTableWidgetItem(item.get("style", "")))
            title_item = QTableWidgetItem(item.get("title", ""))
            reason_item = QTableWidgetItem(item.get("reason", ""))
            reason_item.setToolTip(item.get("reason", ""))
            title_item.setToolTip(item.get("title", ""))
            self.table.setItem(row, 1, title_item)
            self.table.setItem(row, 2, reason_item)
        if titles:
            self.table.selectRow(0)

        hint = QLabel("双击确认选择 · 回车确认 · Esc 取消（默认取第一条）")
        hint.setStyleSheet("color:#555;")
        btn_ok = QPushButton("使用选中章名")
        btn_cancel = QPushButton("取消（用第一条）")
        btn_ok.clicked.connect(self._accept_current)
        btn_cancel.clicked.connect(self.reject)
        self.table.doubleClicked.connect(lambda *_: self._accept_current())

        btns = QHBoxLayout()
        btns.addWidget(hint, 1)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(btns)

    def _accept_current(self) -> None:
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount():
            row = 0
        if 0 <= row < len(self.titles):
            self.selected = self.titles[row]
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F6):
            self._accept_current()
            return
        super().keyPressEvent(event)
