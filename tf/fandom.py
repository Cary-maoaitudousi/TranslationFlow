"""IP 档案（Fandom Profile）：把"作品设定"从代码里抽出来。

同一套流水线要给不同作品用，差别其实只有几件事：

* 角色在作品里叫什么（猫？人？机甲？精灵？）；
* 专有名词的命名规则（`-kit→崽`、`-star→星` 这类）；
* 哪些"人类中心"表达需要改掉、改成品里该有的说法（肢体、量词、称谓）；
* 章名的风格要求；
* 翻译时的角色设定与语气要求。

这些全部放进一个 JSON 文件（`profiles/*.json`），代码只负责读它、拼提示词。
换一个 IP 就换一个档案文件，不需要改代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger

log = get_logger("main")

# ---------------------------------------------------------------------------
# 默认（通用）档案：没有提供任何档案时的兜底，保证流水线仍可运行
# ---------------------------------------------------------------------------
DEFAULTS: dict = {
    "name": "通用作品",
    "subject_singular": "角色",
    "subject_plural": "角色",
    "domain_note": "",
    "naming_rules": [
        "按原作设定翻译人名、地名、组织名、职位名",
        "同一专有名词全书保持同一译法，不得前后不一致",
        "有官方译名或通行译名时优先采用",
        "姓名中的构词成分（前缀/后缀/头衔）按该作品的构词习惯拆译",
    ],
    "naming_examples": [],
    "noun_scope": "人名、地名、组织/势力名、职位/头衔、专有物品名",
    "culture_rules": [
        "找出与作品设定不符的\"人类中心\"表达（如把角色称作人、使用人体肢体名词），换成符合设定的说法",
        "量词、称谓、亲属关系词要与作品中的生物/身份设定一致",
        "不符合上述特征的表达不要列入",
    ],
    "culture_examples": [],
    "title_rule": "四字章节名，依据不同标准与风格各生成一个",
    "translate_role": "你是一位经验丰富的文学翻译家，擅长处理带世界观设定的作品，能把英文原文译成有个人风格的中文",
    "translation_brief": "忠实原文，不增删情节，保留原文分段；专有名词使用给定的中文，不要改动或音译",
}


@dataclass
class FandomProfile:
    name: str = DEFAULTS["name"]
    subject_singular: str = DEFAULTS["subject_singular"]
    subject_plural: str = DEFAULTS["subject_plural"]
    domain_note: str = ""
    naming_rules: list[str] = field(default_factory=lambda: list(DEFAULTS["naming_rules"]))
    naming_examples: list[str] = field(default_factory=list)
    noun_scope: str = DEFAULTS["noun_scope"]
    culture_rules: list[str] = field(default_factory=lambda: list(DEFAULTS["culture_rules"]))
    culture_examples: list[str] = field(default_factory=list)
    title_rule: str = DEFAULTS["title_rule"]
    translate_role: str = DEFAULTS["translate_role"]
    translation_brief: str = DEFAULTS["translation_brief"]
    path: str = ""
    is_fallback: bool = False

    # -- 渲染小工具 --------------------------------------------------------
    def numbered_rules(self) -> str:
        return "\n".join(f"{i}. {r}" for i, r in enumerate(self.naming_rules, 1))

    def culture_rules_text(self) -> str:
        return "\n".join(f"- {r}" for r in self.culture_rules)

    def naming_examples_text(self) -> str:
        return "\n".join(self.naming_examples) if self.naming_examples else "（无示例，按规则自行判断）"

    def culture_examples_text(self) -> str:
        return "\n".join(self.culture_examples) if self.culture_examples else "（无示例，按规则自行判断）"

    def as_dict(self) -> dict:
        return {
            "name": self.name, "subject_singular": self.subject_singular,
            "subject_plural": self.subject_plural, "domain_note": self.domain_note,
            "naming_rules": self.naming_rules, "naming_examples": self.naming_examples,
            "noun_scope": self.noun_scope, "culture_rules": self.culture_rules,
            "culture_examples": self.culture_examples, "title_rule": self.title_rule,
            "translate_role": self.translate_role, "translation_brief": self.translation_brief,
        }


_cache: dict[str, FandomProfile] = {}


def load_profile(path: str | Path | None) -> FandomProfile:
    """读取 IP 档案；文件缺失/损坏时回退到通用档案（不中断流程）。"""
    if not path:
        return FandomProfile(is_fallback=True, path="")
    p = Path(path)
    key = str(p)
    if key in _cache:
        return _cache[key]
    if not p.exists():
        log.warning(f"IP 档案不存在，使用通用档案：{p}")
        profile = FandomProfile(is_fallback=True, path=key)
        _cache[key] = profile
        return profile
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error(f"IP 档案解析失败，使用通用档案：{p}（{exc}）")
        profile = FandomProfile(is_fallback=True, path=key)
        _cache[key] = profile
        return profile

    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    profile = FandomProfile(
        name=str(merged["name"]),
        subject_singular=str(merged["subject_singular"]),
        subject_plural=str(merged["subject_plural"]),
        domain_note=str(merged.get("domain_note", "")),
        naming_rules=[str(x) for x in merged["naming_rules"]],
        naming_examples=[str(x) for x in merged.get("naming_examples") or []],
        noun_scope=str(merged["noun_scope"]),
        culture_rules=[str(x) for x in merged["culture_rules"]],
        culture_examples=[str(x) for x in merged.get("culture_examples") or []],
        title_rule=str(merged["title_rule"]),
        translate_role=str(merged["translate_role"]),
        translation_brief=str(merged["translation_brief"]),
        path=key,
    )
    _cache[key] = profile
    log.info(f"已加载 IP 档案：{profile.name}（{p.name}）")
    return profile


def profile_for(cfg) -> FandomProfile:
    return load_profile(cfg.path("FANDOM_PROFILE_PATH") if cfg.get("FANDOM_PROFILE_PATH") else None)


def list_profiles(directory: str | Path) -> list[tuple[str, str, Path]]:
    """列出目录下所有档案：[(显示名, 文件名, 路径)]。"""
    d = Path(directory)
    out: list[tuple[str, str, Path]] = []
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("name", path.stem))
        except (OSError, json.JSONDecodeError):
            name = path.stem
        out.append((name, path.stem, path))
    return out
