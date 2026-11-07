"""人工审核层（需求书 §7）。

* ``QtReviewer``：PySide6 弹窗审核，交互使用；
* ``AutoReviewer``：无头自动审核（AUTO_APPROVE 或 --auto-approve），
  用于批处理、回归测试和"不想逐条点"的场景，规则与人工一致。

两者对流水线暴露同一套方法，因此 pipeline 不关心审核是弹窗还是自动。
"""

from __future__ import annotations

from typing import Protocol

from ..logger import get_logger
from ..noun_table import NounTable

log = get_logger("review")


class ReviewCancelled(RuntimeError):
    """用户在审核窗口点了取消，本章终止。"""


class Reviewer(Protocol):
    def review_nouns(self, entries: list[dict], global_table: NounTable) -> list[dict]: ...
    def review_polish(self, entries: list[dict], translated_text: str) -> list[dict]: ...
    def choose_title(self, titles: list[dict]) -> dict: ...


class AutoReviewer:
    """全部通过（跳过标记为"待人工确认"的条目）。"""

    def __init__(self, chapter_label: str = ""):
        self.chapter_label = chapter_label
        self.stats: dict[str, int] = {"nouns_approved": 0, "polish_approved": 0, "polish_skipped": 0}

    def review_nouns(self, entries: list[dict], global_table: NounTable) -> list[dict]:
        approved = [dict(e) for e in entries if e.get("source") and e.get("target")]
        self.stats["nouns_approved"] = len(approved)
        log.info(f"[自动审核] 专有名词 {len(approved)}/{len(entries)} 条全部通过")
        return approved

    def review_polish(self, entries: list[dict], translated_text: str) -> list[dict]:
        approved, skipped = [], 0
        for entry in entries:
            if entry.get("_missing") == "1" or entry["source"] not in translated_text:
                skipped += 1
                log.warning(f"[自动审核] 跳过 source 不存在的条目：{entry['source'][:24]!r}")
                continue
            approved.append(dict(entry))
        self.stats["polish_approved"] = len(approved)
        self.stats["polish_skipped"] = skipped
        log.info(f"[自动审核] 润色条目 {len(approved)}/{len(entries)} 条通过，跳过 {skipped} 条")
        return approved

    def choose_title(self, titles: list[dict]) -> dict:
        if not titles:
            return {"style": "", "title": "未命名", "reason": "模型未返回候选章名"}
        log.info(f"[自动审核] 章名取第一条：{titles[0]['title']}")
        return dict(titles[0])


class QtReviewer:
    """弹窗审核。需要在 Qt 事件循环线程中调用。"""

    def __init__(self, parent=None):
        self.parent = parent

    def review_nouns(self, entries: list[dict], global_table: NounTable) -> list[dict]:
        if not entries:
            log.info("[审核] 本章没有待审核的新专有名词")
            return []
        from .noun_review_dialog import NounReviewDialog

        dlg = NounReviewDialog(entries, global_table, self.parent)
        if dlg.exec() and dlg.submitted:
            approved = dlg.approved_entries()
            log.info(f"[审核] 专有名词通过 {len(approved)}/{len(entries)} 条")
            return approved
        if dlg.skip_all:
            log.info("[审核] 用户选择跳过本章新增专有名词")
            return []
        raise ReviewCancelled("用户取消了专有名词审核")

    def review_polish(self, entries: list[dict], translated_text: str) -> list[dict]:
        if not entries:
            log.info("[审核] 本章没有待审核的润色条目")
            return []
        from .polish_review_dialog import PolishReviewDialog

        dlg = PolishReviewDialog(entries, translated_text, self.parent)
        if dlg.exec() and dlg.submitted:
            approved = dlg.approved_entries()
            log.info(f"[审核] 润色条目通过 {len(approved)}/{len(entries)} 条")
            return approved
        if dlg.skip_all:
            log.info("[审核] 用户选择全部跳过润色替换")
            return []
        raise ReviewCancelled("用户取消了润色表审核")

    def choose_title(self, titles: list[dict]) -> dict:
        if not titles:
            return {"style": "", "title": "未命名", "reason": "模型未返回候选章名"}
        from .title_dialog import TitleChooserDialog

        dlg = TitleChooserDialog(titles, self.parent)
        dlg.exec()
        chosen = dlg.selected or titles[0]
        log.info(f"[审核] 章名选定：{chosen['title']}")
        return dict(chosen)


def build_reviewer(cfg, parent=None) -> Reviewer:
    """按配置选择审核实现。"""
    if cfg.AUTO_APPROVE:
        return AutoReviewer()
    return QtReviewer(parent)
