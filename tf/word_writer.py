"""Word 输出（需求书 §9）。

* 输出文件不存在则新建，存在则打开追加（跨章累积同一个文档）；
* 每章：``add_heading("第 N 章  章名", level=1)`` → 逐段 ``add_paragraph`` → 分页；
* 排版默认沿用 Word 默认样式，只有配置了字体/字号才覆盖。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .logger import get_logger

log = get_logger("word")


def _ensure_doc(path: Path):
    from docx import Document

    if path.exists() and path.stat().st_size > 0:
        return Document(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    return Document()


def split_paragraphs_for_word(text: str) -> list[str]:
    """按空行切段；单换行视为同段内的软换行，合并为一段。"""
    blocks = [b for b in text.replace("\r\n", "\n").split("\n\n")]
    out: list[str] = []
    for block in blocks:
        merged = " ".join(line.strip() for line in block.split("\n") if line.strip())
        if merged.strip():
            out.append(merged.strip())
    return out


def _apply_style(doc, cfg: Config) -> None:
    """可选排版覆盖：仅当配置了具体值才动样式。"""
    from docx.shared import Pt

    normal = doc.styles["Normal"]
    font_name = str(cfg.get("WORD_FONT_NAME", "") or "").strip()
    if font_name:
        normal.font.name = font_name
        try:
            from docx.oxml.ns import qn

            normal.element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
        except Exception:  # pragma: no cover - 样式元素缺失时忽略
            pass
    size = float(cfg.get("WORD_FONT_SIZE", 0) or 0)
    if size > 0:
        normal.font.size = Pt(size)
    spacing = float(cfg.get("WORD_LINE_SPACING", 0) or 0)
    if spacing > 0:
        pf = normal.paragraph_format
        pf.line_spacing = spacing


def append_chapter(
    cfg: Config,
    chapter_label: int,
    title: str,
    body_text: str,
    output_path: str | Path | None = None,
) -> Path:
    """把一章写入输出 Word，返回文档路径。"""
    try:
        from docx import Document  # noqa: F401  仅用于错误提示
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("写入 Word 需要 python-docx：pip install python-docx") from exc

    path = Path(output_path) if output_path else cfg.path("OUTPUT_DOC_PATH")
    if not path.is_absolute():
        path = (Path(cfg.get("_project_root", ".")) / path).resolve()

    doc = _ensure_doc(path)
    _apply_style(doc, cfg)

    template = str(cfg.get("WORD_TITLE_TEMPLATE", "第 {label} 章  {title}"))
    heading_text = template.format(label=chapter_label, title=title or "未命名")
    doc.add_heading(heading_text, level=1)

    paragraphs = split_paragraphs_for_word(body_text)
    if not paragraphs:
        log.warning(f"第 {chapter_label} 章正文为空，仅写入标题")
    for para in paragraphs:
        doc.add_paragraph(para)

    doc.add_page_break()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    log.info(f"第 {chapter_label} 章已写入 {path}（{len(paragraphs)} 段）")
    return path


def count_chapters(path: str | Path) -> int:
    """统计文档里已有的"第 N 章"标题数，用于幂等校验。"""
    p = Path(path)
    if not p.exists():
        return 0
    try:
        from docx import Document

        doc = Document(str(p))
    except Exception:  # pragma: no cover
        return 0
    return sum(1 for para in doc.paragraphs if para.style.name.startswith("Heading 1"))
