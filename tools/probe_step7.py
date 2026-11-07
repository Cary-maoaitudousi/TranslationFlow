"""单步探针：对指定译文跑一次"润色检测"，验证真实模型是否返回可用条目。

用法::

    $env:DEEPSEEK_API_KEY='sk-...'
    python tools\\probe_step7.py work\\run3 1

会在译文末尾注入几句带人类特征的句子，检查模型能否把它们找出来。
费用约 ¥0.03。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tf import ai_steps  # noqa: E402
from tf.config import load_config  # noqa: E402
from tf.cost import CostLog  # noqa: E402
from tf.deepseek_client import DeepSeekClient  # noqa: E402
from tf.logger import setup_logging  # noqa: E402

INJECT = (
    "\n\n两人并肩走进空地，谁都没想到这一幕。"
    "\n\n他一拳砸在石头上，指节渗出血来。"
    "\n\n众猫面面相觑，四目相对，谁也不敢先开口。"
)


def main(argv: list[str]) -> int:
    base = argv[0] if argv else "work/run3"
    idx = argv[1] if len(argv) > 1 else "1"
    cfg = load_config("config.json")
    setup_logging(cfg.path("LOG_DIR"), "INFO")
    client = DeepSeekClient(cfg, CostLog(cfg.path("WORK_DIR") / "cost_log.csv"))

    path = Path(base) / f"chapter_{idx}" / f"chapter_{idx}_translated.txt"
    text = path.read_text(encoding="utf-8") + INJECT
    entries, report = ai_steps.detect_cultural_issues(client, cfg, text, chapter_index=0)

    print(f"\n候选 {len(entries)} 条，调用 {report.calls} 次，费用 ¥{report.cost_cny:.4f}")
    for e in entries:
        print(f"\n  原文：{e['source']}")
        print(f"  替换：{e['target']}")
        print(f"  说明：{e.get('context', '')}")
        if e.get("_missing"):
            print("  ⚠ 在译文中找不到该 source")
    injected = [e for e in entries if any(k in e["source"] for k in ("两人", "一拳", "四目", "面面相觑"))]
    print(f"\n注入句命中：{len(injected)}/3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
