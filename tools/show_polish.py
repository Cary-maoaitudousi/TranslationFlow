"""查看某章的润色候选与最终改动（不调 API）。

用法：python tools/show_polish.py work/run3 1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    base = Path(argv[0]) / f"chapter_{argv[1]}"
    polish = json.loads((base / f"chapter_{argv[1]}_polish.json").read_text(encoding="utf-8"))
    approved = json.loads((base / f"chapter_{argv[1]}_polish_approved.json").read_text(encoding="utf-8"))
    translated = (base / f"chapter_{argv[1]}_translated.txt").read_text(encoding="utf-8")
    final = (base / f"chapter_{argv[1]}_final_chinese.txt").read_text(encoding="utf-8")

    print(f"润色候选 {len(polish.get('entries', []))} 条，审核通过 {len(approved.get('entries', []))} 条")
    for e in approved.get("entries", []):
        print(f"\n  原文：{e['source']}")
        print(f"  替换：{e['target']}")
        print(f"  说明：{e.get('context', '')}")
    print(f"\n译文 {len(translated)} 字 → 终稿 {len(final)} 字（差 {len(final) - len(translated)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
