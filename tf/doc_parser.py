"""文档解析：章节切分与正文摘取（需求书 §3）。

正文范围规则复述：
1. 章头 = 匹配 ``^\\s*Chapter\\s+(\\d+)`` 的行（区分大小写）；冒号后的副标题不是章头；
2. 起点 = 章头之后 ``Chapter Text`` 行的**下一行**；
3. 终点 = 章末 ``Notes:`` 行之前；没有则到下一个章头之前 / 文件末尾；
4. ``Chapter Text`` 之前的 ``Notes:`` 属于元数据，不进正文；
5. 找不到 ``Chapter Text`` 时从章头下一行开始，记 WARNING。

解析全程不消耗 API token。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .logger import get_logger

log = get_logger("doc_parser")

DEFAULT_HEADING_PATTERN = r"^\s*Chapter\s+(\d+)"
CHAPTER_TEXT_RE = re.compile(r"^\s*Chapter\s*Text\s*$")
NOTES_RE = re.compile(r"^\s*Notes\s*:")
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1252")


class ParseError(RuntimeError):
    """源文档无法解析时抛出（属于 ERROR 级，不重试）。"""


@dataclass
class Chapter:
    """一章的结构化信息。"""

    index: int                     # 在文档中的序号（1 基，按章头出现顺序）
    heading: str                   # 章头整行原文
    chapter_no: int | None         # 章头里的数字（Chapter N 的 N）
    subtitle: str                   # 冒号后的副标题，可能为空
    start_line: int                # 章头所在行号（1 基）
    end_line: int                  # 正文最后一行行号（1 基），无正文时等于 start_line
    text: str                      # 正文纯文本（保留段落空行）
    has_chapter_text_marker: bool  # 是否找到 Chapter Text 标记
    has_pre_notes: bool            # Chapter Text 之前是否有 Notes 块
    has_trailing_notes: bool       # 章末是否有 Notes 块（被排除）
    extra_notes: list[str] = field(default_factory=list)  # 跳过的前置 Notes 行，仅调试用

    @property
    def paragraph_count(self) -> int:
        return len([p for p in self.text.split("\n") if p.strip()])

    def meta_dict(self) -> dict:
        return {
            "index": self.index,
            "heading": self.heading,
            "chapter_no": self.chapter_no,
            "subtitle": self.subtitle,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "paragraphs": self.paragraph_count,
            "has_chapter_text_marker": self.has_chapter_text_marker,
            "has_pre_notes": self.has_pre_notes,
            "has_trailing_notes": self.has_trailing_notes,
            "char_count": len(self.text),
        }


# ---------------------------------------------------------------------------
# 读文件
# ---------------------------------------------------------------------------
def read_source_text(path: str | Path) -> str:
    """读取 .txt/.md/.docx 源文档，统一返回纯文本（行以 \\n 分隔）。"""
    p = Path(path)
    if not p.exists():
        raise ParseError(f"源文档不存在：{p}")

    suffix = p.suffix.lower()
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:  # pragma: no cover
            raise ParseError("读取 .docx 需要 python-docx：pip install python-docx") from exc
        doc = Document(str(p))
        lines: list[str] = []
        for para in doc.paragraphs:
            lines.append(para.text.rstrip())
        return "\n".join(lines)

    if suffix not in {".txt", ".md", ""}:
        raise ParseError(f"不支持的源文档格式：{suffix}（支持 .txt/.docx/.md）")

    raw = p.read_bytes()
    for enc in TEXT_ENCODINGS:
        try:
            text = raw.decode(enc)
            if enc != "utf-8-sig":
                log.debug(f"源文档解码使用编码 {enc}")
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise ParseError(f"无法解码源文档（尝试过 {TEXT_ENCODINGS}）：{p}")


# ---------------------------------------------------------------------------
# 切章
# ---------------------------------------------------------------------------
def _looks_like_heading(line: str, match: re.Match) -> bool:
    """章头行的额外合理性检查。

    默认正则 ``^\\s*Chapter\\s+(\\d+)`` 会把章末 Notes 里的
    ``Chapter 100 baybee!!`` 也当成章头。章头行的特征是该行以章号结束，
    或章号后紧跟冒号/分隔符；紧跟普通单词的视为正文/备注。
    """
    rest = line[match.end():].strip()
    if not rest:
        return True
    return rest[0] in ":：-–—|·"


def iter_heading_matches(lines: list[str], pattern: re.Pattern) -> Iterable[tuple[int, re.Match]]:
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m and _looks_like_heading(line, m):
            yield i, m


def _strip_blank_edges(block: list[str]) -> list[str]:
    start, end = 0, len(block)
    while start < end and not block[start].strip():
        start += 1
    while end > start and not block[end - 1].strip():
        end -= 1
    return block[start:end]


def _strip_indent(block: list[str]) -> list[str]:
    """去掉行首缩进（同人站常见 4/8 空格缩进），保留段内空行。"""
    return [line.strip() if line.strip() else "" for line in block]


def parse_text(
    text: str,
    heading_pattern: str = DEFAULT_HEADING_PATTERN,
    strip_indent: bool = True,
) -> list[Chapter]:
    """把整篇文本切成 Chapter 列表。"""
    try:
        pattern = re.compile(heading_pattern)
    except re.error as exc:
        raise ParseError(f"CHAPTER_HEADING_PATTERN 非法：{exc}") from exc
    if pattern.groups < 1:
        log.warning("章头正则没有捕获组，章序号将回退为出现顺序")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    heads = list(iter_heading_matches(lines, pattern))
    if not heads:
        raise ParseError(f"未识别到任何章头（正则：{heading_pattern}）")

    chapters: list[Chapter] = []
    for order, (head_i, match) in enumerate(heads, start=1):
        next_head_i = heads[order][0] if order < len(heads) else len(lines)
        heading_line = lines[head_i].strip()

        chapter_no: int | None = None
        if match.groups():
            try:
                chapter_no = int(match.group(1))
            except (TypeError, ValueError):
                chapter_no = None
        subtitle = ""
        if ":" in heading_line:
            subtitle = heading_line.split(":", 1)[1].strip()

        # ---- 定位 Chapter Text ----
        marker_i = None
        for i in range(head_i + 1, next_head_i):
            if CHAPTER_TEXT_RE.match(lines[i]):
                marker_i = i
                break

        has_pre_notes = False
        pre_notes: list[str] = []
        if marker_i is not None:
            # Chapter Text 之前的 Notes 块（含 "(See the end of the chapter for notes.)"）
            for i in range(head_i + 1, marker_i):
                if NOTES_RE.match(lines[i]):
                    has_pre_notes = True
                    pre_notes.append(lines[i].strip())
            body_start = marker_i + 1
        else:
            body_start = head_i + 1
            log.warning(
                f"第 {order} 章（{heading_line}）未找到 'Chapter Text'，"
                f"退化为从章头下一行开始摘取"
            )
            # 容错：跳过紧邻章头的 Notes 块
            j = body_start
            while j < next_head_i and not lines[j].strip():
                j += 1
            if j < next_head_i and NOTES_RE.match(lines[j]):
                has_pre_notes = True
                notes_end = j
                while notes_end < next_head_i and lines[notes_end].strip():
                    pre_notes.append(lines[notes_end].strip())
                    notes_end += 1
                body_start = notes_end
                log.info(f"第 {order} 章：跳过章头后的前置 Notes 块（{notes_end - j} 行）")

        # ---- 定位章末 Notes ----
        body_end = next_head_i
        has_trailing_notes = False
        for i in range(body_start, next_head_i):
            if NOTES_RE.match(lines[i]):
                body_end = i
                has_trailing_notes = True
                break

        body_lines = _strip_blank_edges(lines[body_start:body_end])
        if strip_indent:
            body_lines = _strip_blank_edges(_strip_indent(body_lines))
        body_text = "\n".join(body_lines)

        if not body_text.strip():
            log.warning(f"第 {order} 章（{heading_line}）正文为空")

        last_line = body_start + len(body_lines)  # 1 基行号
        chapters.append(
            Chapter(
                index=order,
                heading=heading_line,
                chapter_no=chapter_no,
                subtitle=subtitle,
                start_line=head_i + 1,
                end_line=max(last_line, head_i + 1),
                text=body_text,
                has_chapter_text_marker=marker_i is not None,
                has_pre_notes=has_pre_notes,
                has_trailing_notes=has_trailing_notes,
                extra_notes=pre_notes,
            )
        )

    log.info(f"解析完成：共 {len(chapters)} 章，总字符 {sum(len(c.text) for c in chapters)}")
    return chapters


def parse_document(
    path: str | Path,
    heading_pattern: str = DEFAULT_HEADING_PATTERN,
    strip_indent: bool = True,
) -> list[Chapter]:
    return parse_text(read_source_text(path), heading_pattern, strip_indent)


def save_chapter_artifacts(chapter: Chapter, chapter_dir: str | Path) -> tuple[Path, Path]:
    """写出 chapter_{n}_raw.txt 与 chapter_{n}_meta.json（§3.3）。"""
    d = Path(chapter_dir)
    d.mkdir(parents=True, exist_ok=True)
    raw_path = d / "raw.txt"
    meta_path = d / "meta.json"
    raw_path.write_text(chapter.text, encoding="utf-8")
    meta_path.write_text(
        json.dumps(chapter.meta_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return raw_path, meta_path
