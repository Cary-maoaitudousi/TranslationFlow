"""输入量检测与动态输出预算（应对 1k～10k 词/章的体量差异）。

思路：
1. 先按配置的分块预算把原文切开（段落 → 行 → 句降级，绝不断句）；
2. 每个块单独估算输入 token，再按 ``OUTPUT_TOKENS_PER_INPUT`` 推输出预算，
   夹在 ``[MIN_CHUNK_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS_CAP]`` 之间；
3. 计划表写进日志与 ``chapter_{n}_summary.json``，跑之前就能看到"这章会被切成
   几块、每块给多少输出额度"，成本可解释。

为什么要动态：短章（1000 词）如果一律给 8000 输出额度，遇上模型跑飞也不会被截断；
长章（10000 词）如果按短章额度算，译文会被腰斩。动态额度两头都兜住。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .chunker import chunk_text, estimate_tokens


@dataclass
class ChunkPlan:
    index: int
    chars: int
    paragraphs: int
    est_input_tokens: int
    max_output_tokens: int

    def as_dict(self) -> dict:
        return {
            "index": self.index, "chars": self.chars, "paragraphs": self.paragraphs,
            "est_input_tokens": self.est_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


def budget_for(
    est_input_tokens: int,
    cfg,
    ratio: float = 1.7,
    floor: int = 1200,
    thinking: bool = False,
) -> int:
    """通用输出额度计算：``est_in × ratio``，夹在 [floor, 单块上限] 之间，
    再叠加思考预留，最后受硬上限约束。"""
    cap = int(cfg.get("MAX_OUTPUT_TOKENS_CAP", 8000))
    hard = int(cfg.get("MAX_TOKENS_HARD_CAP", 16000))
    headroom = int(cfg.get("THINKING_TOKEN_HEADROOM", 0)) if thinking else 0
    base = max(int(floor), min(cap, int(est_input_tokens * float(ratio)) + 400))
    return min(hard, base + headroom)


def output_token_budget(est_input_tokens: int, cfg, thinking: bool = False) -> int:
    """翻译步骤的输出额度：按配置的输入/输出比例推导。"""
    return budget_for(
        est_input_tokens, cfg,
        ratio=float(cfg.get("OUTPUT_TOKENS_PER_INPUT", 1.7)),
        floor=int(cfg.get("MIN_CHUNK_OUTPUT_TOKENS", 1200)),
        thinking=thinking,
    )


def plan_translation(text: str, cfg, thinking: bool = False) -> tuple[list[str], list[ChunkPlan]]:
    """返回 (分块文本, 计划表)。"""
    chunks = chunk_text(
        text, int(cfg.MAX_INPUT_TOKENS_PER_CHUNK), float(cfg.CHARS_PER_TOKEN_EN)
    )
    plans: list[ChunkPlan] = []
    for i, chunk in enumerate(chunks, start=1):
        est = estimate_tokens(chunk, float(cfg.CHARS_PER_TOKEN_EN))
        plans.append(
            ChunkPlan(
                index=i, chars=len(chunk),
                paragraphs=len([p for p in chunk.split("\n") if p.strip()]),
                est_input_tokens=est,
                max_output_tokens=output_token_budget(est, cfg, thinking),
            )
        )
    return chunks, plans


def summarize(plans: Iterable[ChunkPlan]) -> str:
    plans = list(plans)
    if not plans:
        return "无需分块"
    total_in = sum(p.est_input_tokens for p in plans)
    total_out = sum(p.max_output_tokens for p in plans)
    detail = "，".join(
        f"#{p.index} {p.chars}字/入{p.est_input_tokens}/出上限{p.max_output_tokens}"
        for p in plans
    )
    return (f"{len(plans)} 块，输入约 {total_in} tok，输出额度合计 {total_out} tok"
            f"（{detail}）")


def chapter_profile(text: str, cfg, thinking: bool = False) -> dict:
    """章节体量画像：词数、字符数、估算 token、建议块数。"""
    words = len(text.split())
    chunks, plans = plan_translation(text, cfg, thinking)
    return {
        "words": words,
        "chars": len(text),
        "paragraphs": len([p for p in text.split("\n") if p.strip()]),
        "est_input_tokens": estimate_tokens(text, float(cfg.CHARS_PER_TOKEN_EN)),
        "chunks": len(chunks),
        "chunk_budget": int(cfg.MAX_INPUT_TOKENS_PER_CHUNK),
        "max_output_per_chunk": [p.max_output_tokens for p in plans],
    }


def describe_profile(profile: dict) -> str:
    outs = profile.get("max_output_per_chunk") or []
    out_desc = f"{min(outs)}~{max(outs)}" if outs else "-"
    return (
        f"{profile['words']} 词 / {profile['chars']} 字符 / {profile['paragraphs']} 段"
        f" | 输入约 {profile['est_input_tokens']} tok"
        f" | 切 {profile['chunks']} 块 | 单块输出额度 {out_desc}"
    )
