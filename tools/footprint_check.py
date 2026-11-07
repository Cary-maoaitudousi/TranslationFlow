"""足迹验证：跑一章（mock）前后对比项目文件，确认只多出"该多出来"的东西。

用法：
    python tools/footprint_check.py before     # 跑之前拍快照
    python tools/footprint_check.py after      # 跑之后对比
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / ".footprint_snapshot.json"
SKIP_DIRS = {".venv311", ".git", "__pycache__", ".pytest_cache"}


def snapshot() -> set[str]:
    items: set[str] = set()
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.name == SNAP.name:
            continue
        items.add(rel.as_posix())
    return items


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "after"
    current = snapshot()
    if action == "before":
        SNAP.write_text(json.dumps(sorted(current), ensure_ascii=False), encoding="utf-8")
        print(f"已拍快照：{len(current)} 个条目")
        return 0

    if not SNAP.exists():
        print("没有快照，先跑 before")
        return 2
    before = set(json.loads(SNAP.read_text(encoding="utf-8")))
    added = sorted(current - before)
    removed = sorted(before - current)
    print(f"新增 {len(added)} 项：")
    for item in added:
        print(f"  + {item}")
    if removed:
        print(f"减少 {len(removed)} 项：")
        for item in removed:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
