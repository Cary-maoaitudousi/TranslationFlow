"""端到端流水线测试（验收标准 §12.7 / §12.9 / §12.11 / §12.13）。

MOCK_MODE 下跑完 11 个步骤，检查产物、Word 输出、成本日志与幂等重跑。
"""

from __future__ import annotations

import json

from docx import Document

from tf.cost import BudgetGuard, CostLog
from tf.deepseek_client import DeepSeekClient
from tf.doc_parser import parse_document
from tf.pipeline import ChapterPipeline, run_loop
from tf.replacer import mode_a_replace
from tf.review import AutoReviewer


def _run(cfg, start=1, count=2):
    cost_log = CostLog(cfg.path("WORK_DIR") / "cost_log.csv")
    budget = BudgetGuard(cost_log, cfg.BUDGET_TOTAL_CNY, cfg.BUDGET_PER_CHAPTER_CNY)
    client = DeepSeekClient(cfg, cost_log, budget)
    pipeline = ChapterPipeline(cfg, cost_log, client, AutoReviewer())
    chapters = parse_document(cfg.path("SOURCE_DOC_PATH"))
    return run_loop(cfg, chapters, pipeline, start, count), cost_log


def test_two_chapters_end_to_end(cfg_factory, noun_table_file):
    cfg = cfg_factory(GLOBAL_NOUN_TABLE_PATH=str(noun_table_file))
    results, cost_log = _run(cfg)

    assert [r.status for r in results] == ["ok", "ok"]
    assert cost_log.rows()

    # --- 步骤 1 产物 ---
    work = cfg.path("WORK_DIR")
    raw1 = work / "chapter_1" / "chapter_1_raw.txt"
    assert raw1.exists()
    assert "Firestar" in raw1.read_text(encoding="utf-8")      # 原文保留英文名
    meta = json.loads((work / "chapter_1" / "chapter_1_meta.json").read_text(encoding="utf-8"))
    assert meta["heading"].startswith("Chapter 100")

    # --- 步骤 2：初步替换（只有全局表）---
    replaced1 = (work / "chapter_1" / "chapter_1_replaced.txt").read_text(encoding="utf-8")
    assert "火星" in replaced1 and "Firestar" not in replaced1
    assert "Thistleclaw" in replaced1                          # 新名字此时还没替换

    # --- 步骤 5：二次替换以 raw 为基准 ---
    final_mixed = (work / "chapter_1" / "chapter_1_final_mixed.txt").read_text(encoding="utf-8")
    table = json.loads(noun_table_file.read_text(encoding="utf-8"))["entries"]
    expected = mode_a_replace(raw1.read_text(encoding="utf-8"), table).text_out
    assert final_mixed == expected
    assert "Thistleclaw" not in final_mixed                    # 新名字已被替换

    # --- 步骤 6~9 中文产物 ---
    translated = (work / "chapter_1" / "chapter_1_translated.txt").read_text(encoding="utf-8")
    assert translated.strip()
    assert (work / "chapter_1" / "chapter_1_final_chinese.txt").exists()

    # --- 步骤 10 章名 ---
    titles = json.loads((work / "chapter_1" / "chapter_1_titles.json").read_text(encoding="utf-8"))
    assert len(titles["titles"]) == 5

    # --- 步骤 11 Word ---
    doc_path = cfg.path("OUTPUT_DOC_PATH")
    doc = Document(str(doc_path))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert len(headings) == 2
    assert headings[0].startswith("第 100 章")
    assert headings[1].startswith("第 101 章")
    assert "章" in headings[0]
    assert any("w:type=\"page\"" in p._p.xml for p in doc.paragraphs)

    # --- 章节汇总 ---
    summary = json.loads((work / "chapter_1" / "chapter_1_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ok"
    assert len(summary["steps"]) == 11

    # --- 成本日志：mock 不花钱 ---
    rows = cost_log.rows()
    assert all(float(r["cost_cny"]) == 0.0 for r in rows)
    assert all(r["cache_source"] == "mock" for r in rows)


def test_rerun_with_skip_completed_costs_nothing(cfg_factory, noun_table_file):
    cfg = cfg_factory(GLOBAL_NOUN_TABLE_PATH=str(noun_table_file))
    results1, cost_log = _run(cfg)
    rows_first = len(cost_log.rows())
    doc_path = cfg.path("OUTPUT_DOC_PATH")
    docx_size = doc_path.stat().st_size

    results2, cost_log2 = _run(cfg)
    rows_second = len(cost_log2.rows())
    assert rows_second == rows_first          # 没有任何新的 AI 调用
    # 已完成的章节直接早退，不再走 11 步
    assert [r.status for r in results2] == ["skipped", "skipped"]
    assert all(r.steps == [] for r in results2)
    assert doc_path.stat().st_size == docx_size   # Word 没有被重复追加


def test_no_skip_forces_full_rerun(cfg_factory, noun_table_file):
    cfg = cfg_factory(GLOBAL_NOUN_TABLE_PATH=str(noun_table_file), SKIP_COMPLETED=False)
    _run(cfg, start=1, count=1)
    results, _ = _run(cfg, start=1, count=1)
    assert all(s.status != "skipped" for s in results[0].steps)


def test_chapter_label_mode_loop(cfg_factory, noun_table_file):
    cfg = cfg_factory(GLOBAL_NOUN_TABLE_PATH=str(noun_table_file), CHAPTER_LABEL_MODE="loop")
    _run(cfg, start=1, count=2)
    doc = Document(str(cfg.path("OUTPUT_DOC_PATH")))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert headings[0].startswith("第 1 章")
    assert headings[1].startswith("第 2 章")


def test_empty_chapter_is_reported_not_crashed(cfg_factory, tmp_path, noun_table_file):
    src = tmp_path / "empty.txt"
    src.write_text("Chapter 5\n\nChapter Text\n\n\nChapter 6\n\nChapter Text\n\nReal text.\n",
                   encoding="utf-8")
    cfg = cfg_factory(SOURCE_DOC_PATH=str(src), GLOBAL_NOUN_TABLE_PATH=str(noun_table_file))
    results, _ = _run(cfg, start=1, count=1)
    assert results[0].status == "failed"
    assert any("正文为空" in e for e in results[0].errors)
