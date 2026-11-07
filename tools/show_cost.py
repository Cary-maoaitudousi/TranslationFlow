"""查看成本明细：按行 + 按步骤汇总。

用法::

    .\\.venv311\\Scripts\\python.exe tools\\show_cost.py work\\run1\\cost_log.csv [--chapter 1]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tf.cost import CostLog  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = Path(argv[0])
    chapter = None
    if "--chapter" in argv:
        chapter = int(argv[argv.index("--chapter") + 1])

    log = CostLog(path)
    rows = log.rows()
    if chapter is not None:
        rows = [r for r in rows if str(r["chapter_index"]) == str(chapter)]

    header = ["step", "model", "in", "hit", "out", "retry", "finish", "cost"]
    print(f"{header[0]:<24}{header[1]:<22}{header[2]:>8}{header[3]:>8}"
          f"{header[4]:>8}{header[5]:>7}{header[6]:>10}{header[7]:>10}")
    by_step: dict[str, float] = defaultdict(float)
    for r in rows:
        print(f"{r['step_name']:<24}{r['model']:<22}{r['prompt_tokens']:>8}"
              f"{r['cache_hit_tokens']:>8}{r['completion_tokens']:>8}"
              f"{r['retry_count']:>7}{r['finish_reason']:>10}"
              f"{float(r['cost_cny']):>10.4f}")
        by_step[r["step_name"]] += float(r["cost_cny"])

    total = sum(by_step.values())
    in_tok = sum(int(r["prompt_tokens"]) for r in rows)
    out_tok = sum(int(r["completion_tokens"]) for r in rows)
    hit_tok = sum(int(r["cache_hit_tokens"]) for r in rows)
    print(f"\n合计：{len(rows)} 次调用 | 输入 {in_tok} tok（缓存命中 {hit_tok}）"
          f" | 输出 {out_tok} tok | ¥{total:.4f}")
    for step, cost in sorted(by_step.items(), key=lambda kv: -kv[1]):
        print(f"  {step:<24} ¥{cost:.4f}  ({cost / total * 100 if total else 0:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
