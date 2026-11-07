"""替换表编辑器（人工维护专有名词表）。

* 直接从 ``英文->中文`` 的 TXT / 已有的 JSON 导入并统一存成 JSON；
* 表格里逐条改、加、删，支持搜索与批量删除；
* 保存前做基本校验（空原文/空译名、原文已是中文的提醒）；
* 导出成 TXT，方便 diff 与备份。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..logger import get_logger
from ..noun_table import NounTable, export_table_text, load_table_file
from ..replacer import has_cjk

log = get_logger("review")


class NounTableDialog(QDialog):
    def __init__(self, table_path: str | Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("专有名词表编辑")
        self.resize(860, 680)
        self.table_path = Path(table_path)
        self.noun_table = NounTable.load(self.table_path)
        self.rows: list[list[str]] = [[e["source"], e["target"]] for e in self.noun_table.entries]

        # 顶部：搜索 + 统计
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索原文或译名…")
        self.stat = QLabel()

        # 表格
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["原文（英文）", "译名（中文）"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )

        # 底部按钮
        self.b_import = QPushButton("导入 TXT / JSON…")
        self.b_export = QPushButton("导出 TXT…")
        self.b_add = QPushButton("新增一行")
        self.b_del = QPushButton("删除选中")
        self.b_dedup = QPushButton("合并大小写重复")
        self.b_save = QPushButton("保存")
        self.b_cancel = QPushButton("取消")

        # 提示区
        self.hint = QPlainTextEdit()
        self.hint.setReadOnly(True)
        self.hint.setMaximumHeight(86)
        self.hint.setPlainText(
            "格式说明：TXT 每行一条「英文->中文」，例如 ThunderClan->雷族；"
            "井号开头的行会被忽略。导入时可选择合并或覆盖，保存后统一写成 JSON。\n"
            "提示：原文建议保留英文原名（区分大小写），别把已经是中文的词写进原文列。"
        )
        self.hint.setStyleSheet("QPlainTextEdit{background:#f7f7f7;color:#333;border:1px solid #ccc;}")

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.stat)
        bottom = QHBoxLayout()
        for b in (self.b_import, self.b_export, self.b_add, self.b_del, self.b_dedup):
            bottom.addWidget(b)
        bottom.addStretch(1)
        bottom.addWidget(self.b_cancel)
        bottom.addWidget(self.b_save)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.hint)
        layout.addLayout(bottom)

        self.search.textChanged.connect(self._reload)
        self.table.itemChanged.connect(self._on_item_changed)
        self.b_add.clicked.connect(self._on_add)
        self.b_del.clicked.connect(self._on_delete)
        self.b_dedup.clicked.connect(self._on_dedup)
        self.b_import.clicked.connect(self._on_import)
        self.b_export.clicked.connect(self._on_export)
        self.b_save.clicked.connect(self._on_save)
        self.b_cancel.clicked.connect(self.reject)

        self._reload()

    # -- 数据同步 ----------------------------------------------------------
    def _visible_indices(self) -> list[int]:
        keyword = self.search.text().strip().lower()
        if not keyword:
            return list(range(len(self.rows)))
        return [
            i for i, (src, tgt) in enumerate(self.rows)
            if keyword in src.lower() or keyword in tgt.lower()
        ]

    def _reload(self) -> None:
        indices = self._visible_indices()
        self.table.blockSignals(True)
        self.table.setRowCount(len(indices))
        for row, idx in enumerate(indices):
            src_item = QTableWidgetItem(self.rows[idx][0])
            tgt_item = QTableWidgetItem(self.rows[idx][1])
            src_item.setData(Qt.ItemDataRole.UserRole, idx)
            self.table.setItem(row, 0, src_item)
            self.table.setItem(row, 1, tgt_item)
        self.table.blockSignals(False)
        self.stat.setText(f"共 {len(self.rows)} 条，显示 {len(indices)} 条")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        idx = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self.rows):
            return
        if item.column() == 0:
            # 原文列改动后，行索引保持不变
            self.rows[idx][0] = item.text().strip()
        else:
            self.rows[idx][1] = item.text().strip()

    # -- 操作 --------------------------------------------------------------
    def _on_add(self) -> None:
        self.rows.append(["", ""])
        self.search.clear()
        self._reload()
        self.table.scrollToBottom()
        last = self.table.rowCount() - 1
        if last >= 0:
            self.table.setCurrentCell(last, 0)
            self.table.editItem(self.table.item(last, 0))

    def _on_delete(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "未选中", "请先选中要删除的行（可按住 Ctrl/Shift 多选）")
            return
        indices = sorted(
            (self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows), reverse=True
        )
        for idx in indices:
            if idx is not None and idx < len(self.rows):
                del self.rows[idx]
        self._reload()

    def _on_dedup(self) -> None:
        """把仅大小写不同的条目合并成一条（保留第一次出现的写法与译名）。"""
        seen: dict[str, int] = {}
        kept: list[list[str]] = []
        for src, tgt in self.rows:
            key = src.strip().lower()
            if not key:
                kept.append([src, tgt])
                continue
            if key in seen:
                seen[key] += 1
                continue
            seen[key] = 1
            kept.append([src, tgt])
        removed = len(self.rows) - len(kept)
        if removed == 0:
            QMessageBox.information(self, "没有重复", "没有仅大小写不同的重复条目")
            return
        self.rows = kept
        self._reload()
        self.hint.appendPlainText(f"\n已合并 {removed} 条大小写重复（保留首次出现的写法）")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择替换表", str(self.table_path.parent), "替换表 (*.txt *.json);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            entries, bad = load_table_file(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if not entries:
            QMessageBox.warning(self, "没有可用条目",
                                f"解析出 0 条可用条目，失败 {len(bad)} 行：\n" + "\n".join(bad[:8]))
            return
        mode = QMessageBox.question(
            self, "导入方式",
            f"从 {Path(path).name} 解析到 {len(entries)} 条（失败 {len(bad)} 行）。\n\n"
            "「Yes」= 合并到当前表（同原文以新导入为准）\n「No」= 清空后覆盖\n「Cancel」= 不导入",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if mode == QMessageBox.StandardButton.Cancel:
            return
        merged: dict[str, str] = {}
        if mode == QMessageBox.StandardButton.Yes:
            for src, tgt in self.rows:
                if src:
                    merged[src] = tgt
        added = 0
        for entry in entries:
            if entry["source"] not in merged:
                added += 1
            merged[entry["source"]] = entry["target"]
        self.rows = [[src, tgt] for src, tgt in merged.items()]
        self._reload()
        self.hint.appendPlainText(
            f"\n已导入 {len(entries)} 条，新增 {added} 条，当前共 {len(self.rows)} 条（记得点保存）"
        )

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出替换表", str(self.table_path.with_suffix(".txt")), "文本 (*.txt)"
        )
        if not path:
            return
        entries = [{"source": s, "target": t} for s, t in self.rows if s and t]
        Path(path).write_text(export_table_text(entries), encoding="utf-8")
        self.hint.appendPlainText(f"\n已导出 {len(entries)} 条到 {path}")

    def _on_save(self) -> None:
        cleaned: list[list[str]] = []
        empty = 0
        chinese_src = 0
        dup: dict[str, str] = {}
        for src, tgt in self.rows:
            src, tgt = src.strip(), tgt.strip()
            if not src or not tgt:
                empty += 1
                continue
            if has_cjk(src):
                chinese_src += 1
            dup[src] = tgt
        cleaned = [[s, t] for s, t in dup.items()]
        if empty:
            answer = QMessageBox.question(
                self, "有空条目",
                f"有 {empty} 行原文或译名为空，这些行会被丢弃。继续保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if chinese_src:
            answer = QMessageBox.question(
                self, "原文看起来是中文",
                f"有 {chinese_src} 条「原文」列含中文，它们不会被英文替换命中。仍要保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        table = NounTable(self.table_path, [{"source": s, "target": t} for s, t in cleaned])
        try:
            table.save()
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        log.info(f"专有名词表已保存：{self.table_path}（{len(cleaned)} 条）")
        self.rows = cleaned
        self.accept()

    def saved_count(self) -> int:
        return len(self.rows)
