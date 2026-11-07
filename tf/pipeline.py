"""单章流水线与循环控制（需求书 §8.2）。

每一步的产物落盘在 ``work/chapter_{n}/`` 下，文件名遵循附录 B；``SKIP_COMPLETED``
开启时，已有的产物会被直接复用，从而实现"重跑不重复花 token"的幂等性。
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import ai_steps, word_writer
from .config import Config
from .cost import BudgetExceeded, CostLog, is_peak, next_offpeak_start, tier_of
from .deepseek_client import ApiFatalError, DeepSeekClient
from .doc_parser import Chapter, save_chapter_artifacts
from .logger import get_logger
from .noun_table import NounTable
from .replacer import mode_a_replace, mode_b_replace
from .review import ReviewCancelled
from .sizing import chapter_profile, describe_profile, plan_translation

log = get_logger("main")

EventFn = Callable[[str, dict], None]


def _noop_event(_kind: str, _payload: dict) -> None:
    return None

@dataclass
class StepRecord:
    step: int
    name: str
    status: str          # done / skipped / failed
    duration_s: float = 0.0
    detail: str = ""
    cost_cny: float = 0.0
    items: int = 0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0
    fallback: bool = False

    def absorb(self, report) -> None:
        """吸收 ai_steps.StepReport 里的 token / 重试统计（A2）。"""
        self.calls = report.calls
        self.prompt_tokens = report.prompt_tokens
        self.completion_tokens = report.completion_tokens
        self.retries = report.retries
        self.fallback = report.fallback
        self.cost_cny = report.cost_cny
        self.items = report.items

    def as_dict(self) -> dict:
        return {
            "step": self.step, "name": self.name, "status": self.status,
            "duration_s": round(self.duration_s, 2), "detail": self.detail,
            "cost_cny": self.cost_cny, "items": self.items,
            "calls": self.calls, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retries": self.retries, "json_fallback": self.fallback,
        }


@dataclass
class ReviewStats:
    shown: int = 0       # 送审条数
    approved: int = 0    # 通过条数
    modified: int = 0    # 其中被人工改过译名/替换文本的条数
    skipped: int = 0     # 未通过 / 跳过

    def as_dict(self) -> dict:
        return {"shown": self.shown, "approved": self.approved,
                "modified": self.modified, "skipped": self.skipped}


def diff_modified(shown: list[dict], approved: list[dict], key: str = "target") -> int:
    """统计审核后被人工改动过的条目数（同 source 对比 target）。"""
    original = {str(e.get("source", "")): str(e.get(key, "") or "") for e in shown}
    modified = 0
    for entry in approved:
        src = str(entry.get("source", ""))
        if src in original and str(entry.get(key, "") or "") != original[src]:
            modified += 1
    return modified


@dataclass
class ChapterResult:
    index: int
    label: int
    status: str = "ok"        # ok / skipped / failed / cancelled
    title: str = ""
    steps: list[StepRecord] = field(default_factory=list)
    cost_cny: float = 0.0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)
    review: dict[str, dict] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "chapter_index": self.index, "chapter_label": self.label,
            "status": self.status, "title": self.title,
            "cost_cny": self.cost_cny, "duration_s": round(self.duration_s, 2),
            "errors": self.errors,
            "review": self.review,
            "tokens": self.tokens,
            "retries_total": sum(s.retries for s in self.steps),
            "steps": [s.as_dict() for s in self.steps],
        }


class ChapterPipeline:
    """把一章从英文原文跑到中文 Word。"""

    def __init__(
        self,
        cfg: Config,
        cost_log: CostLog,
        client: DeepSeekClient,
        reviewer,
        emit: EventFn = _noop_event,
        pause_event: threading.Event | None = None,
    ):
        self.cfg = cfg
        self.cost_log = cost_log
        self.client = client
        self.reviewer = reviewer
        self.emit = emit
        self.pause_event = pause_event or threading.Event()
        self.pause_event.set()          # set = 运行中
        self._stop = threading.Event()
        self.noun_table = NounTable.load(cfg.path("GLOBAL_NOUN_TABLE_PATH"))
        self.style_sample, self.style_is_excerpt = ai_steps.load_style_sample(cfg)
        self._completed: dict[str, dict] = self._load_completed()

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        self.pause_event.set()

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _checkpoint(self) -> None:
        """步骤边界：暂停 / 停止检查。"""
        while not self.pause_event.wait(timeout=0.2):
            if self._stop.is_set():
                break
        if self._stop.is_set():
            raise ReviewCancelled("用户停止流水线")

    def _emit(self, kind: str, **payload) -> None:
        payload.setdefault("chapter", payload.get("chapter_index"))
        try:
            self.emit(kind, payload)
        except Exception as exc:  # noqa: BLE001 - UI 回调不应影响流水线
            log.warning(f"事件回调异常（已忽略）：{exc}")

    # ------------------------------------------------------------------
    def wait_for_offpeak(self) -> None:
        """成本优先时，批量任务等到空闲时段再开始（可被暂停/停止打断）。"""
        if not (self.cfg.get("COST_PRIORITY") and self.cfg.get("WAIT_FOR_OFFPEAK")):
            return
        peak_windows = self.cfg.get("PEAK_WINDOWS") or []
        weekdays = self.cfg.get("PEAK_WEEKDAYS") or []
        holidays = self.cfg.get("HOLIDAYS") or []
        if not is_peak(None, peak_windows, weekdays, holidays):
            return
        target = next_offpeak_start(None, peak_windows, weekdays, holidays)
        wait_s = max(0.0, (target - dt.datetime.now()).total_seconds())
        if wait_s <= 0:
            return
        log.warning(
            f"当前为高峰时段，成本优先：等待至 {target:%m-%d %H:%M} 的空闲时段再继续"
            f"（约 {wait_s / 60:.0f} 分钟）"
        )
        self._emit("waiting_offpeak", chapter_index=None,
                   target=target.isoformat(timespec="minutes"), seconds=int(wait_s))
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if self._stop.is_set():
                raise ReviewCancelled("等待空闲时段时被停止")
            self._checkpoint()
            time.sleep(min(5.0, max(0.1, end - time.monotonic())))

    # ------------------------------------------------------------------
    # -- 完成记录 / 产物清理 -------------------------------------------
    def _completed_path(self) -> Path:
        return self.cfg.path("PROGRESS_PATH")

    def _load_completed(self) -> dict[str, dict]:
        path = self._completed_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("chapters", {}) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            log.warning(f"completed.json 解析失败，按空记录继续：{path}")
            return {}

    def _record_completed(self, index: int, label: int, title: str) -> None:
        self._completed[str(index)] = {
            "label": label, "title": title,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        path = self._completed_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"chapters": self._completed}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            log.warning(f"写完成记录失败：{exc}")

    def _cleanup_chapter_files(self, chapter_dir: Path) -> None:
        import shutil

        try:
            shutil.rmtree(chapter_dir, ignore_errors=True)
            # 顺带把空的 work/ 也收掉，避免留下空目录
            work_root = self.cfg.path("WORK_DIR")
            if work_root.exists() and not any(work_root.iterdir()):
                work_root.rmdir()
            log.info(f"已清理本章中间产物：{chapter_dir}")
        except OSError as exc:  # pragma: no cover
            log.warning(f"清理中间产物失败（不影响结果）：{exc}")

    def _paths(self, index: int) -> dict[str, Path]:
        d = self.cfg.path("WORK_DIR") / f"chapter_{index}"
        d.mkdir(parents=True, exist_ok=True)
        p = {k: d / v for k, v in {
            "dir": "",
            "raw": f"chapter_{index}_raw.txt",
            "meta": f"chapter_{index}_meta.json",
            "replaced": f"chapter_{index}_replaced.txt",
            "new_nouns": f"chapter_{index}_new_nouns.json",
            "approved_nouns": f"chapter_{index}_approved_nouns.json",
            "final_mixed": f"chapter_{index}_final_mixed.txt",
            "translated": f"chapter_{index}_translated.txt",
            "polish": f"chapter_{index}_polish.json",
            "polish_approved": f"chapter_{index}_polish_approved.json",
            "final_chinese": f"chapter_{index}_final_chinese.txt",
            "titles": f"chapter_{index}_titles.json",
            "summary": f"chapter_{index}_summary.json",
            "word_marker": f"chapter_{index}_word.json",
            "manual_review": f"chapter_{index}_manual_review.log",
        }.items() if k != "dir"}
        p["dir"] = d
        return p

    @staticmethod
    def _exists_nonempty(path: Path) -> bool:
        try:
            return path.exists() and path.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def _write_json(path: Path, payload) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    # ------------------------------------------------------------------
    def run_chapter(self, chapter: Chapter, label: int) -> ChapterResult:
        t0 = time.perf_counter()
        result = ChapterResult(index=chapter.index, label=label)
        self._emit("chapter_start", chapter_index=chapter.index, label=label,
                   heading=chapter.heading, paragraphs=chapter.paragraph_count)

        # 已完成 → 直接早退，连章节工作目录都不创建
        previous = self._completed.get(str(chapter.index))
        if self.cfg.get("SKIP_COMPLETED") and previous:
            result.status = "skipped"
            result.title = str(previous.get("title", ""))
            result.duration_s = 0.0
            log.info(f"第 {chapter.index} 章已完成（《{result.title}》），跳过，未产生任何文件")
            self._emit("chapter_done", chapter_index=chapter.index, label=label,
                       status="skipped", title=result.title, cost=0.0, duration=0.0,
                       errors=[])
            return result

        paths = self._paths(chapter.index)
        spent_before = self.client.budget.spent_on_chapter(chapter.index)

        skip = bool(self.cfg.SKIP_COMPLETED)
        profile: dict = {}
        raw_text = ""
        self.wait_for_offpeak()

        def step(step_no: int, name: str, fn):
            """统一的步骤包装：耗时统计、异常归集、事件上报。"""
            self._checkpoint()
            st = time.perf_counter()
            self._emit("step_start", chapter_index=chapter.index, step=step_no, name=name)
            record = StepRecord(step=step_no, name=name, status="done")
            result.steps.append(record)   # 先入列，步骤函数内可用 result.steps[-1] 回填
            try:
                value = fn()
                record.duration_s = time.perf_counter() - st
                self._emit("step_done", chapter_index=chapter.index, step=step_no,
                           name=name, status=record.status, duration=record.duration_s)
                return value
            except ReviewCancelled:
                record.status = "cancelled"
                record.duration_s = time.perf_counter() - st
                raise
            except Exception as exc:  # noqa: BLE001
                record.status = "failed"
                record.detail = f"{type(exc).__name__}: {exc}"
                record.duration_s = time.perf_counter() - st
                result.errors.append(f"步骤{step_no} {name}: {record.detail}")
                log.error(f"第 {chapter.index} 章 步骤{step_no}（{name}）失败：{record.detail}")
                self._emit("step_failed", chapter_index=chapter.index, step=step_no,
                           name=name, error=record.detail)
                raise

        try:
            # ---------- 步骤 1：解析 ----------
            def s1():
                raw_path = paths["raw"]
                if skip and self._exists_nonempty(raw_path):
                    raw_text = raw_path.read_text(encoding="utf-8")
                    result.steps[-1].status = "skipped"
                    return raw_text
                save_chapter_artifacts(chapter, paths["dir"])
                # 统一文件名（解析模块写的是 raw.txt/meta.json）
                staged_raw, staged_meta = paths["dir"] / "raw.txt", paths["dir"] / "meta.json"
                if staged_raw.exists():
                    staged_raw.replace(raw_path)
                if staged_meta.exists():
                    staged_meta.replace(paths["meta"])
                return chapter.text

            raw_text = step(1, "章节摘取", s1)
            if not raw_text.strip():
                raise ValueError("章节正文为空，无法继续")
            profile = chapter_profile(raw_text, self.cfg)
            log.info(f"第 {chapter.index} 章体量：{describe_profile(profile)}")
            self._emit("profile", chapter_index=chapter.index, **profile)

            # ---------- 步骤 2：模式 A 初步替换 ----------
            def s2():
                if skip and self._exists_nonempty(paths["replaced"]):
                    result.steps[-1].status = "skipped"
                    return paths["replaced"].read_text(encoding="utf-8")
                report = mode_a_replace(raw_text, self.noun_table.as_entries())
                paths["replaced"].write_text(report.text_out, encoding="utf-8")
                result.steps[-1].detail = f"替换 {report.total_replacements} 处"
                return report.text_out

            replaced_text = step(2, "专有名词初步替换", s2)

            # ---------- 步骤 3：AI 识别新专有名词 ----------
            def s3():
                if skip and self._exists_nonempty(paths["new_nouns"]):
                    result.steps[-1].status = "skipped"
                    data = self._read_json(paths["new_nouns"], {"entries": []})
                    return data.get("entries", [])
                entries, rep = ai_steps.recognize_new_nouns(
                    self.client, self.cfg, replaced_text, self.noun_table.sources(),
                    chapter.index, self._progress_fn(3),
                )
                self._write_json(paths["new_nouns"], {"entries": entries, "report": rep.as_dict()})
                result.steps[-1].absorb(rep)
                result.steps[-1].items = len(entries)
                result.steps[-1].detail = f"识别 {len(entries)} 条（{rep.calls} 次调用）"
                return entries

            new_nouns = step(3, "AI 识别新专有名词", s3)

            # ---------- 步骤 4：人工审核 ----------
            def s4():
                if skip and self._exists_nonempty(paths["approved_nouns"]):
                    result.steps[-1].status = "skipped"
                    data = self._read_json(paths["approved_nouns"], {"entries": []})
                    entries = data.get("entries", [])
                else:
                    entries = self.reviewer.review_nouns(new_nouns, self.noun_table) or []
                    self._write_json(paths["approved_nouns"], {"entries": entries})
                added, skipped_entries = self.noun_table.add(entries)
                if added:
                    self.noun_table.save()
                # A2：送审 / 通过 / 人工改动 / 跳过 的统计
                stats = ReviewStats(
                    shown=len(new_nouns), approved=len(entries),
                    modified=diff_modified(new_nouns, entries, "target"),
                    skipped=max(0, len(new_nouns) - len(entries)),
                )
                result.review["nouns"] = stats.as_dict()
                result.steps[-1].items = len(added)
                result.steps[-1].detail = (
                    f"送审 {stats.shown} / 通过 {stats.approved}（改动 {stats.modified}）"
                    f" / 新增入表 {len(added)}，跳过 {len(skipped_entries)}"
                )
                self._emit("nouns_updated", chapter_index=chapter.index, added=len(added))
                return entries

            step(4, "专有名词审核", s4)

            # ---------- 步骤 5：以 raw 为基准的二次模式 A 替换 ----------
            def s5():
                if skip and self._exists_nonempty(paths["final_mixed"]):
                    result.steps[-1].status = "skipped"
                    return paths["final_mixed"].read_text(encoding="utf-8")
                report = mode_a_replace(raw_text, self.noun_table.as_entries())
                paths["final_mixed"].write_text(report.text_out, encoding="utf-8")
                result.steps[-1].detail = f"替换 {report.total_replacements} 处（基准=raw）"
                return report.text_out

            final_mixed = step(5, "二次专有名词替换", s5)

            # ---------- 步骤 6：翻译 ----------
            def s6():
                if skip and self._exists_nonempty(paths["translated"]):
                    result.steps[-1].status = "skipped"
                    return paths["translated"].read_text(encoding="utf-8")
                translation, notes, rep = ai_steps.translate_chapter(
                    self.client, self.cfg, final_mixed, chapter.index,
                    self.style_sample, self.style_is_excerpt, self._progress_fn(6),
                )
                if not translation.strip():
                    raise ValueError("翻译结果为空")
                paths["translated"].write_text(translation, encoding="utf-8")
                if notes:
                    self._write_json(paths["dir"] / f"chapter_{chapter.index}_notes.json", notes)
                result.steps[-1].absorb(rep)
                result.steps[-1].detail = f"{rep.chunks} 块，{len(translation)} 字"
                return translation

            translated = step(6, "AI 翻译", s6)

            # ---------- 步骤 7：润色检测 ----------
            def s7():
                if skip and self._exists_nonempty(paths["polish"]):
                    result.steps[-1].status = "skipped"
                    return self._read_json(paths["polish"], {"entries": []}).get("entries", [])
                entries, rep = ai_steps.detect_cultural_issues(
                    self.client, self.cfg, translated, chapter.index, self._progress_fn(7)
                )
                self._write_json(paths["polish"], {"entries": entries, "report": rep.as_dict()})
                self._dump_manual_review(paths, rep)
                result.steps[-1].absorb(rep)
                result.steps[-1].items = len(entries)
                result.steps[-1].detail = f"检测 {len(entries)} 条（{rep.calls} 次调用）"
                return entries

            polish_entries = step(7, "AI 润色检测", s7)

            # ---------- 步骤 8：润色审核 ----------
            def s8():
                if skip and self._exists_nonempty(paths["polish_approved"]):
                    result.steps[-1].status = "skipped"
                    approved = self._read_json(paths["polish_approved"], {"entries": []}).get("entries", [])
                else:
                    approved = self.reviewer.review_polish(polish_entries, translated) or []
                    self._write_json(paths["polish_approved"], {"entries": approved})
                stats = ReviewStats(
                    shown=len(polish_entries), approved=len(approved),
                    modified=diff_modified(polish_entries, approved, "target"),
                    skipped=max(0, len(polish_entries) - len(approved)),
                )
                result.review["polish"] = stats.as_dict()
                result.steps[-1].items = len(approved)
                result.steps[-1].detail = (
                    f"送审 {stats.shown} / 通过 {stats.approved}（改动 {stats.modified}）"
                )
                return approved

            approved_polish = step(8, "润色表审核", s8)

            # ---------- 步骤 9：模式 B 润色替换 ----------
            def s9():
                if skip and self._exists_nonempty(paths["final_chinese"]):
                    result.steps[-1].status = "skipped"
                    return paths["final_chinese"].read_text(encoding="utf-8")
                report = mode_b_replace(translated, approved_polish)
                paths["final_chinese"].write_text(report.text_out, encoding="utf-8")
                result.steps[-1].detail = (
                    f"替换 {report.total_replacements} 处，缺失 {len(report.missing_sources)} 条"
                )
                return report.text_out

            final_chinese = step(9, "润色替换", s9)

            # ---------- 步骤 10：章名推荐 ----------
            def s10():
                if skip and self._exists_nonempty(paths["titles"]):
                    result.steps[-1].status = "skipped"
                    return self._read_json(paths["titles"], {"titles": []}).get("titles", [])
                titles, rep = ai_steps.recommend_titles(
                    self.client, self.cfg, final_chinese, chapter.index
                )
                self._write_json(paths["titles"], {"titles": titles})
                result.steps[-1].absorb(rep)
                result.steps[-1].items = len(titles)
                result.steps[-1].detail = f"候选 {len(titles)} 个"
                return titles

            titles = step(10, "AI 章名推荐", s10)

            # ---------- 步骤 11：写入 Word ----------
            def s11():
                marker = paths["word_marker"]
                if skip and self._exists_nonempty(marker):
                    result.steps[-1].status = "skipped"
                    data = self._read_json(marker, {})
                    return data.get("title") or (titles[0]["title"] if titles else "")
                chosen = (
                    self.reviewer.choose_title(titles)
                    if self.cfg.TITLE_MODE == "manual"
                    else (titles[0] if titles else {"title": "未命名", "style": "", "reason": ""})
                )
                title = str(chosen.get("title", "")).strip() or "未命名"
                word_writer.append_chapter(self.cfg, label, title, final_chinese)
                self._write_json(marker, {
                    "label": label, "title": title,
                    "output": str(self.cfg.path("OUTPUT_DOC_PATH")),
                    "style": chosen.get("style", ""), "reason": chosen.get("reason", ""),
                })
                result.steps[-1].detail = f"第 {label} 章《{title}》"
                return title

            result.title = step(11, "写入 Word", s11) or ""
            result.status = "ok"
        except ReviewCancelled:
            result.status = "cancelled"
            raise
        except BudgetExceeded as exc:
            result.status = "failed"
            result.errors.append(f"预算闸门：{exc}")
            log.error(f"第 {chapter.index} 章终止：{exc}")
        except ApiFatalError as exc:
            result.status = "failed"
            result.errors.append(f"API 致命错误：{exc}")
            log.error(f"第 {chapter.index} 章终止（不可重试错误）：{exc}")
        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.errors.append(f"{type(exc).__name__}: {exc}")
            log.exception(f"第 {chapter.index} 章失败")
        finally:
            result.duration_s = time.perf_counter() - t0
            result.cost_cny = round(
                self.client.budget.spent_on_chapter(chapter.index) - spent_before, 6
            )
            summary = result.as_dict()
            summary["style_excerpt"] = self.style_is_excerpt
            summary["noun_table_size"] = len(self.noun_table.entries)
            summary["chapter_profile"] = profile
            summary["price_tier"] = tier_of(self.cfg)
            summary["chunk_plans"] = [
                {"index": p.index, "chars": p.chars, "est_in": p.est_input_tokens,
                 "max_out": p.max_output_tokens}
                for p in plan_translation(raw_text, self.cfg,
                                          ai_steps.step_thinking(self.cfg, 6))[1]
            ] if raw_text and raw_text.strip() else []
            result.tokens = {
                "prompt": sum(s.prompt_tokens for s in result.steps),
                "completion": sum(s.completion_tokens for s in result.steps),
                "calls": sum(s.calls for s in result.steps),
            }
            summary["tokens"] = result.tokens
            self._write_json(paths["summary"], summary)
            if result.status == "ok":
                self._record_completed(chapter.index, label, result.title)
                if self.cfg.get("CLEAN_INTERMEDIATES", True):
                    self._cleanup_chapter_files(paths["dir"])
            self._emit("chapter_done", chapter_index=chapter.index, label=label,
                       status=result.status, title=result.title, cost=result.cost_cny,
                       duration=result.duration_s, errors=result.errors)
            log.info(
                f"第 {chapter.index} 章结束：{result.status}，"
                f"¥{result.cost_cny:.6f}，{result.duration_s:.1f}s"
            )
        return result

    # ------------------------------------------------------------------
    def _progress_fn(self, step_no: int):
        def _fn(_step: int, message: str) -> None:
            self._checkpoint()
            self._emit("progress", chapter_index=None, step=step_no, message=message)
        return _fn

    def _dump_manual_review(self, paths: dict, report) -> None:
        if not report.warnings:
            return
        try:
            with paths["manual_review"].open("a", encoding="utf-8") as fh:
                fh.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} 步骤7\n")
                for w in report.warnings:
                    fh.write(f"- {w}\n")
        except OSError:  # pragma: no cover
            pass


def run_loop(
    cfg: Config,
    chapters: list[Chapter],
    pipeline: ChapterPipeline,
    start: int,
    count: int,
    emit: EventFn = _noop_event,
) -> list[ChapterResult]:
    """按 START_CHAPTER / LOOP_COUNT 循环处理。"""
    results: list[ChapterResult] = []
    selected = chapters[start - 1: start - 1 + count]
    if not selected:
        log.error(f"起始章 {start} 超出文档范围（共 {len(chapters)} 章）")
        return results

    for offset, chapter in enumerate(selected):
        if pipeline.stopped:
            log.info("流水线已停止，剩余章节不再处理")
            break
        label = chapter.chapter_no if cfg.CHAPTER_LABEL_MODE == "doc" else (offset + 1)
        label = int(label) if label else (offset + 1)
        emit("loop_progress", {
            "index": offset + 1, "total": len(selected),
            "chapter_index": chapter.index, "label": label,
        })
        try:
            results.append(pipeline.run_chapter(chapter, label))
        except ReviewCancelled:
            log.warning("用户取消，终止循环")
            break
    return results
