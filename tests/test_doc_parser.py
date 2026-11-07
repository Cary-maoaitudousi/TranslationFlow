"""文档解析测试（需求书 §3.4 边界示例 + 验收标准 §12.4）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tf.doc_parser import ParseError, parse_document, parse_text

ROOT = Path(__file__).resolve().parent.parent

DOC_WITH_BOTH = """Chapter 100: Arc 3 - chapter 15

Notes:
(See the end of the chapter for notes.)

Chapter Text

The slow breeze carried the smell of rain.

Frostfur watched the ridge.

Notes:
Chapter 100 baybee!!

Chapter 101: Arc 3 - Chapter 16

The hollow was practically buzzing.
"""

DOC_NO_CHAPTER_TEXT = """Chapter 1: test

A line before any marker.

Another line.
"""

DOC_SINGLE = """Chapter 7: solo

Chapter Text

One paragraph.

Second paragraph.
"""

DOC_NO_NOTES = """Chapter 2

Chapter Text

First.

Second.
"""


def test_heading_with_subtitle_is_not_a_new_chapter():
    chapters = parse_text(DOC_WITH_BOTH)
    assert len(chapters) == 2
    assert chapters[0].chapter_no == 100
    assert chapters[0].subtitle == "Arc 3 - chapter 15"
    assert chapters[1].chapter_no == 101
    assert chapters[1].subtitle == "Arc 3 - Chapter 16"


def test_pre_notes_excluded_and_body_start_after_marker():
    chapters = parse_text(DOC_WITH_BOTH)
    ch = chapters[0]
    assert ch.text.startswith("The slow breeze")
    assert "See the end of the chapter" not in ch.text
    assert ch.has_chapter_text_marker is True
    assert ch.has_pre_notes is True


def test_trailing_notes_excluded_from_body():
    ch = parse_text(DOC_WITH_BOTH)[0]
    assert ch.has_trailing_notes is True
    assert "baybee" not in ch.text
    assert ch.text.rstrip().endswith("Frostfur watched the ridge.")


def test_notes_line_starting_with_chapter_is_not_a_heading():
    """章末 Notes 里的 `Chapter 100 baybee!!` 不能被当成章头。"""
    chapters = parse_text(DOC_WITH_BOTH)
    assert len(chapters) == 2
    assert all("baybee" not in c.heading for c in chapters)


def test_next_chapter_without_notes_runs_to_eof():
    ch = parse_text(DOC_WITH_BOTH)[1]
    assert ch.has_trailing_notes is False
    assert ch.has_pre_notes is False
    assert ch.text.strip().startswith("The hollow was practically buzzing.")
    assert ch.text.strip().endswith("buzzing.")


def test_missing_chapter_text_falls_back_to_line_after_heading():
    chapters = parse_text(DOC_NO_CHAPTER_TEXT)
    ch = chapters[0]
    assert ch.has_chapter_text_marker is False
    assert ch.text.startswith("A line before any marker.")
    assert ch.paragraph_count == 2


def test_no_notes_at_all():
    ch = parse_text(DOC_NO_NOTES)[0]
    assert ch.has_trailing_notes is False
    assert ch.text == "First.\n\nSecond."


def test_paragraph_structure_preserved():
    ch = parse_text(DOC_SINGLE)[0]
    assert ch.paragraph_count == 2
    assert "\n\n" in ch.text


def test_line_numbers_recorded():
    ch = parse_text(DOC_SINGLE)[0]
    assert ch.start_line == 1
    assert ch.end_line >= 6


def test_no_heading_raises():
    with pytest.raises(ParseError):
        parse_text("Just prose, no headings at all.")


def test_custom_heading_pattern():
    text = "## 3\n\nChapter Text\n\nHello.\n"
    chapters = parse_text(text, r"^\s*##\s*(\d+)")
    assert len(chapters) == 1
    assert chapters[0].text.strip() == "Hello."


def test_sample_document_two_chapters():
    chapters = parse_document(ROOT / "samples" / "source_sample.txt")
    assert len(chapters) == 2
    assert chapters[0].chapter_no == 100
    assert "The slow breeze" in chapters[0].text
    assert "baybee" not in chapters[0].text
    assert chapters[1].chapter_no == 101
    assert "practically buzzing" in chapters[1].text
