"""审核窗口的公共部件（PySide6）。

快捷键约定（需求书 §7.1 / §7.2，两个窗口完全一致）：

===========  ==========================================
按键          行为
===========  ==========================================
Space        通过当前条并跳到下一条
Enter        进入编辑；再次 Enter 保存并跳到下一条
← / →        上一条 / 下一条
F6           最终确认提交（独立于 Enter，防惯性误操作）
===========  ==========================================
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

STATUS_PENDING = "待审核"
STATUS_APPROVE = "通过"
STATUS_MODIFIED = "修改后通过"
STATUS_SKIP = "跳过"
STATUS_MISSING = "待人工确认"

COLOR_APPROVE = QColor("#43a047")   # 深浅色主题下都清晰
COLOR_SKIP = QColor("#9e9e9e")
COLOR_WARN = QColor("#e08a1e")


class ReviewTable(QTableWidget):
    """带审核快捷键的表格。"""

    approveAndAdvance = Signal()
    submitRequested = Signal()
    skipAllRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, editable_col: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.editable_col = editable_col
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    # -- 快捷键 ------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.approveAndAdvance.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.state() == QAbstractItemView.State.EditingState:
                super().keyPressEvent(event)     # 提交编辑
                self.next_row()                  # 保存并下一条
                return
            row = self.currentRow()
            item = self.item(row, self.editable_col)
            if item is not None:
                self.edit(self.indexFromItem(item))
            return
        if key == Qt.Key.Key_Left:
            self.prev_row()
            return
        if key == Qt.Key.Key_Right:
            self.next_row()
            return
        if key == Qt.Key.Key_F6:
            self.submitRequested.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.cancelRequested.emit()
            return
        super().keyPressEvent(event)

    # -- 导航 --------------------------------------------------------------
    def next_row(self) -> None:
        row = self.currentRow() + 1
        if row < self.rowCount():
            self.setCurrentCell(row, max(self.editable_col, 0))

    def prev_row(self) -> None:
        row = self.currentRow() - 1
        if row >= 0:
            self.setCurrentCell(row, max(self.editable_col, 0))


class ReviewDialogBase(QDialog):
    """两个审核窗口的共同骨架：上表格 + 下预览 + 底部按钮。"""

    def __init__(self, title: str, editable_col: int, headers: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 720)

        self.table = ReviewTable(editable_col, self)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(180)
        # 固定浅底深字：深色主题下也不至于白底白字
        self.preview.setStyleSheet(
            "QPlainTextEdit { background:#ffffff; color:#1c1c1c; border:1px solid #bbb; }"
        )

        self.hint = QLabel(
            "Space 通过并下一条 · Enter 编辑/保存并下一条 · ←/→ 切换 · F6 确认提交 · Esc 取消"
        )
        self.hint.setStyleSheet("color:#555;")

        self.btn_submit = QPushButton("确认提交 (F6)")
        self.btn_skip_all = QPushButton("跳过本章新增")
        self.btn_cancel = QPushButton("取消")

        btns = QHBoxLayout()
        btns.addWidget(self.hint, 1)
        btns.addWidget(self.btn_skip_all)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_submit)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 3)
        layout.addWidget(QLabel("预览"))
        layout.addWidget(self.preview, 2)
        layout.addLayout(btns)

        self.table.approveAndAdvance.connect(self._on_approve)
        self.table.submitRequested.connect(self._on_submit)
        self.table.skipAllRequested.connect(self._on_skip_all)
        self.table.cancelRequested.connect(self.reject)
        self.table.currentCellChanged.connect(lambda *_: self._refresh_preview())
        self.table.itemChanged.connect(lambda *_: self._refresh_preview(rebuild=True))

        self.btn_submit.clicked.connect(self._on_submit)
        self.btn_skip_all.clicked.connect(self._on_skip_all)
        self.btn_cancel.clicked.connect(self.reject)

        self._submitted = False
        self._skip_all = False

    # -- 子类实现 ----------------------------------------------------------
    def _on_approve(self) -> None:  # pragma: no cover - 子类覆盖
        raise NotImplementedError

    def _refresh_preview(self, rebuild: bool = False) -> None:  # pragma: no cover
        raise NotImplementedError

    def _on_submit(self) -> None:
        self._submitted = True
        self.accept()

    def _on_skip_all(self) -> None:
        self._skip_all = True
        self.reject()

    # -- 辅助 --------------------------------------------------------------
    @staticmethod
    def _item(text: str, color: QColor | None = None, editable: bool = False) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        if color is not None:
            it.setForeground(QBrush(color))
        if not editable:
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    def set_status(self, row: int, status: str, color: QColor | None = None, status_col: int | None = None) -> None:
        col = status_col if status_col is not None else self.table.columnCount() - 1
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, col, item)
        item.setText(status)
        if color is not None:
            item.setForeground(QBrush(color))

    @property
    def submitted(self) -> bool:
        return self._submitted

    @property
    def skip_all(self) -> bool:
        return self._skip_all
