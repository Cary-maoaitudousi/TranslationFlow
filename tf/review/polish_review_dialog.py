"""步骤 8：润色表审核窗口（需求书 §7.2）。

上表：原文片段 | 替换为 | 说明 | 状态
下框：预览——对译文做一次内存中的模式 B 替换，只展示受影响的段落。
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from ..replacer import preview_changes
from .common import (
    COLOR_APPROVE, COLOR_SKIP, COLOR_WARN, STATUS_APPROVE, STATUS_MISSING,
    STATUS_MODIFIED, STATUS_PENDING, STATUS_SKIP, ReviewDialogBase,
)


class PolishReviewDialog(ReviewDialogBase):
    def __init__(self, entries: list[dict], translated_text: str, parent=None):
        super().__init__(
            "润色表审核（步骤 8）", 1, ["原文片段", "替换为", "说明", "状态"], parent
        )
        self.entries = [dict(e) for e in entries]
        self.translated_text = translated_text
        self._approved = [False] * len(self.entries)
        self._modified = [False] * len(self.entries)

        # source 不存在于译文 → 直接标注待人工确认（需求书 §6.3）
        for i, entry in enumerate(self.entries):
            if entry.get("_missing") == "1" or entry["source"] not in translated_text:
                self.entries[i]["_missing"] = "1"

        self.table.blockSignals(True)
        self.table.setRowCount(len(self.entries))
        for i, entry in enumerate(self.entries):
            missing = entry.get("_missing") == "1"
            self.table.setItem(i, 0, self._item(entry["source"], COLOR_WARN if missing else None))
            self.table.setItem(i, 1, self._item(entry["target"], editable=True))
            self.table.setItem(i, 2, self._item(entry.get("context", "")))
            self.table.setItem(
                i, 3, self._item(STATUS_MISSING if missing else STATUS_PENDING,
                                 COLOR_WARN)
            )
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
        self._refresh_preview(rebuild=True)

    def _on_approve(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        if self._approved[row]:
            self._approved[row] = False
            self.set_status(
                row,
                STATUS_MISSING if self.entries[row].get("_missing") == "1" else STATUS_SKIP,
                COLOR_SKIP,
            )
        else:
            item = self.table.item(row, 1)
            if item is None or not item.text().strip():
                self.preview.appendPlainText("\n⚠ 替换文本为空，已忽略该条的通过操作")
                self.table.next_row()
                return
            self._approved[row] = True
            self.set_status(
                row, STATUS_MODIFIED if self._modified[row] else STATUS_APPROVE, COLOR_APPROVE
            )
        self._update_footer()
        self._refresh_preview(rebuild=True)
        self.table.next_row()

    def _update_footer(self) -> None:
        approved = sum(1 for a in self._approved if a)
        self.btn_submit.setText(
            f"确认提交 (F6) · 通过 {approved}/{len(self.entries)} 条"
        )

    # -- 预览 --------------------------------------------------------------
    def _refresh_preview(self, rebuild: bool = False) -> None:
        entries = self.approved_entries()
        if not entries:
            self.preview.setPlainText("（尚未通过任何条目）")
            return
        pairs = preview_changes(self.translated_text, entries)
        lines = [f"已通过 {len(entries)} 条，影响 {len(pairs)} 个段落：", "-" * 48]
        for i, (before, after) in enumerate(pairs[:20], start=1):
            lines.append(f"[{i}] 原文：{before}")
            lines.append(f"    替换：{after}\n")
        if len(pairs) > 20:
            lines.append(f"…… 其余 {len(pairs) - 20} 段省略")
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
            out.append(
                {"source": entry["source"], "target": target,
                 "context": entry.get("context", "")}
            )
        return out

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F6:
            self._on_submit()
            return
        super().keyPressEvent(event)
