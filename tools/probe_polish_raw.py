"""调试：把润色提示词发给模型，原样打印返回内容。

用法::

    python tools\\probe_polish_raw.py [译文文件] [phrase|sentence]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tf import prompts  # noqa: E402
from tf.config import load_config  # noqa: E402
from tf.cost import CostLog  # noqa: E402
from tf.deepseek_client import DeepSeekClient  # noqa: E402
from tf.logger import setup_logging  # noqa: E402

FALLBACK = (
    "两人并肩走进空地，谁都没想到这一幕。\n\n"
    "他一拳砸在石头上，指节渗出血来。\n\n"
    "众猫面面相觑，四目相对，谁也不敢先开口。"
)


def main(argv: list[str]) -> int:
    cfg = load_config("config.json")
    setup_logging(cfg.path("LOG_DIR"), "INFO")
    client = DeepSeekClient(cfg, CostLog(cfg.path("WORK_DIR") / "cost_log.csv"))

    path = Path(argv[0]) if argv else None
    text = path.read_text(encoding="utf-8") if path and path.exists() else FALLBACK
    mode = argv[1] if len(argv) > 1 else "phrase"

    if mode == "phrase":
        system, user, schema = prompts.POLISH_PHRASE_SYSTEM, prompts.render_polish_phrase_user(text), "polish_phrase"
        max_tokens = 1600
    else:
        system, user, schema = prompts.POLISH_SYSTEM, prompts.render_polish_user(text), "polish"
        max_tokens = 4000

    result = client.call_json(
        step_name="probe-polish", chapter_index=0, system=system, user=user,
        schema_key=schema, model=str(cfg.MODEL_POLISH or cfg.MODEL_PRO),
        temperature=0.1, max_tokens=max_tokens, thinking=False, use_cache=False,
    )
    print(f"模式={mode} 输入长度={len(text)} 输出tok={result.completion_tokens} "
          f"finish={result.finish_reason} 费用=¥{result.cost_cny:.4f}")
    print("--- 原始返回 ---")
    print(result.raw[:4000])
    entries = result.data.get("entries", []) if isinstance(result.data, dict) else []
    print(f"--- 解析出 {len(entries)} 条 ---")
    for e in entries[:25]:
        src = e.get("source") or e.get("phrase")
        tgt = e.get("target") or e.get("replacement")
        same = "相同⚠" if src == tgt else "改写"
        print(f"[{same}] {src!r} → {tgt!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
