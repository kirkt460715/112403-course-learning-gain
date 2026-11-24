"""统计原语：Wilson 置信区间、前后测配对分类。

样本极小是这类研学数据的常态，因此：
- 同时报告原始计数与比例，不用均值/总分掩盖人数；
- n 较小时给出 Wilson 区间，并明确标注“证据有限”；
- 只有同一人、同一场次、同一量表版本、两阶段都可判对错的配对，
  才进入“真实学习变化”的分子分母。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .models import CORRECT, WRONG


def wilson_interval(k: int, n: int, z: float = 1.96) -> Optional[Dict[str, float]]:
    if n <= 0:
        return None
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return {"low": max(0.0, center - margin), "high": min(1.0, center + margin),
            "center": center}


# 配对结果
IMPROVED = "improved"          # 前错后对
REGRESSED = "regressed"        # 前对后错
STABLE_CORRECT = "stable_correct"
STABLE_WRONG = "stable_wrong"

PAIR_LABELS_ZH = {
    IMPROVED: "错→对（改善）",
    REGRESSED: "对→错（回退）",
    STABLE_CORRECT: "对→对（稳定掌握）",
    STABLE_WRONG: "错→错（持续错误）",
}


def pair_outcome(pre_status: str, post_status: str) -> Optional[str]:
    if pre_status not in (CORRECT, WRONG) or post_status not in (CORRECT, WRONG):
        return None
    if pre_status == WRONG and post_status == CORRECT:
        return IMPROVED
    if pre_status == CORRECT and post_status == WRONG:
        return REGRESSED
    if pre_status == CORRECT:
        return STABLE_CORRECT
    return STABLE_WRONG


def summarize_pairs(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """pairs: 每个元素含 outcome / household_independent 等。"""
    gradeable = [p for p in pairs if p.get("outcome")]
    outcomes = {IMPROVED: 0, REGRESSED: 0, STABLE_CORRECT: 0, STABLE_WRONG: 0}
    for p in gradeable:
        outcomes[p["outcome"]] += 1
    n = len(gradeable)
    improved = outcomes[IMPROVED]
    improved_or_stable_mastery = outcomes[IMPROVED] + outcomes[STABLE_CORRECT]
    return {
        "n_gradeable_pairs": n,
        "outcomes": outcomes,
        "improvement_rate": (improved / n) if n else None,
        "mastery_retention_rate": (improved_or_stable_mastery / n) if n else None,
        "wilson_improvement": wilson_interval(improved, n),
        "independent_pairs": sum(1 for p in gradeable if p.get("household_independent")),
        "household_level_pairs": sum(1 for p in gradeable if not p.get("household_independent")),
    }
