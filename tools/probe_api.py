"""API 连通性自检（两份最小请求，验证鉴权、JSON Output、思考模式参数）。

用法（不要把密钥写进文件）::

    $env:DEEPSEEK_API_KEY='sk-...'
    .\.venv311\Scripts\python.exe tools\probe_api.py -c config.json

只会产生极小的费用（几百 token 量级）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tf.config import load_config  # noqa: E402
from tf.cost import CostLog  # noqa: E402
from tf.deepseek_client import DeepSeekClient, classify_error  # noqa: E402
from tf.logger import setup_logging  # noqa: E402

SYSTEM = """你是测试助手。只输出一个 JSON 对象，不要任何解释文字。
输出结构：{"ok": true, "echo": "你收到的内容"}"""


def main(argv: list[str]) -> int:
    cfg_path = "config.json"
    if "-c" in argv:
        cfg_path = argv[argv.index("-c") + 1]
    cfg = load_config(cfg_path)
    setup_logging(cfg.path("LOG_DIR"), "INFO")
    cost_log = CostLog(cfg.path("WORK_DIR") / "cost_log.csv")
    client = DeepSeekClient(cfg, cost_log)

    probes = [
        ("flash-无思考", cfg.MODEL_FLASH, False),
        ("pro-思考", cfg.MODEL_PRO, True),
    ]
    for name, model, thinking in probes:
        print(f"\n=== {name}（{model}，thinking={thinking}）===")
        try:
            result = client.call_json(
                step_name=f"probe-{name}", chapter_index=0,
                system=SYSTEM, user="请回显：探针 OK",
                schema_key="titles", model=model, temperature=0.0,
                max_tokens=400, thinking=thinking,
                reasoning_effort="high" if thinking else None,
                use_cache=False,
            )
            print(f"  JSON 解析：{'OK' if result.json_parse_ok else '降级'}")
            print(f"  返回：{str(result.data)[:200]}")
            print(f"  用量：输入 {result.prompt_tokens} tok"
                  f"（缓存命中 {result.cache_hit_tokens}）| 输出 {result.completion_tokens} tok")
            print(f"  费用：¥{result.cost_cny:.6f} | 耗时 {result.latency_ms} ms")
        except Exception as exc:  # noqa: BLE001
            kind, status = classify_error(exc)
            print(f"  ✘ 失败（{kind}, status={status}）：{exc}")
            if kind == "fatal":
                return 2
    total = cost_log.summary(0)
    print(f"\n探针合计：¥{total.cost_cny:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
