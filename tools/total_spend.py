"""汇总某目录下所有 cost_log.csv 的实际花费。

用法：python tools/total_spend.py work
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tf.cost import CostLog  # noqa: E402


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else "work")
    files = sorted(root.rglob("cost_log.csv"))
    total = 0.0
    in_tok = out_tok = calls = 0
    for f in files:
        s = CostLog(f).summary()
        total += s.cost_cny
        in_tok += s.prompt_tokens
        out_tok += s.completion_tokens
        calls += s.calls
        print(f"{str(f):<52} ¥{s.cost_cny:>9.4f}  {s.calls:>3} 次")
    print(f"\n合计：{calls} 次调用 | 输入 {in_tok} tok | 输出 {out_tok} tok | ¥{total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
