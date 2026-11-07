"""干净运行模式测试：成功时只留 Word + 替换表 + 完成记录。

用户要求：正常跑通时，理论上只有"输出的 Word"和"更新后的替换表"会变。
测试里把 CLEAN_INTERMEDIATES 打开，验证 work/chapter_n 不残留、completed.json 可幂等复用。
"""

from __future__ import annotations

import json

from docx import Document

from tf.cost import BudgetGuard, CostLog
from tf.deepseek_client import DeepSeekClient
from tf.doc_parser import parse_document
from tf.pipeline import ChapterPipeline, run_loop
from tf.review import AutoReviewer


def _run(cfg, count=1):
    cost_log = CostLog(cfg.path("COST_LOG_PATH"))
    budget = BudgetGuard(cost_log, 0, 0)
    client = DeepSeekClient(cfg, cost_log, budget)
    pipeline = ChapterPipeline(cfg, cost_log, client, AutoReviewer())
    chapters = parse_document(cfg.path("SOURCE_DOC_PATH"))
    return run_loop(cfg, chapters, pipeline, 1, count), pipeline


def _tree(path) -> set[str]:
    return {p.name for p in path.iterdir()} if path.exists() else set()


def test_success_leaves_only_word_table_and_completed_record(cfg_factory, noun_table_file):
    cfg = cfg_factory(
        GLOBAL_NOUN_TABLE_PATH=str(noun_table_file),
        CLEAN_INTERMEDIATES=True,
    )
    results, _ = _run(cfg, count=1)
    assert results[0].status == "ok"

    work = cfg.path("WORK_DIR")
    assert not (work / "chapter_1").exists(), "成功章节不应残留中间产物"
    progress = cfg.path("PROGRESS_PATH")
    assert progress.exists()
    completed = json.loads(progress.read_text(encoding="utf-8"))
    assert "1" in completed["chapters"]
    assert completed["chapters"]["1"]["title"]

    # Word 与替换表是唯一实质产物
    assert cfg.path("OUTPUT_DOC_PATH").exists()
    assert noun_table_file.exists()
    doc = Document(str(cfg.path("OUTPUT_DOC_PATH")))
    assert [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]


def test_second_run_is_skipped_without_any_new_files(cfg_factory, noun_table_file):
    cfg = cfg_factory(GLOBAL_NOUN_TABLE_PATH=str(noun_table_file), CLEAN_INTERMEDIATES=True)
    _run(cfg, count=1)
    work = cfg.path("WORK_DIR")
    before = _tree(work) if work.exists() else set()
    docx_before = cfg.path("OUTPUT_DOC_PATH").stat().st_size

    results, _ = _run(cfg, count=1)
    assert results[0].status == "skipped"
    assert (_tree(work) if work.exists() else set()) == before
    assert cfg.path("OUTPUT_DOC_PATH").stat().st_size == docx_before


def test_failed_chapter_keeps_intermediates_for_debugging(cfg_factory, tmp_path, noun_table_file):
    src = tmp_path / "bad.txt"
    src.write_text("Chapter 9\n\nChapter Text\n\n\n", encoding="utf-8")
    cfg = cfg_factory(
        SOURCE_DOC_PATH=str(src),
        GLOBAL_NOUN_TABLE_PATH=str(noun_table_file),
        CLEAN_INTERMEDIATES=True,
    )
    results, _ = _run(cfg, count=1)
    assert results[0].status == "failed"
    # 失败时保留现场：raw/summary 都在
    chapter_dir = cfg.path("WORK_DIR") / "chapter_1"
    assert chapter_dir.exists()
    assert (chapter_dir / "chapter_1_raw.txt").exists()
    assert (chapter_dir / "chapter_1_summary.json").exists()
    progress = cfg.path("PROGRESS_PATH")
    assert not progress.exists() or '"1"' not in progress.read_text(encoding="utf-8")


def test_clean_mode_off_keeps_everything(cfg_factory, noun_table_file):
    cfg = cfg_factory(GLOBAL_NOUN_TABLE_PATH=str(noun_table_file), CLEAN_INTERMEDIATES=False)
    _run(cfg, count=1)
    chapter_dir = cfg.path("WORK_DIR") / "chapter_1"
    assert (chapter_dir / "chapter_1_final_chinese.txt").exists()
