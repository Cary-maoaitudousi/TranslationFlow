"""命令行驱动（无 GUI 的等价入口）。

用途：
* 批处理／定时任务（配合 Windows 计划任务在空闲时段跑，调用成本更低）；
* 端到端联调与回归测试（``--mock`` 完全不花钱）；
* 成本预演：``estimate`` 只读文档、不调 API。

示例::

    python cli.py chapters -c config.json            # 列出文档里的章节
    python cli.py estimate -c config.json --chapter 1 # 估算这一章要花多少钱
    python cli.py run -c config.json --mock           # 零成本跑通全流程
    python cli.py run -c config.json --start 1 --count 2 --auto-approve
    python cli.py report -c config.json               # 查看累计成本
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tf import ai_steps, doc_parser, prompts
from tf.config import load_config, save_config, soft_warnings, validate_config
from tf.cost import BudgetGuard, CostLog
from tf.deepseek_client import DeepSeekClient
from tf.logger import get_logger, setup_logging
from tf.pipeline import ChapterPipeline, run_loop
from tf.review import AutoReviewer

log = get_logger("main")


def _prepare(args) -> tuple:
    cfg = load_config(args.config)
    for key, value in (("MOCK_MODE", args.mock), ("AUTO_APPROVE", args.auto_approve)):
        if value:
            cfg[key] = True
    if args.start is not None:
        cfg["START_CHAPTER"] = args.start
    if args.count is not None:
        cfg["LOOP_COUNT"] = args.count
    if args.no_skip:
        cfg["SKIP_COMPLETED"] = False
    if args.model_pro:
        cfg["MODEL_PRO"] = args.model_pro
    if args.model_flash:
        cfg["MODEL_FLASH"] = args.model_flash

    setup_logging(cfg.path("LOG_DIR"), cfg.LOG_LEVEL)
    return cfg


def _load_chapters(cfg):
    return doc_parser.parse_document(cfg.path("SOURCE_DOC_PATH"), cfg.CHAPTER_HEADING_PATTERN)


def cmd_chapters(args) -> int:
    cfg = _prepare(args)
    chapters = _load_chapters(cfg)
    print(f"共 {len(chapters)} 章")
    print(f"{'#':>4}  {'章头章节号':>10}  {'段落':>5}  {'字符':>7}  Notes  章头")
    for ch in chapters:
        print(
            f"{ch.index:>4}  {str(ch.chapter_no):>10}  {ch.paragraph_count:>5}  "
            f"{len(ch.text):>7}  {'Y' if ch.has_trailing_notes else '-'}      {ch.heading}"
        )
    return 0


def cmd_estimate(args) -> int:
    cfg = _prepare(args)
    chapters = _load_chapters(cfg)
    index = args.chapter or cfg.START_CHAPTER
    if not 1 <= index <= len(chapters):
        print(f"章节序号越界：{index}（共 {len(chapters)} 章）", file=sys.stderr)
        return 2
    chapter = chapters[index - 1]
    from tf.sizing import chapter_profile, describe_profile, plan_translation, summarize

    profile = chapter_profile(chapter.text, cfg)
    _chunks, plans = plan_translation(chapter.text, cfg)
    est = ai_steps.preflight_cost_estimate(cfg, chapter.text)
    print(f"第 {index} 章：{chapter.heading}")
    print(f"体量：{describe_profile(profile)}")
    print(f"分块计划：{summarize(plans)}")
    from tf.cost import tier_of
    tier = tier_of(cfg)
    print(f"--- 成本预估（保守上界，当前档位：{'高峰' if tier == 'peak' else '空闲'}价）---")
    for name, cost in est.items():
        if name.startswith("_"):
            continue
        print(f"  {name:<16} ¥{cost:.4f}")
    print(f"总计 {cfg.LOOP_COUNT} 章约 ¥{est['合计'] * cfg.LOOP_COUNT:.4f}")
    return 0


def cmd_pricing(args) -> int:
    cfg = _prepare(args)
    from tf.cost import next_offpeak_start, price_for, tier_of

    tier = tier_of(cfg)
    print(f"当前档位：{tier}（{'高峰' if tier == 'peak' else '空闲'}价）")
    print("价目表（元 / 百万 tokens）：")
    models = [m for m in cfg.PRICE_TABLE if not m.startswith("_")]
    for model in models:
        for t in ("peak", "offpeak"):
            price = price_for(model, cfg.PRICE_TABLE, t)
            mark = " ← 当前" if t == tier else ""
            print(f"  {model:<18} {'高峰' if t == 'peak' else '空闲'}  "
                  f"命中 {price.get('input_cache_hit')}  未命中 {price.get('input_cache_miss')}  "
                  f"输出 {price.get('output')}{mark}")
    print(f"\n高峰时段：{cfg.PEAK_WINDOWS}（周 {'一二三四五六日'[cfg.PEAK_WEEKDAYS[0]]}"
          f"-{'一二三四五六日'[cfg.PEAK_WEEKDAYS[-1]]}），节假日 {len(cfg.HOLIDAYS)} 天")
    nxt = next_offpeak_start(None, cfg.PEAK_WINDOWS, cfg.PEAK_WEEKDAYS, cfg.HOLIDAYS)
    print(f"下一个空闲时段起点：{nxt:%Y-%m-%d %H:%M}")
    print(f"成本优先={cfg.COST_PRIORITY} 等待空闲时段={cfg.WAIT_FOR_OFFPEAK} "
          f"强制档位={cfg.PRICING_TIER}")
    return 0


def cmd_report(args) -> int:
    cfg = _prepare(args)
    cost_log = CostLog(cfg.path("COST_LOG_PATH"))
    total = cost_log.summary()
    print("=== 累计 ===")
    print(total.describe())
    if total.by_chapter:
        print("按章：")
        for ch in sorted(total.by_chapter):
            print(f"  第 {ch} 章  ¥{total.by_chapter[ch]:.6f}")
    if total.by_step:
        print("按步骤：")
        for step in sorted(total.by_step):
            print(f"  {step:<20} ¥{total.by_step[step]:.6f}")
    return 0


def cmd_prompts(args) -> int:
    """把提示词打印出来，方便人工审阅（不调用 API）。"""
    name = args.name or "all"
    blocks = {
        "nouns": prompts.NOUNS_SYSTEM,
        "translate": prompts.TRANSLATE_SYSTEM,
        "polish": prompts.POLISH_SYSTEM,
        "titles": prompts.TITLES_SYSTEM,
    }
    for key, text in blocks.items():
        if name in ("all", key):
            print(f"\n{'=' * 30} {key} {'=' * 30}\n{text}")
    return 0


def cmd_init(args) -> int:
    """生成 config.json 与数据目录骨架（不写入任何密钥）。"""
    root = Path(__file__).resolve().parent
    cfg = load_config(None)
    cfg["SOURCE_DOC_PATH"] = args.source or "samples/source_sample.txt"
    cfg["STYLE_SAMPLE_PATH"] = "samples/style_sample.txt"
    cfg["GLOBAL_NOUN_TABLE_PATH"] = "data/noun_table.json"
    cfg["OUTPUT_DOC_PATH"] = "output/译文.docx"
    target = save_config(cfg, args.config or (root / "config.json"))
    for rel in ("data", "output", "work", "logs"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    table = root / "data/noun_table.json"
    if not table.exists():
        table.write_text(json.dumps({"version": 1, "entries": []}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"已生成配置：{target}")
    print("下一步：复制 .env.example 为 .env 并填写 DEEPSEEK_API_KEY")
    return 0


def cmd_import_nouns(args) -> int:
    """把 "英文->中文" 形式的替换表文本导入全局专有名词表。"""
    cfg = _prepare(args)
    from tf.noun_table import NounTable, load_table_text_file

    entries, bad = load_table_text_file(args.txt)
    target = cfg.path("GLOBAL_NOUN_TABLE_PATH")
    table = NounTable(target, []) if args.replace else NounTable.load(target)
    added, skipped = table.add(entries)
    table.save()
    print(f"解析 {len(entries)} 条 → 新增 {len(added)} 条，跳过 {len(skipped)} 条"
          f"（已存在或 source 已是中文），表内现有 {len(table.entries)} 条"
          f"：{target}")
    if bad:
        print(f"⚠ 有 {len(bad)} 行无法解析（需要 '英文->中文' 格式）")
        for line in bad[:10]:
            print(f"   {line!r}")
    return 0


def cmd_review_demo(args) -> int:
    """打开三个审核窗口看效果（不调用 API、不写任何产物）。"""
    cfg = _prepare(args)
    from PySide6.QtWidgets import QApplication

    from tf.review.demo import capture_demo_png, run_demo

    app = QApplication.instance() or QApplication(sys.argv[:1])
    if args.capture:
        shots = capture_demo_png(cfg, args.capture)
        print("已生成截图：")
        for shot in shots:
            print(f"  {shot}")
    else:
        run_demo(cfg)
    _ = app
    return 0


def cmd_audit_nouns(args) -> int:
    """检查替换表在真实文本上的命中情况与潜在误伤。"""
    cfg = _prepare(args)
    from tf.audit import audit_entries, summarize
    from tf.noun_table import NounTable

    table = NounTable.load(cfg.path("GLOBAL_NOUN_TABLE_PATH"))
    chapters = _load_chapters(cfg)
    span = chapters[: max(1, args.chapters)]
    text = "\n\n".join(c.text for c in span)
    audits = audit_entries(table.as_entries(), text)
    info = summarize(audits)

    print(f"检查范围：前 {len(span)} 章，{len(text.split())} 词")
    print(f"表内 {info['entries']} 条 → 命中 {info['used']} 条，未命中 {info['unused']} 条，"
          f"总替换 {info['total_hits']} 处")
    print("\n命中最多：")
    for a in info["top"]:
        print(f"  {a.hits:>4} × {a.source}->{a.target}  [{a.shape}]")
    if info["risky_used"]:
        print("\n⚠ 疑似误伤（全小写通用词被当独立单词替换）：")
        for a in info["risky_used"]:
            print(f"  {a.hits:>3} × {a.source}->{a.target}")
            for ctx in a.contexts:
                print(f"       …{ctx}…")
        print("  建议：把这类后缀词从表里删掉，交给 AI 命名规则处理整名。")
    return 0


def cmd_export_nouns(args) -> int:
    """把 JSON 替换表导出成 ``英文->中文`` 文本（便于 diff / 备份）。"""
    from tf.noun_table import NounTable, export_table_text

    cfg = _prepare(args)
    table = NounTable.load(cfg.path("GLOBAL_NOUN_TABLE_PATH"))
    target = Path(args.txt) if args.txt else cfg.path("GLOBAL_NOUN_TABLE_PATH").with_suffix(".txt")
    target.write_text(export_table_text(table.entries) + "\n", encoding="utf-8")
    print(f"已导出 {len(table.entries)} 条到 {target}")
    return 0


def cmd_holidays(args) -> int:
    """查看/设置法定节假日（只影响峰谷计价）。"""
    cfg = _prepare(args)
    from tf.cost import next_offpeak_start, tier_of

    if args.set is not None:
        raw = args.set.replace("，", ",").replace(" ", ",").replace(";", ",")
        dates: list[str] = []
        bad: list[str] = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                import datetime as dt

                dt.date.fromisoformat(token)
            except ValueError:
                bad.append(token)
                continue
            dates.append(token)
        cfg["HOLIDAYS"] = sorted(set(dates))
        target = save_config(cfg, args.config)
        print(f"已写入 {len(set(dates))} 天节假日到 {target}")
        if bad:
            print(f"⚠ 忽略格式不正确的 {len(bad)} 项：{bad[:5]}")

    holidays = cfg.get("HOLIDAYS") or []
    tier = tier_of(cfg)
    print(f"当前档位：{tier}（{'高峰' if tier == 'peak' else '空闲'}价）")
    print(f"高峰时段：{cfg.PEAK_WINDOWS}（工作日 {cfg.PEAK_WEEKDAYS}）")
    print(f"法定节假日：{len(holidays)} 天")
    for day in holidays:
        print(f"  {day}")
    nxt = next_offpeak_start(None, cfg.PEAK_WINDOWS, cfg.PEAK_WEEKDAYS, holidays)
    print(f"下一个空闲时段起点：{nxt:%Y-%m-%d %H:%M}")
    if not holidays:
        print("\n提示：节假日按工作日作息计价会略微高估成本。查「国务院办公厅 节假日安排」后执行：")
        print('  python cli.py holidays --set "2026-01-01,2026-02-17,2026-10-01"')
    return 0


def cmd_cache(args) -> int:
    """查看/清空本地请求缓存（清空只影响重跑成本，不影响已有译文）。"""
    import shutil

    cfg = _prepare(args)
    cache_dir = cfg.path("CACHE_DIR")
    files = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
    print(f"缓存目录：{cache_dir}")
    print(f"缓存条目：{len(files)} 个（CACHE_ENABLED={cfg.CACHE_ENABLED}）")
    if args.clear:
        shutil.rmtree(cache_dir, ignore_errors=True)
        print("已清空。之后重跑同一章会重新调用 API。")
    return 0


def cmd_run(args) -> int:
    cfg = _prepare(args)
    problems = validate_config(cfg)
    if problems:
        print("配置校验未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    for w in soft_warnings(cfg):
        print(f"[提醒] {w}")

    chapters = _load_chapters(cfg)
    start, count = int(cfg.START_CHAPTER), int(cfg.LOOP_COUNT)
    selected = chapters[start - 1: start - 1 + count]
    if not selected:
        print(f"起始章 {start} 超出范围（共 {len(chapters)} 章）", file=sys.stderr)
        return 2

    if not cfg.MOCK_MODE:
        est = sum(ai_steps.preflight_cost_estimate(cfg, ch.text)["合计"] for ch in selected)
        print(f"本次将处理 {len(selected)} 章，预估成本上界 ¥{est:.4f}"
              f"（缓存命中与空闲时段会显著更低）")
        if not args.yes:
            answer = input("继续调用真实 API？[y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("已取消（未产生任何费用）")
                return 1

    cost_log = CostLog(cfg.path("COST_LOG_PATH"))
    budget = BudgetGuard(cost_log, cfg.BUDGET_TOTAL_CNY, cfg.BUDGET_PER_CHAPTER_CNY)
    client = DeepSeekClient(cfg, cost_log, budget)
    reviewer = AutoReviewer() if cfg.AUTO_APPROVE else _qt_reviewer()
    pipeline = ChapterPipeline(cfg, cost_log, client, reviewer, emit=_cli_event)

    results = run_loop(cfg, chapters, pipeline, start, count, emit=_cli_event)

    print("\n=== 结果 ===")
    for res in results:
        flag = {"ok": "✔", "failed": "✘", "skipped": "→", "cancelled": "‖"}.get(res.status, "?")
        print(f"{flag} 第 {res.index} 章（第 {res.label} 章）{res.title} | ¥{res.cost_cny:.6f} | {res.duration_s:.0f}s")
        for err in res.errors:
            print(f"    ! {err}")
    total = cost_log.summary()
    print(f"\n累计：{total.describe()}")
    print(f"成本明细：{cost_log.path}")
    return 0 if all(r.status in ("ok", "skipped") for r in results) else 1


def _qt_reviewer():
    from PySide6.QtWidgets import QApplication

    from tf.review import QtReviewer

    app = QApplication.instance() or QApplication(sys.argv[:1])
    return QtReviewer()


def _cli_event(kind: str, payload: dict) -> None:
    if kind == "step_start":
        print(f"  → 步骤{payload.get('step')} {payload.get('name')} …", flush=True)
    elif kind == "step_done":
        pass
    elif kind == "step_failed":
        print(f"  ✘ 步骤{payload.get('step')} 失败：{payload.get('error')}", flush=True)
    elif kind == "chapter_start":
        print(f"\n[第 {payload.get('chapter_index')} 章] {payload.get('heading')}", flush=True)
    elif kind == "progress":
        print(f"    {payload.get('message')}", flush=True)
    elif kind == "chapter_done":
        print(
            f"[第 {payload.get('chapter_index')} 章] {payload.get('status')} "
            f"《{payload.get('title')}》 ¥{payload.get('cost'):.6f}",
            flush=True,
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cli.py", description="猫武士同人翻译流水线 CLI")
    p.add_argument("-c", "--config", default=None, help="配置文件路径（默认 config.json）")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("-c", "--config", dest="config_sub", default=None,
                        help="配置文件路径（也可写在子命令前面）")
        sp.add_argument("--mock", action="store_true", help="不调用真实 API（零成本）")
        sp.add_argument("--auto-approve", action="store_true", help="审核窗口自动全部通过")
        sp.add_argument("--start", type=int, default=None, help="起始章（文档内第几个章头）")
        sp.add_argument("--count", type=int, default=None, help="循环章数")
        sp.add_argument("--no-skip", action="store_true", help="不跳过已完成步骤")
        sp.add_argument("--model-pro", default=None, help="覆盖 MODEL_PRO")
        sp.add_argument("--model-flash", default=None, help="覆盖 MODEL_FLASH")

    sp = sub.add_parser("chapters", help="列出源文档中的章节")
    add_common(sp)
    sp.set_defaults(func=cmd_chapters)

    sp = sub.add_parser("estimate", help="预估某章成本（不调 API）")
    add_common(sp)
    sp.add_argument("--chapter", type=int, default=None)
    sp.set_defaults(func=cmd_estimate)

    sp = sub.add_parser("run", help="执行流水线")
    add_common(sp)
    sp.add_argument("-y", "--yes", action="store_true", help="跳过付费确认")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("report", help="查看累计成本")
    add_common(sp)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("pricing", help="查看计价表")
    add_common(sp)
    sp.set_defaults(func=cmd_pricing)

    sp = sub.add_parser("prompts", help="打印提示词（便于审阅）")
    add_common(sp)
    sp.add_argument("--name", choices=["all", "nouns", "translate", "polish", "titles"], default="all")
    sp.set_defaults(func=cmd_prompts)

    sp = sub.add_parser("import-nouns", help="导入 英文->中文 替换表文本")
    add_common(sp)
    sp.add_argument("--txt", required=True, help="替换表文本路径")
    sp.add_argument("--replace", action="store_true", help="覆盖而非合并（默认合并）")
    sp.set_defaults(func=cmd_import_nouns)

    sp = sub.add_parser("review-demo", help="打开审核窗口看效果（不调 API）")
    add_common(sp)
    sp.add_argument("--capture", default=None, help="不弹窗，渲染成 PNG 到指定目录")
    sp.set_defaults(func=cmd_review_demo)

    sp = sub.add_parser("audit-nouns", help="检查替换表在正文上的命中与误伤")
    add_common(sp)
    sp.add_argument("--chapters", type=int, default=5, help="抽样章数（默认 5）")
    sp.set_defaults(func=cmd_audit_nouns)

    sp = sub.add_parser("export-nouns", help="把替换表导出成 TXT")
    add_common(sp)
    sp.add_argument("--txt", default=None, help="输出路径（默认同名 .txt）")
    sp.set_defaults(func=cmd_export_nouns)

    sp = sub.add_parser("holidays", help="查看/设置法定节假日（影响峰谷计价）")
    add_common(sp)
    sp.add_argument("--set", default=None, help='设置，如 "2026-01-01,2026-02-17"')
    sp.set_defaults(func=cmd_holidays)

    sp = sub.add_parser("cache", help="查看/清空本地请求缓存")
    add_common(sp)
    sp.add_argument("--clear", action="store_true", help="清空缓存")
    sp.set_defaults(func=cmd_cache)

    sp = sub.add_parser("init", help="生成 config.json 与目录骨架")
    sp.add_argument("--source", default=None)
    sp.set_defaults(func=cmd_init)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # 允许 `-c` 写在子命令前面或后面
    args.config = getattr(args, "config_sub", None) or args.config
    if not hasattr(args, "mock"):
        args.mock = False
    args.auto_approve = getattr(args, "auto_approve", False)
    args.start = getattr(args, "start", None)
    args.count = getattr(args, "count", None)
    args.no_skip = getattr(args, "no_skip", False)
    args.model_pro = getattr(args, "model_pro", None)
    args.model_flash = getattr(args, "model_flash", None)
    try:
        return int(args.func(args) or 0)
    except doc_parser.ParseError as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
