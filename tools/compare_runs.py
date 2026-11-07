"""对比两次运行：译文抽样、润色条目数、章名、成本。

用法：python tools/compare_runs.py work/run1 work/run2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    for name in argv[:2]:
        chapter_dir = Path(name) / "chapter_1"
        print(f"\n{'=' * 70}\n{name}")
        translated = load(chapter_dir / "chapter_1_translated.txt")
        final = load(chapter_dir / "chapter_1_final_chinese.txt")
        print(f"译文 {len(translated)} 字 / 终稿 {len(final)} 字")
        print("--- 译文前 260 字 ---")
        print(translated[:260].replace("\n", " ⏎ "))
        print("--- 译文中段 260 字 ---")
        mid = len(translated) // 2
        print(translated[mid:mid + 260].replace("\n", " ⏎ "))
        polish = json.loads(load(chapter_dir / "chapter_1_polish.json") or '{"entries": []}')
        entries = polish.get("entries", [])
        print(f"--- 润色候选 {len(entries)} 条 ---")
        for e in entries[:6]:
            print(f"    {e.get('source', '')[:46]!r} → {e.get('target', '')[:46]!r}")
        titles = json.loads(load(chapter_dir / "chapter_1_titles.json") or '{"titles": []}')
        print("--- 章名 ---")
        for t in titles.get("titles", []):
            print(f"    {t.get('style', ''):<6}{t.get('title', '')}  {t.get('reason', '')[:40]}")
        summary = json.loads(load(chapter_dir / "chapter_1_summary.json") or "{}")
        print(f"--- 成本 ¥{summary.get('cost_cny', 0):.4f} / 耗时 {summary.get('duration_s', 0):.0f}s ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
