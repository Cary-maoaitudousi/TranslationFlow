"""GUI 主控（需求书 §2.1 / §8）。

职责：
* 参数配置（含 API Key / base_url、每一步的模型与思考开关）；
* 循环控制：开始 / 暂停 / 继续 / 停止；
* 进度展示：当前章、11 步清单、累计 token 与费用（按当前峰谷档位计价）；
* 错误汇总：按章按步骤列出，双击打开**对应模块**的日志文件。

流水线跑在 QThread 里，审核窗口通过"事件 + 阻塞等待"回到 GUI 线程弹出。
注意：跨线程信号一律用 ``Signal(str, object)`` —— 用 ``dict`` 会让 PySide 尝试
转成 QVariantMap，遇到 threading.Event / 自定义对象时静默失败，表现为
"审核窗口没弹出来，步骤 4 报 NoneType 不可迭代"。
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import ai_steps
from ..config import (
    load_config, save_config, save_secrets, soft_warnings, step_model, step_thinking,
    validate_config,
)
from ..cost import BudgetGuard, CostLog, tier_of
from ..deepseek_client import DeepSeekClient
from ..doc_parser import parse_document
from ..logger import log_dir, setup_logging
from ..pipeline import ChapterPipeline, run_loop
from ..review import ReviewCancelled
from .reviewer_bridge import GuiThreadReviewer

STEP_NAMES = [
    "1 章节摘取", "2 初步替换", "3 AI 人名识别", "4 人名审核", "5 二次替换",
    "6 AI 翻译", "7 AI 润色检测", "8 润色审核", "9 润色替换", "10 AI 章名", "11 写入 Word",
]

# 双击错误 → 打开哪个模块的日志（A4）
STEP_LOG_MODULE = {
    1: "doc_parser", 2: "replacer", 3: "deepseek", 4: "review", 5: "replacer",
    6: "deepseek", 7: "deepseek", 8: "review", 9: "replacer", 10: "deepseek", 11: "word",
}

AI_STEPS = [(3, "步骤3 人名识别"), (6, "步骤6 翻译"), (7, "步骤7 润色检测"), (10, "步骤10 章名")]


# ---------------------------------------------------------------------------
# 工作线程
# ---------------------------------------------------------------------------
class PipelineWorker(QThread):
    event = Signal(str, object)          # 必须是 object，见模块 docstring
    finished_results = Signal(object)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.pause_event = threading.Event()
        self.pause_event.set()
        self._pipeline: ChapterPipeline | None = None

    def _emit(self, kind: str, payload: dict) -> None:
        self.event.emit(kind, payload)

    def run(self) -> None:  # noqa: D401 - QThread 入口
        try:
            chapters = parse_document(
                self.cfg.path("SOURCE_DOC_PATH"), self.cfg.CHAPTER_HEADING_PATTERN
            )
            cost_log = CostLog(self.cfg.path("COST_LOG_PATH"))
            budget = BudgetGuard(cost_log, self.cfg.BUDGET_TOTAL_CNY,
                                 self.cfg.BUDGET_PER_CHAPTER_CNY)
            client = DeepSeekClient(self.cfg, cost_log, budget)
            if self.cfg.AUTO_APPROVE:
                from ..review import AutoReviewer

                reviewer = AutoReviewer()
            else:
                reviewer = GuiThreadReviewer(self._emit)
            self._pipeline = ChapterPipeline(
                self.cfg, cost_log, client, reviewer, emit=self._emit,
                pause_event=self.pause_event,
            )
            results = run_loop(
                self.cfg, chapters, self._pipeline,
                int(self.cfg.START_CHAPTER), int(self.cfg.LOOP_COUNT), emit=self._emit,
            )
            self.finished_results.emit([r.as_dict() for r in results])
        except ReviewCancelled:
            self._emit("log", {"message": "已停止"})
            self.finished_results.emit([])
        except Exception as exc:  # noqa: BLE001
            self._emit("error", {"message": f"{type(exc).__name__}: {exc}",
                                 "trace": traceback.format_exc()})
            self.finished_results.emit([])

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def stop(self) -> None:
        if self._pipeline:
            self._pipeline.stop()


# ---------------------------------------------------------------------------
# 章节列表窗口（可滚动、可搜索）
# ---------------------------------------------------------------------------
class ChaptersDialog(QDialog):
    def __init__(self, chapters, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"章节列表（共 {len(chapters)} 章）")
        self.resize(720, 560)
        self.chapters = chapters

        self.search = QLineEdit()
        self.search.setPlaceholderText("输入章号或标题关键字过滤…")
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stat = QLabel()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.stat)
        layout.addWidget(btns)

        self.search.textChanged.connect(self._fill)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        self._fill()

    def _fill(self) -> None:
        keyword = self.search.text().strip().lower()
        self.list.clear()
        shown = 0
        for chapter in self.chapters:
            label = (f"第 {chapter.index} 个 · {chapter.heading} · "
                     f"{chapter.paragraph_count} 段 · {len(chapter.text.split())} 词"
                     f"{' · 含章末Notes' if chapter.has_trailing_notes else ''}"
                     f"{' · 无Chapter Text' if not chapter.has_chapter_text_marker else ''}")
            if keyword and keyword not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, chapter.index)
            self.list.addItem(item)
            shown += 1
        self.stat.setText(f"显示 {shown} / {len(self.chapters)} 章")

    def selected_index(self) -> int | None:
        item = self.list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("猫武士同人翻译流水线 v2.0")
        self.resize(1240, 980)

        self.cfg = load_config(None)
        self.cfg_path = Path(self.cfg.get("_config_path") or "config.json")
        self.worker: PipelineWorker | None = None
        self.cost_log = CostLog(self.cfg.path("COST_LOG_PATH"))
        setup_logging(self.cfg.path("LOG_DIR"), self.cfg.LOG_LEVEL)

        self._build_ui()
        self._load_cfg_to_ui()
        self._refresh_cost()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_cost)
        self.timer.start(3000)

    # -- 界面 --------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        # 配置区统一放进滚动区：窗口压扁时出现滚动条，不会把下面的控件挤没
        config_widget = QWidget()
        cfg_root = QVBoxLayout(config_widget)
        cfg_root.setContentsMargins(0, 0, 0, 0)

        # ===== 路径参数 =====
        path_box = QGroupBox("输入输出")
        form = QFormLayout(path_box)
        self.e_source = QLineEdit()
        self.e_output = QLineEdit()
        self.e_nouns = QLineEdit()
        self.e_style = QLineEdit()
        form.addRow("源文档（.txt/.docx）", self._browse_row(self.e_source, "文档 (*.txt *.docx *.md)"))
        form.addRow("输出 Word", self._browse_row(self.e_output, "Word (*.docx)", save=True))
        form.addRow("全局专有名词表", self._browse_row(self.e_nouns, "JSON (*.json)"))

        table_row = QHBoxLayout()
        self.b_import_table = QPushButton("导入替换表（TXT/JSON）…")
        self.b_edit_table = QPushButton("编辑替换表…")
        table_row.addWidget(self.b_import_table)
        table_row.addWidget(self.b_edit_table)
        table_row.addStretch(1)
        form.addRow("替换表", self._wrap(table_row))

        form.addRow("文风样本", self._browse_row(self.e_style, "文本 (*.txt)"))

        # ===== API 配置 =====
        api_box = QGroupBox("DeepSeek API")
        api_form = QFormLayout(api_box)
        self.e_api_key = QLineEdit()
        self.e_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.e_api_key.setPlaceholderText("sk-…（留空则沿用当前环境变量/.env）")
        self.e_base_url = QLineEdit()
        self.e_base_url.setPlaceholderText("https://api.deepseek.com")
        self.c_save_env = QCheckBox("保存到 .env（已在 .gitignore 中）")

        key_row = QHBoxLayout()
        key_row.addWidget(self.e_api_key, 1)
        self.b_save_key = QPushButton("保存密钥")
        self.b_test_key = QPushButton("测试连接")
        key_row.addWidget(self.b_save_key)
        key_row.addWidget(self.b_test_key)
        api_form.addRow("API Key", self._wrap(key_row))
        url_row = QHBoxLayout()
        url_row.addWidget(self.e_base_url, 1)
        url_row.addWidget(self.c_save_env)
        api_form.addRow("Base URL", self._wrap(url_row))
        self.l_api_state = QLabel("")
        self.l_api_state.setStyleSheet("color:#888;")
        api_form.addRow("状态", self.l_api_state)

        # ===== 每步模型与思考 =====
        model_box = QGroupBox("每一步的模型与思考模式（成本/质量自行权衡）")
        ml = QVBoxLayout(model_box)
        self.t_models = QTableWidget(len(AI_STEPS), 3)
        self.t_models.setHorizontalHeaderLabels(["步骤", "模型", "思考模式"])
        self.t_models.verticalHeader().setVisible(False)
        self.t_models.verticalHeader().setDefaultSectionSize(30)
        self.t_models.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.t_models.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.t_models.setMinimumHeight(30 * len(AI_STEPS) + 32)
        self.t_models.setMaximumHeight(30 * len(AI_STEPS) + 40)
        self.model_combos: dict[int, QComboBox] = {}
        self.thinking_boxes: dict[int, QCheckBox] = {}
        for row, (step_no, label) in enumerate(AI_STEPS):
            self.t_models.setItem(row, 0, QTableWidgetItem(label))
            combo = QComboBox()
            combo.addItem("Flash · deepseek-flash", self.cfg.MODEL_FLASH)
            combo.addItem("Pro · deepseek-v4-pro", self.cfg.MODEL_PRO)
            self.model_combos[step_no] = combo
            self.t_models.setCellWidget(row, 1, combo)
            box = QCheckBox("开启")
            self.thinking_boxes[step_no] = box
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 0, 0)
            hl.addWidget(box)
            hl.addStretch(1)
            self.t_models.setCellWidget(row, 2, holder)
        ml.addWidget(self.t_models)
        hint = QLabel("思考模式会消耗额外输出 token（实测单章 ¥0.54 → ¥0.05）；"
                      "步骤6/7 关掉更划算。点「预估成本」可看当前组合的价格。")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        ml.addWidget(hint)

        # ===== 循环与预算 =====
        loop_box = QGroupBox("循环与预算")
        lf = QFormLayout(loop_box)
        self.s_start = QSpinBox(); self.s_start.setRange(1, 9999)
        self.s_count = QSpinBox(); self.s_count.setRange(1, 999)
        self.c_label_mode = QComboBox(); self.c_label_mode.addItems(["doc", "loop"])
        self.c_title_mode = QComboBox(); self.c_title_mode.addItems(["auto", "manual"])
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("起始章")); row1.addWidget(self.s_start)
        row1.addWidget(QLabel("循环章数")); row1.addWidget(self.s_count)
        row1.addWidget(QLabel("章号口径(doc=原文)")); row1.addWidget(self.c_label_mode)
        row1.addWidget(QLabel("章名")); row1.addWidget(self.c_title_mode)
        row1.addStretch(1)
        lf.addRow("循环", self._wrap(row1))

        self.d_budget_total = QDoubleSpinBox(); self.d_budget_total.setRange(0, 9999)
        self.d_budget_total.setDecimals(4)
        self.d_budget_chapter = QDoubleSpinBox(); self.d_budget_chapter.setRange(0, 999)
        self.d_budget_chapter.setDecimals(4)
        self.s_chunk = QSpinBox(); self.s_chunk.setRange(500, 128000); self.s_chunk.setSingleStep(500)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("总预算(¥,0=不限)")); row2.addWidget(self.d_budget_total)
        row2.addWidget(QLabel("单章预算(¥)")); row2.addWidget(self.d_budget_chapter)
        row2.addWidget(QLabel("分块 token")); row2.addWidget(self.s_chunk)
        row2.addStretch(1)
        lf.addRow("成本", self._wrap(row2))

        row3 = QHBoxLayout()
        self.c_mock = QCheckBox("MOCK（不花钱）")
        self.c_auto = QCheckBox("审核全自动通过")
        self.c_skip = QCheckBox("跳过已完成步骤")
        self.c_cost_priority = QCheckBox("成本优先（峰谷计价）")
        self.c_wait_offpeak = QCheckBox("等到空闲时段再开跑")
        for w in (self.c_mock, self.c_auto, self.c_skip, self.c_cost_priority, self.c_wait_offpeak):
            row3.addWidget(w)
        row3.addStretch(1)
        lf.addRow("开关", self._wrap(row3))

        row4 = QHBoxLayout()
        self.e_holidays = QLineEdit()
        self.e_holidays.setPlaceholderText("YYYY-MM-DD, YYYY-MM-DD …（留空 = 只按周末区分）")
        self.b_holiday_help = QPushButton("怎么填？")
        row4.addWidget(self.e_holidays, 1)
        row4.addWidget(self.b_holiday_help)
        lf.addRow("法定节假日", self._wrap(row4))

        btns = QHBoxLayout()
        self.b_save = QPushButton("保存配置")
        self.b_validate = QPushButton("校验配置")
        self.b_estimate = QPushButton("预估成本")
        self.b_chapters = QPushButton("列出章节")
        for b in (self.b_save, self.b_validate, self.b_estimate, self.b_chapters):
            btns.addWidget(b)
        btns.addStretch(1)
        lf.addRow("操作", self._wrap(btns))
        # 按使用频率排序：路径 > 循环/预算 > 每步模型 > API 凭证
        cfg_root.addWidget(path_box)
        cfg_root.addWidget(loop_box)
        cfg_root.addWidget(model_box)
        cfg_root.addWidget(api_box)

        # ===== 主体：上=配置滚动区，下=步骤清单+日志/错误（可拖动分配）=====
        self.config_scroll = QScrollArea()
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setWidget(config_widget)
        self.config_scroll.setMinimumHeight(240)
        self.config_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.l_steps = QListWidget()
        for name in STEP_NAMES:
            self.l_steps.addItem(QListWidgetItem(f"☐ {name}"))
        split.addWidget(self.l_steps)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)
        self.l_cost = QLabel("成本：—")
        self.l_cost.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.l_cost.setWordWrap(True)
        self.l_err = QListWidget()
        self.l_err.setMinimumHeight(90)
        self.log_err_split = QSplitter(Qt.Orientation.Vertical)
        log_box = QWidget()
        lb = QVBoxLayout(log_box); lb.setContentsMargins(0, 0, 0, 0)
        lb.addWidget(QLabel("运行日志"))
        lb.addWidget(self.log_view, 1)
        lb.addWidget(self.l_cost)
        err_box = QWidget()
        eb = QVBoxLayout(err_box); eb.setContentsMargins(0, 0, 0, 0)
        eb.addWidget(QLabel("错误 / 警告（双击打开对应模块日志）"))
        eb.addWidget(self.l_err, 1)
        self.log_err_split.addWidget(log_box)
        self.log_err_split.addWidget(err_box)
        self.log_err_split.setSizes([260, 150])
        rv.addWidget(self.log_err_split, 1)
        split.addWidget(right)
        split.setSizes([260, 760])
        split.setChildrenCollapsible(False)

        self.main_split = QSplitter(Qt.Orientation.Vertical)
        self.main_split.addWidget(self.config_scroll)
        self.main_split.addWidget(split)
        self.main_split.setSizes([560, 300])
        self.main_split.setChildrenCollapsible(False)
        root.addWidget(self.main_split, 1)

        # ===== 控制条（始终可见，不随配置区滚动）=====
        ctrl = QHBoxLayout()
        self.b_start = QPushButton("开始")
        self.b_pause = QPushButton("暂停")
        self.b_resume = QPushButton("继续")
        self.b_stop = QPushButton("停止")
        self.l_state = QLabel("就绪")
        self.l_state.setStyleSheet("font-weight:bold;")
        for b in (self.b_start, self.b_pause, self.b_resume, self.b_stop):
            ctrl.addWidget(b)
        ctrl.addWidget(self.l_state, 1)
        root.addLayout(ctrl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)

        self.setCentralWidget(central)

        # 信号
        self.b_save.clicked.connect(self._on_save)
        self.b_validate.clicked.connect(self._on_validate)
        self.b_estimate.clicked.connect(self._on_estimate)
        self.b_chapters.clicked.connect(self._on_chapters)
        self.b_start.clicked.connect(self._on_start)
        self.b_pause.clicked.connect(lambda: self._worker_call("pause", "已暂停"))
        self.b_resume.clicked.connect(lambda: self._worker_call("resume", "已继续"))
        self.b_stop.clicked.connect(self._on_stop)
        self.b_save_key.clicked.connect(self._on_save_key)
        self.b_test_key.clicked.connect(self._on_test_key)
        self.l_err.itemDoubleClicked.connect(self._on_open_log)
        self.b_import_table.clicked.connect(self._on_import_table)
        self.b_edit_table.clicked.connect(self._on_edit_table)
        self.b_holiday_help.clicked.connect(self._on_holiday_help)

    def _browse_row(self, line_edit: QLineEdit, filt: str, save: bool = False) -> QWidget:
        box = QWidget(); lay = QHBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(line_edit, 1)
        btn = QPushButton("浏览…")

        def browse():
            if save:
                path, _ = QFileDialog.getSaveFileName(self, "选择文件", line_edit.text(), filt)
            else:
                path, _ = QFileDialog.getOpenFileName(self, "选择文件", line_edit.text(), filt)
            if path:
                line_edit.setText(path)

        btn.clicked.connect(browse)
        lay.addWidget(btn)
        return box

    @staticmethod
    def _wrap(layout) -> QWidget:
        box = QWidget()
        box.setLayout(layout)
        return box

    # -- 替换表 ------------------------------------------------------------
    def _on_import_table(self) -> None:
        """从 TXT / JSON 导入替换表，统一合并进 JSON。"""
        from ..noun_table import NounTable, load_table_file

        cfg = self._collect_cfg()
        path, _ = QFileDialog.getOpenFileName(
            self, "选择替换表", str(Path(cfg.GLOBAL_NOUN_TABLE_PATH).parent),
            "替换表 (*.txt *.json);;所有文件 (*.*)",
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
                                f"解析出 0 条可用条目，失败 {len(bad)} 行。")
            return
        target = cfg.path("GLOBAL_NOUN_TABLE_PATH")
        table = NounTable.load(target)
        added, skipped = table.add(entries)
        try:
            table.save()
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._log(f"导入替换表：解析 {len(entries)} 条 → 新增 {added}，跳过 {skipped}"
                  f"，现有 {len(table.entries)} 条 → {target}")
        if bad:
            self._add_error(f"替换表有 {len(bad)} 行无法解析，示例：{str(bad[0])[:40]}")
        QMessageBox.information(
            self, "导入完成",
            f"来源：{Path(path).name}\n解析 {len(entries)} 条，新增 {added} 条，"
            f"跳过 {skipped} 条（重复或原文已是中文）\n现有 {len(table.entries)} 条"
            f"\n已写入：{target}",
        )

    def _on_edit_table(self) -> None:
        from .noun_table_dialog import NounTableDialog

        cfg = self._collect_cfg()
        dlg = NounTableDialog(cfg.path("GLOBAL_NOUN_TABLE_PATH"), self)
        if dlg.exec():
            self._log(f"替换表已保存（{dlg.saved_count()} 条）：{cfg.GLOBAL_NOUN_TABLE_PATH}")

    def _on_holiday_help(self) -> None:
        QMessageBox.information(
            self, "法定节假日怎么填",
            "只影响「峰谷计价」：填进去的日期全天按空闲价（高峰价的一半）计算。\n\n"
            "格式：YYYY-MM-DD，多个用逗号或空格分隔\n"
            "示例：2026-01-01, 2026-02-17, 2026-10-01\n\n"
            "怎么查：搜索「国务院办公厅 节假日安排 通知」，把其中的放假日期照抄过来即可。\n"
            "填错只会让成本估算偏高/偏低，不影响翻译结果。\n"
            "也可以直接改 config.json 里的 HOLIDAYS 数组。",
        )

    # -- 配置读写 ----------------------------------------------------------
    def _load_cfg_to_ui(self) -> None:
        cfg = self.cfg
        self.e_source.setText(str(cfg.SOURCE_DOC_PATH))
        self.e_output.setText(str(cfg.OUTPUT_DOC_PATH))
        self.e_nouns.setText(str(cfg.GLOBAL_NOUN_TABLE_PATH))
        self.e_style.setText(str(cfg.STYLE_SAMPLE_PATH))
        self.e_base_url.setText(os.getenv("DEEPSEEK_BASE_URL", cfg.DEEPSEEK_BASE_URL))
        if cfg.get("_api_key_present"):
            self.l_api_state.setText(f"已从环境变量读取密钥（{self._masked_key()}）")
        else:
            self.l_api_state.setText("尚未设置密钥：请填写后点「保存密钥」，或写进 .env")

        self.s_start.setValue(int(cfg.START_CHAPTER))
        self.s_count.setValue(int(cfg.LOOP_COUNT))
        self.c_label_mode.setCurrentText(str(cfg.CHAPTER_LABEL_MODE))
        self.c_title_mode.setCurrentText(str(cfg.TITLE_MODE))
        self.d_budget_total.setValue(float(cfg.BUDGET_TOTAL_CNY))
        self.d_budget_chapter.setValue(float(cfg.BUDGET_PER_CHAPTER_CNY))
        self.s_chunk.setValue(int(cfg.MAX_INPUT_TOKENS_PER_CHUNK))
        self.c_mock.setChecked(bool(cfg.MOCK_MODE))
        self.c_auto.setChecked(bool(cfg.AUTO_APPROVE))
        self.c_skip.setChecked(bool(cfg.SKIP_COMPLETED))
        self.c_cost_priority.setChecked(bool(cfg.COST_PRIORITY))
        self.c_wait_offpeak.setChecked(bool(cfg.WAIT_FOR_OFFPEAK))
        self.e_holidays.setText(", ".join(str(h) for h in (cfg.HOLIDAYS or [])))

        for step_no, _label in AI_STEPS:
            combo = self.model_combos[step_no]
            current = step_model(cfg, step_no)
            if combo.findData(current) < 0:
                combo.addItem(f"自定义 · {current}", current)
            combo.setCurrentIndex(combo.findData(current))
            self.thinking_boxes[step_no].setChecked(step_thinking(cfg, step_no))

    @staticmethod
    def _masked_key() -> str:
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            return "未设置"
        return key[:6] + "…" + key[-4:] if len(key) > 12 else "***"

    def _collect_cfg(self):
        cfg = self.cfg
        cfg["SOURCE_DOC_PATH"] = self.e_source.text().strip()
        cfg["OUTPUT_DOC_PATH"] = self.e_output.text().strip()
        cfg["GLOBAL_NOUN_TABLE_PATH"] = self.e_nouns.text().strip()
        cfg["STYLE_SAMPLE_PATH"] = self.e_style.text().strip()
        cfg["START_CHAPTER"] = self.s_start.value()
        cfg["LOOP_COUNT"] = self.s_count.value()
        cfg["CHAPTER_LABEL_MODE"] = self.c_label_mode.currentText()
        cfg["TITLE_MODE"] = self.c_title_mode.currentText()
        cfg["BUDGET_TOTAL_CNY"] = self.d_budget_total.value()
        cfg["BUDGET_PER_CHAPTER_CNY"] = self.d_budget_chapter.value()
        cfg["MAX_INPUT_TOKENS_PER_CHUNK"] = self.s_chunk.value()
        cfg["MOCK_MODE"] = self.c_mock.isChecked()
        cfg["AUTO_APPROVE"] = self.c_auto.isChecked()
        cfg["SKIP_COMPLETED"] = self.c_skip.isChecked()
        cfg["COST_PRIORITY"] = self.c_cost_priority.isChecked()
        cfg["WAIT_FOR_OFFPEAK"] = self.c_wait_offpeak.isChecked()
        raw_holidays = self.e_holidays.text().replace("，", ",").replace(";", ",")
        holidays: list[str] = []
        for token in raw_holidays.replace("\u3001", ",").replace(" ", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                dt.date.fromisoformat(token)
            except ValueError:
                self._log(f"[提醒] 节假日格式不对，已忽略：{token}（应为 YYYY-MM-DD）")
                continue
            holidays.append(token)
        cfg["HOLIDAYS"] = sorted(set(holidays))

        # 每步模型 / 思考
        for step_no, _label in AI_STEPS:
            model = self.model_combos[step_no].currentData()
            cfg[f"MODEL_STEP{step_no}"] = model
        cfg["THINKING_STEPS"] = [
            step_no for step_no, _ in AI_STEPS if self.thinking_boxes[step_no].isChecked()
        ]

        # 密钥（只进环境变量，不写 config.json）
        key = self.e_api_key.text().strip()
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
            cfg["_api_key_present"] = True
        base = self.e_base_url.text().strip() or "https://api.deepseek.com"
        os.environ["DEEPSEEK_BASE_URL"] = base
        cfg["DEEPSEEK_BASE_URL"] = base
        return cfg

    def _on_save_key(self) -> None:
        key = self.e_api_key.text().strip()
        base = self.e_base_url.text().strip()
        if not key and not os.getenv("DEEPSEEK_API_KEY"):
            QMessageBox.warning(self, "缺少密钥", "请先填写 API Key")
            return
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
        os.environ["DEEPSEEK_BASE_URL"] = base or "https://api.deepseek.com"
        try:
            path = save_secrets(os.getenv("DEEPSEEK_API_KEY", ""), base)
            self.l_api_state.setText(f"已保存到 {path}（{self._masked_key()}）")
            self.e_api_key.clear()
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
        self.cfg["_api_key_present"] = bool(os.getenv("DEEPSEEK_API_KEY"))

    def _on_test_key(self) -> None:
        """最小请求验证密钥与连通性（约 ¥0.0003）。"""
        self._collect_cfg()
        if not os.getenv("DEEPSEEK_API_KEY"):
            QMessageBox.warning(self, "未设置密钥", "请先填写 API Key 或点「保存密钥」")
            return
        self.l_api_state.setText("正在测试连接…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            cost_log = CostLog(self.cfg.path("COST_LOG_PATH"))
            client = DeepSeekClient(self.cfg, cost_log)
            result = client.call_json(
                step_name="test-key", chapter_index=0,
                system='你是测试助手，只输出 JSON。结构：{"ok": true}',
                user="连通性测试", schema_key="titles", model=self.cfg.MODEL_FLASH,
                temperature=0.0, max_tokens=64, thinking=False, use_cache=False,
            )
            self.l_api_state.setText(
                f"连接正常 · {result.latency_ms}ms · ¥{result.cost_cny:.6f} · "
                f"档位 {tier_of(self.cfg)}"
            )
        except Exception as exc:  # noqa: BLE001
            self.l_api_state.setText(f"连接失败：{exc}")
            QMessageBox.critical(self, "连接失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _on_save(self) -> None:
        cfg = self._collect_cfg()
        target = save_config(cfg, self.cfg_path)
        self._log(f"配置已保存：{target}（密钥不写入配置文件）")

    def _on_validate(self) -> None:
        cfg = self._collect_cfg()
        problems = validate_config(cfg)
        warns = soft_warnings(cfg)
        if problems:
            QMessageBox.warning(self, "配置校验未通过", "\n".join(problems))
            self.l_state.setText("配置有问题")
            return
        self.l_state.setText("配置校验通过")
        if warns:
            QMessageBox.information(self, "提醒", "\n".join(warns))

    def _on_estimate(self) -> None:
        cfg = self._collect_cfg()
        try:
            chapters = parse_document(cfg.path("SOURCE_DOC_PATH"), cfg.CHAPTER_HEADING_PATTERN)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "解析失败", str(exc))
            return
        start, count = int(cfg.START_CHAPTER), int(cfg.LOOP_COUNT)
        selected = chapters[start - 1: start - 1 + count]
        if not selected:
            QMessageBox.warning(self, "越界", f"起始章 {start} 超出范围（共 {len(chapters)} 章）")
            return
        est = ai_steps.preflight_cost_estimate(cfg, selected[0].text)
        detail = "\n".join(f"  {k}: ¥{v:.4f}" for k, v in est.items() if not k.startswith("_"))
        thinking = [f"步骤{s}" for s, _ in AI_STEPS if cfg.get(f"MODEL_STEP{s}") and step_thinking(cfg, s)]
        QMessageBox.information(
            self, "成本预估",
            f"共 {len(selected)} 章，第 {start} 章预估（当前档位 {tier_of(cfg)}）：\n{detail}\n\n"
            f"合计约 ¥{est['合计'] * len(selected):.4f}（保守上界）\n"
            f"思考模式：{('、'.join(thinking) or '全部关闭')}",
        )

    def _on_chapters(self) -> None:
        cfg = self._collect_cfg()
        try:
            chapters = parse_document(cfg.path("SOURCE_DOC_PATH"), cfg.CHAPTER_HEADING_PATTERN)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "解析失败", str(exc))
            return
        dlg = ChaptersDialog(chapters, self)
        if dlg.exec() and dlg.selected_index():
            self.s_start.setValue(dlg.selected_index())
            self._log(f"起始章已设为文档中第 {dlg.selected_index()} 个")

    # -- 运行 --------------------------------------------------------------
    def _on_start(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "提示", "流水线正在运行")
            return
        cfg = self._collect_cfg()
        problems = validate_config(cfg)
        if problems:
            QMessageBox.warning(self, "配置校验未通过", "\n".join(problems))
            return

        if not cfg.MOCK_MODE:
            tip = ""
            try:
                chapters = parse_document(cfg.path("SOURCE_DOC_PATH"), cfg.CHAPTER_HEADING_PATTERN)
                sel = chapters[int(cfg.START_CHAPTER) - 1:
                               int(cfg.START_CHAPTER) - 1 + int(cfg.LOOP_COUNT)]
                est = sum(ai_steps.preflight_cost_estimate(cfg, c.text)["合计"] for c in sel)
                tip = f"预估成本上界 ¥{est:.4f}（{len(sel)} 章，档位 {tier_of(cfg)}）\n\n"
            except Exception:  # noqa: BLE001
                pass
            answer = QMessageBox.question(
                self, "确认调用真实 API", tip + "将产生真实费用，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._reset_progress()
        self.worker = PipelineWorker(cfg)
        self.worker.event.connect(self._on_event)
        self.worker.finished_results.connect(self._on_finished)
        self.worker.start()
        self.l_state.setText("运行中")
        self.b_start.setEnabled(False)

    def _worker_call(self, method: str, message: str) -> None:
        if self.worker and self.worker.isRunning():
            getattr(self.worker, method)()
            self.l_state.setText(message)
            self._log(message)

    def _on_stop(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self._log("已请求停止（在当前步骤边界生效）")
            self.l_state.setText("正在停止…")

    def _reset_progress(self) -> None:
        self.progress.setValue(0)
        self.l_err.clear()
        for i in range(self.l_steps.count()):
            self.l_steps.item(i).setText(f"☐ {STEP_NAMES[i]}")

    # -- 事件处理（GUI 线程）----------------------------------------------
    def _on_event(self, kind: str, payload: dict) -> None:
        if kind == "chapter_start":
            self._log("")
            self._log(f"=== 第 {payload.get('chapter_index')} 章 {payload.get('heading')} ===")
            self._reset_progress()
            self.l_state.setText(f"第 {payload.get('chapter_index')} 章处理中")
        elif kind == "loop_progress":
            total = max(1, int(payload.get("total", 1)))
            self.progress.setValue(int((payload.get("index", 1) - 1) / total * 100))
        elif kind == "profile":
            self._log("   体量：" + str(payload.get("words")) + " 词 / "
                      f"{payload.get('chars')} 字符 / {payload.get('paragraphs')} 段"
                      f" | 输入约 {payload.get('est_input_tokens')} tok"
                      f" | 切 {payload.get('chunks')} 块")
        elif kind == "step_start":
            step = int(payload.get("step", 0)) - 1
            if 0 <= step < self.l_steps.count():
                self.l_steps.item(step).setText(f"▶ {STEP_NAMES[step]}")
                self.l_steps.setCurrentRow(step)
            self._log(f"→ {payload.get('name')}")
        elif kind == "step_done":
            step = int(payload.get("step", 0)) - 1
            if 0 <= step < self.l_steps.count():
                mark = "☑" if payload.get("status") != "skipped" else "⏭"
                self.l_steps.item(step).setText(f"{mark} {STEP_NAMES[step]}")
        elif kind == "step_failed":
            step = int(payload.get("step", 0))
            self._add_error(f"步骤{step} 失败：{payload.get('error')}", step)
        elif kind == "progress":
            self._log(f"   {payload.get('message')}")
        elif kind == "chapter_done":
            status = payload.get("status")
            self._log(f"✓ 第 {payload.get('chapter_index')} 章 {status}《{payload.get('title')}》"
                      f" ¥{float(payload.get('cost') or 0):.6f}")
            for err in payload.get("errors") or []:
                self._add_error(err)
            self._refresh_cost()
        elif kind == "nouns_updated":
            self._log(f"   专有名词表新增 {payload.get('added')} 条")
        elif kind == "waiting_offpeak":
            self._log(f"⏳ 高峰时段：将在 {payload.get('target')} 后按空闲价继续")
            self.l_state.setText("等待空闲时段…")
        elif kind == "review_nouns":
            self._handle_review(payload, "nouns")
        elif kind == "review_polish":
            self._handle_review(payload, "polish")
        elif kind == "review_title":
            self._handle_review(payload, "title")
        elif kind == "log":
            self._log(payload.get("message", ""))
        elif kind == "error":
            self._add_error(payload.get("message", ""))
            self._log(payload.get("trace", ""))

    def _handle_review(self, payload: dict, kind: str) -> None:
        """在 GUI 线程打开审核窗口，并把结果交回后台线程。"""
        from ..review import QtReviewer

        holder = payload.get("holder")
        done = payload.get("done")
        if holder is None or done is None:
            self._log("[错误] 审核请求数据缺失（holder/done），已跳过本条")
            return
        reviewer = QtReviewer(self)
        try:
            if kind == "nouns":
                holder["value"] = reviewer.review_nouns(payload.get("entries") or [],
                                                        payload.get("table"))
            elif kind == "polish":
                holder["value"] = reviewer.review_polish(payload.get("entries") or [],
                                                         payload.get("text") or "")
            else:
                holder["value"] = reviewer.choose_title(payload.get("entries") or [])
        except ReviewCancelled:
            holder["cancelled"] = True
        except Exception as exc:  # noqa: BLE001 - 任何异常都必须回填，否则后台线程卡死
            holder["cancelled"] = True
            self._add_error(f"审核窗口异常：{type(exc).__name__}: {exc}")
            self._log(traceback.format_exc())
        finally:
            done.set()

    def _on_finished(self, results: list) -> None:
        self.progress.setValue(100)
        self.b_start.setEnabled(True)
        if not results:
            self.l_state.setText("已结束（无结果）")
        else:
            ok = sum(1 for r in results if r.get("status") in ("ok", "skipped"))
            self.l_state.setText(f"完成 {ok}/{len(results)} 章")
            for r in results:
                if r.get("status") not in ("ok", "skipped"):
                    self._add_error(f"第 {r.get('chapter_index')} 章 {r.get('status')}")
        self._refresh_cost()

    # -- 辅助 --------------------------------------------------------------
    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _add_error(self, message: str, step: int | None = None) -> None:
        item = QListWidgetItem(message)
        item.setData(Qt.ItemDataRole.UserRole, step)
        self.l_err.addItem(item)
        self._log(f"[错误] {message}")

    def _refresh_cost(self) -> None:
        try:
            total = self.cost_log.summary()
            tier = "高峰" if tier_of(self.cfg) == "peak" else "空闲"
            self.l_cost.setText(
                f"成本：{total.describe()}　当前档位：{tier}　明细 {self.cost_log.path}"
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_open_log(self, item: QListWidgetItem) -> None:
        step = item.data(Qt.ItemDataRole.UserRole)
        module = STEP_LOG_MODULE.get(int(step)) if step is not None else None
        target = Path(log_dir()) / f"{module}.log" if module else Path(log_dir()) / "main.log"
        if not target.exists():
            target = Path(log_dir()) / "main.log"
        try:
            os.startfile(str(target))  # noqa: S606 - Windows 打开日志
        except Exception:  # noqa: BLE001
            self._log(f"无法打开日志：{target}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "退出", "流水线仍在运行，确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
