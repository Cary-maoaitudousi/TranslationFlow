"""Word 输出测试（验收标准 §12.13）。"""

from __future__ import annotations

from docx import Document

from tf.word_writer import append_chapter, count_chapters, split_paragraphs_for_word


def test_split_paragraphs_merges_soft_line_breaks():
    text = "第一段第一行\n第一段第二行\n\n第二段"
    assert split_paragraphs_for_word(text) == ["第一段第一行 第一段第二行", "第二段"]


def test_append_chapter_creates_heading_and_paragraphs(cfg_factory):
    cfg = cfg_factory()
    body = "两猫并肩穿过蕨丛。\n\n雾气在他们身后合拢。"
    path = append_chapter(cfg, 100, "星池寒镜", body)

    doc = Document(str(path))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading 1")]
    assert headings == ["第 100 章  星池寒镜"]
    texts = [p.text for p in doc.paragraphs]
    assert "两猫并肩穿过蕨丛。" in texts
    assert "雾气在他们身后合拢。" in texts


def test_append_second_chapter_appends_not_overwrites(cfg_factory):
    cfg = cfg_factory()
    append_chapter(cfg, 1, "其一", "正文一。")
    path = append_chapter(cfg, 2, "其二", "正文二。")
    doc = Document(str(path))
    assert count_chapters(path) == 2
    joined = "\n".join(p.text for p in doc.paragraphs)
    assert "正文一。" in joined and "正文二。" in joined


def test_page_break_added_at_chapter_end(cfg_factory):
    cfg = cfg_factory()
    path = append_chapter(cfg, 3, "分页", "正文。")
    doc = Document(str(path))
    xml = "".join(p._p.xml for p in doc.paragraphs)
    assert 'w:type="page"' in xml


def test_custom_title_template(cfg_factory):
    cfg = cfg_factory(WORD_TITLE_TEMPLATE="第{label}章 · {title}")
    path = append_chapter(cfg, 7, "月池聚巫", "正文。")
    doc = Document(str(path))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading 1")]
    assert headings == ["第7章 · 月池聚巫"]


def test_empty_title_falls_back(cfg_factory):
    cfg = cfg_factory()
    path = append_chapter(cfg, 8, "", "正文。")
    doc = Document(str(path))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading 1")]
    assert "未命名" in headings[0]
