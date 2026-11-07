"""全局专有名词表（跨章累积，§1.3 / §7.1）。

文件格式（JSON）::

    {"version": 1, "entries": [{"source": "Longtail", "target": "长尾"}, ...]}

也兼容裸数组 ``[{"source": ..., "target": ...}, ...]``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from .logger import get_logger
from .replacer import has_cjk

log = get_logger("review")


def parse_table_text(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """解析 ``英文->中文`` 形式的替换表文本，返回 (条目, 无法解析的行)。

    兼容 ``->``、``→``、``=>``、制表符四种分隔；``#`` 开头视为注释；
    冒号后带注释的行（``A->甲  # 人名``）会保留原文，不做特殊处理。
    """
    entries: list[dict[str, str]] = []
    bad: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        for sep in ("->", "→", "=>", "\t"):
            if sep in line:
                src, tgt = line.split(sep, 1)
                src, tgt = src.strip(), tgt.strip()
                if src and tgt:
                    entries.append({"source": src, "target": tgt})
                else:
                    bad.append(line)
                break
        else:
            bad.append(line)
    return entries, bad


def load_table_text_file(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    p = Path(path)
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return parse_table_text(p.read_text(encoding=enc))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码替换表文件：{p}")


def load_table_file(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    """导入替换表：支持 ``英文->中文`` 文本，也支持已有的 JSON 表。

    返回 (条目, 无法解析的行)。JSON 支持 ``{"entries": [...]}`` 与裸数组两种结构。
    """
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        raw = data.get("entries", []) if isinstance(data, dict) else data
        entries: list[dict[str, str]] = []
        bad: list[str] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, Mapping):
                bad.append(str(item))
                continue
            src, tgt = item.get("source"), item.get("target")
            if isinstance(src, str) and src.strip() and isinstance(tgt, str):
                entries.append({"source": src, "target": tgt})
            else:
                bad.append(json.dumps(item, ensure_ascii=False))
        return entries, bad
    return load_table_text_file(p)


def export_table_text(entries: Iterable[Mapping[str, str]]) -> str:
    """导出成 ``英文->中文`` 文本（便于人工 diff / 备份）。"""
    return "\n".join(f"{e['source']}->{e['target']}" for e in entries)


class NounTable:
    def __init__(self, path: str | Path, entries: list[dict[str, str]] | None = None):
        self.path = Path(path)
        self.entries: list[dict[str, str]] = entries or []
        self._index: dict[str, dict[str, str]] = {e["source"]: e for e in self.entries}

    # -- 读写 ---------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "NounTable":
        p = Path(path)
        if not p.exists():
            log.info(f"专有名词表不存在，按空表启动：{p}")
            return cls(p, [])
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error(f"专有名词表 JSON 解析失败，按空表启动：{p} ({exc})")
            return cls(p, [])
        raw = data.get("entries", []) if isinstance(data, dict) else data
        entries: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            src, tgt = item.get("source"), item.get("target")
            if isinstance(src, str) and src.strip() and isinstance(tgt, str):
                entries.append({"source": src, "target": tgt})
        log.info(f"已加载专有名词表 {len(entries)} 条：{p}")
        return cls(p, entries)

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self.entries}
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)  # 原子替换，避免中途崩溃损坏表
        return target

    # -- 查询 ---------------------------------------------------------------
    def sources(self) -> set[str]:
        return set(self._index)

    def has(self, source: str) -> bool:
        return source in self._index

    def as_entries(self) -> list[dict[str, str]]:
        return [dict(e) for e in self.entries]

    def render_table_text(self) -> str:
        """审核窗口下部通览用：source -> target，一行一条。"""
        return "\n".join(f"{e['source']}->{e['target']}" for e in self.entries)

    # -- 写入 ---------------------------------------------------------------
    def add(self, entries: Iterable[Mapping[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """追加条目，返回 (新增, 跳过)。跳过原因：已存在 / source 是中文 / 非法。"""
        added: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        for item in entries:
            src = item.get("source", "")
            tgt = item.get("target", "")
            if not isinstance(src, str) or not isinstance(tgt, str) or not src.strip():
                continue
            entry = {"source": src, "target": tgt}
            if has_cjk(src):
                log.debug(f"跳过：source 已是中文 {src!r}")
                skipped.append(entry)
                continue
            if src in self._index:
                log.debug(f"跳过：专有名词表已存在 {src!r}")
                skipped.append(entry)
                continue
            self.entries.append(entry)
            self._index[src] = entry
            added.append(entry)
        return added, skipped
