"""检查输出 Word 的结构（标题、段落数、是否分页）。

用法：python tools/inspect_docx.py output/译文.docx
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for name in argv:
        path = Path(name)
        if not path.exists():
            print(f"{name}：不存在")
            continue
        doc = Document(str(path))
        heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        body = [p.text for p in doc.paragraphs if p.text.strip() and not p.style.name.startswith("Heading")]
        pages = sum(1 for p in doc.paragraphs if 'w:type="page"' in p._p.xml)
        print(f"\n{path}")
        print(f"  章节标题 {len(heads)} 个：{heads}")
        print(f"  正文段落 {len(body)} 段，分页符 {pages} 处")
        if body:
            print(f"  首段：{body[0][:60]}")
            print(f"  末段：{body[-1][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
