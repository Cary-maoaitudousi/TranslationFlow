"""替换表体检：跑真实 API 之前先看这张表会不会误伤正文。

检查三件事：
1. **命中分布**：每条规则在前 N 章里命中多少次（0 命中 = 本章用不上，正常）；
2. **危险条目**：全小写且很短的"通用后缀词"（kit / paw / star / moon 这类），
   当它们作为独立单词出现在正文里（如 "white paw"）会被替换成中文，破坏句子；
3. **误伤示例**：把命中位置的前后文摘出来，人工一眼能判断该留还是该删。

不调用 API，纯本地计算。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

GENERIC_SUFFIXES = {"kit", "paw", "star", "moon", "tail", "fur", "claw", "pelt", "fang", "heart", "wing", "whisker"}


@dataclass
class EntryAudit:
    source: str
    target: str
    hits: int = 0
    contexts: list[str] = field(default_factory=list)

    @property
    def risky(self) -> bool:
        src = self.source
        return src.islower() and (len(src) <= 8 or src in GENERIC_SUFFIXES)

    @property
    def shape(self) -> str:
        """条目形态：全小写 / 首字母大写 / 全大写 / 含空格。"""
        src = self.source
        if " " in src:
            return "短语"
        if src.islower():
            return "全小写"
        if src.isupper():
            return "全大写"
        return "首字母大写"


def _pattern(source: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z])" + re.escape(source) + r"(?![A-Za-z])")


def audit_entries(entries: list[dict[str, str]], text: str, max_contexts: int = 2) -> list[EntryAudit]:
    audits: list[EntryAudit] = []
    for entry in entries:
        src = entry["source"]
        pattern = _pattern(src)
        matches = list(pattern.finditer(text))
        item = EntryAudit(source=src, target=entry["target"], hits=len(matches))
        for m in matches[:max_contexts]:
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            item.contexts.append(text[start:end].replace("\n", " "))
        audits.append(item)
    return audits


def summarize(audits: list[EntryAudit]) -> dict:
    used = [a for a in audits if a.hits]
    return {
        "entries": len(audits),
        "used": len(used),
        "unused": len(audits) - len(used),
        "total_hits": sum(a.hits for a in audits),
        "risky_used": [a for a in used if a.risky],
        "top": sorted(used, key=lambda a: -a.hits)[:15],
    }
