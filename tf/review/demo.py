"""审核窗口的演示/自检数据（用于人工验收窗口效果，不消耗任何 API）。

用真实的替换表与真实的参考文风构造演示数据，让审核窗口里看到的内容与
实际跑起来时一致：

* 专有名词窗口：取全局表里的一批条目当作"AI 新推荐"，其中几条故意留空译名，
  用来验证"译名为空不允许通过"的保护；
* 润色窗口：拿参考文风里的真实中文段落，挑出含"人"字的句子做替换候选，
  其中一条故意写成译文中不存在的片段，用来验证"待人工确认"标记；
* 章名窗口：5 个固定候选。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..logger import get_logger
from ..fandom import profile_for
from ..noun_table import NounTable

log = get_logger("review")

FALLBACK_ZH = (
    "她咕哝了一声，两人陷入了惬意的沉默，他心里满是感激。\n\n"
    "这真是一副令人害怕的场景。\n\n"
    "“可毕竟他们人太多了，我双拳难敌四手！”\n\n"
    "四周安静了下来，谁也没有先开口。"
)

# 通用（非特定 IP）时的中性替换表；有 IP 档案时用档案里的称谓
NEUTRAL_REWRITES = [
    ("两人", "两者"),
    ("他们人", "他们"),
    ("令人", "令他"),
    ("一个人", "一个身影"),
]


def _zh_sample(cfg) -> str:
    """取参考文风的前若干段，作为演示用"译文"。"""
    path = cfg.path("STYLE_SAMPLE_PATH")
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if paragraphs:
            return "\n\n".join(paragraphs[:6])
    return FALLBACK_ZH


def build_demo_data(cfg) -> dict[str, Any]:
    table = NounTable.load(cfg.path("GLOBAL_NOUN_TABLE_PATH"))
    source_entries = table.entries[-18:] if table.entries else []
    if not source_entries:
        source_entries = [
            {"source": "Northwatch", "target": "北望堡"},
            {"source": "Silver River", "target": "银水河"},
            {"source": "Aldren", "target": "奥德伦"},
            {"source": "Corvin", "target": "科文"},
        ]
    noun_entries = [dict(e) for e in source_entries]
    # 混入两条真实"待审核"的英文名，模拟 AI 推荐
    noun_entries += [
        {"source": "Mirelle", "target": "米蕾尔"},
        {"source": "Thornwick", "target": ""},        # 空译名 → 验证保护
        {"source": "Ashen Ford", "target": "灰渡"},
    ]

    zh_text = _zh_sample(cfg)
    profile = profile_for(cfg)
    subject = profile.subject_singular if not profile.is_fallback else ""
    if subject and subject not in ("角色", "人"):
        rewrites = [("两人", f"两{subject}"), ("他们人", f"他们{subject}"), ("令人", "令他"),
                    ("人", subject)]
        context_note = f"人类特征表达 → 改用「{subject}」"
    else:
        rewrites = NEUTRAL_REWRITES
        context_note = "人类特征表达 → 中性化"
    polish_entries: list[dict[str, str]] = []
    for m in re.finditer(r"[^。！？\n]{0,12}人[^。！？\n]{0,12}[。！？]?", zh_text):
        snippet = m.group(0).strip()
        if len(snippet) < 4:
            continue
        target = snippet
        for src, dst in rewrites:
            target = target.replace(src, dst)
        polish_entries.append({"source": snippet, "target": target, "context": context_note})
        if len(polish_entries) >= 8:
            break
    if not polish_entries:
        polish_entries = [
            {"source": "两人陷入了惬意的沉默", "target": "两猫陷入了惬意的沉默", "context": "两人→两猫"},
            {"source": "我双拳难敌四手", "target": "我双爪难敌四掌", "context": "肢体人类化"},
        ]
    # 一条"译文中不存在"的候选
    polish_entries.append({
        "source": "这句在参考文风里根本不存在。",
        "target": "这句替换不应生效。",
        "context": "来源不存在的条目（应标为待人工确认）",
    })

    titles = [
        {"style": "意象象征", "title": "风起长夜", "reason": "以长夜与风声起笔，笼罩全章的不安。"},
        {"style": "谶语悬念", "title": "孤灯照影", "reason": "扣住悬而未决的线索，留下伏笔。"},
        {"style": "场景叙事", "title": "暗潮将至", "reason": "以关键场景为叙事支点，点明推进。"},
        {"style": "反讽对照", "title": "旧盟新裂", "reason": "昔日盟约与当下处境形成反差。"},
        {"style": "主题提炼", "title": "殊途同归", "reason": "凝练本章的核心主题。"},
    ]
    return {
        "nouns": noun_entries,
        "polish": polish_entries,
        "translated": zh_text,
        "titles": titles,
        "table": table,
    }


def run_demo(cfg, parent=None) -> None:
    """依次打开三个审核窗口（阻塞，直到用户关闭）。"""
    from .noun_review_dialog import NounReviewDialog
    from .polish_review_dialog import PolishReviewDialog
    from .title_dialog import TitleChooserDialog

    data = build_demo_data(cfg)
    log.info("进入审核窗口演示模式（不调用 API、不写任何文件）")

    d1 = NounReviewDialog(data["nouns"], data["table"], parent)
    d1.setWindowTitle("演示 · 专有名词审核（步骤 4）")
    d1.exec()

    d2 = PolishReviewDialog(data["polish"], data["translated"], parent)
    d2.setWindowTitle("演示 · 润色表审核（步骤 8）")
    d2.exec()

    d3 = TitleChooserDialog(data["titles"], parent)
    d3.setWindowTitle("演示 · 章名选择（步骤 10）")
    d3.exec()


def capture_demo_png(cfg, out_dir: str | Path) -> list[Path]:
    """把三个窗口渲染成 PNG（无显示器也能跑），用于快速查看效果。"""
    from PySide6.QtWidgets import QApplication

    from .noun_review_dialog import NounReviewDialog
    from .polish_review_dialog import PolishReviewDialog
    from .title_dialog import TitleChooserDialog

    app = QApplication.instance() or QApplication([])
    data = build_demo_data(cfg)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shots: list[Path] = []

    def shot(dialog, name: str) -> None:
        dialog.show()
        app.processEvents()
        dialog.adjustSize()
        app.processEvents()
        path = out / name
        dialog.grab().save(str(path))
        shots.append(path)
        dialog.close()

    d1 = NounReviewDialog(data["nouns"], data["table"])
    d1.setWindowTitle("专有名词审核（步骤 4）")
    # 先通过前两条，截图上能看到状态与合并表
    for _ in range(2):
        d1._on_approve()
    shot(d1, "review_1_nouns.png")

    d2 = PolishReviewDialog(data["polish"], data["translated"])
    d2.setWindowTitle("润色表审核（步骤 8）")
    for _ in range(2):
        d2._on_approve()
    shot(d2, "review_2_polish.png")

    d3 = TitleChooserDialog(data["titles"])
    d3.setWindowTitle("章名选择（步骤 10）")
    shot(d3, "review_3_titles.png")
    return shots
