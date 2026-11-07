"""步骤 4：专有名词审核窗口（需求书 §7.1）。

上表：原文 | AI 推荐译名 | 状态
下框：全局表 + 本次通过条目 合并后的完整替换表（可滚动通览）
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from ..noun_table import NounTable
from .common import (
    COLOR_APPROVE, COLOR_SKIP, COLOR_WARN, STATUS_APPROVE, STATUS_MODIFIED,
    STATUS_PENDING, STATUS_SKIP, ReviewDialogBase,
)


class NounReviewDialog(ReviewDialogBase):
    def __init__(self, entries: list[dict], global_table: NounTable, parent=None):
        super().__init__(
            "专有名词审核（步骤 4）", 1, ["原文", "AI 推荐译名", "状态"], parent
        )
        self.global_table = global_table
        self.entries = [dict(e) for e in entries]
        self._approved = [False] * len(self.entries)
        self._modified = [False] * len(self.entries)

        self.table.blockSignals(True)
        self.table.setRowCount(len(self.entries))
        for i, entry in enumerate(self.entries):
            self.table.setItem(i, 0, self._item(entry["source"]))
            self.table.setItem(i, 1, self._item(entry["target"], editable=True))
            self.table.setItem(i, 2, self._item(STATUS_PENDING, COLOR_WARN))
        self.table.blockSignals(False)

        self.table.itemChanged.connect(self._on_item_changed)
        if self.entries:
            self.table.setCurrentCell(0, 1)
        self._update_footer()
        self._refresh_preview(rebuild=True)

    # -- 交互 --------------------------------------------------------------
    def _on_item_changed(self, item) -> None:
        if item.column() != 1:
            return
        row = item.row()
        self._modified[row] = True
        if self._approved[row]:
            self.set_status(row, STATUS_MODIFIED, COLOR_APPROVE)
        else:
            self.set_status(row, STATUS_PENDING, COLOR_WARN)
        self._update_footer()

    def _on_approve(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        if self._approved[row]:
            self._approved[row] = False
            self.set_status(row, STATUS_SKIP, COLOR_SKIP)
        else:
            target_item = self.table.item(row, 1)
            if target_item is None or not target_item.text().strip():
                self.preview.appendPlainText("\n⚠ 译名为空，已忽略该条的通过操作")
                self.table.next_row()
                return
            self._approved[row] = True
            self.set_status(
                row,
                STATUS_MODIFIED if self._modified[row] else STATUS_APPROVE,
                COLOR_APPROVE,
            )
        self._update_footer()
        self.table.next_row()

    def _update_footer(self) -> None:
        approved = sum(1 for a in self._approved if a)
        self.btn_submit.setText(
            f"确认提交 (F6) · 通过 {approved}/{len(self.entries)} 条"
        )

    # -- 预览 --------------------------------------------------------------
    def _refresh_preview(self, rebuild: bool = False) -> None:
        table = NounTable(self.global_table.path, self.global_table.as_entries())
        candidate = []
        for i, entry in enumerate(self.entries):
            if not self._approved[i]:
                continue
            item = self.table.item(i, 1)
            target = item.text().strip() if item is not None else entry["target"]
            candidate.append({"source": entry["source"], "target": target})
        added, _skipped = table.add(candidate)
        lines = [
            f"全局表 {len(self.global_table.entries)} 条 + 本次新增 {len(added)} 条"
            f" = 提交后 {len(table.entries)} 条",
            "-" * 48,
        ]
        lines += [f"{e['source']}->{e['target']}" for e in table.entries]
        self.preview.setPlainText("\n".join(lines))

    # -- 结果 --------------------------------------------------------------
    def approved_entries(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for i, entry in enumerate(self.entries):
            if not self._approved[i]:
                continue
            item = self.table.item(i, 1)
            target = item.text().strip() if item is not None else entry["target"]
            if not target:
                continue
            out.append({"source": entry["source"], "target": target})
        return out

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F6:
            self._on_submit()
            return
        super().keyPressEvent(event)
