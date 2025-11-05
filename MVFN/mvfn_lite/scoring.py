"""打分融合。

作用：融合文本/图标/角色/上下文等多通道证据，给出总体置信度与解释。
输入：Candidate.evidence 及通道权重。
输出：confidence, channel_scores
"""

from typing import Dict, Tuple
from .schema import Candidate


DEFAULT_WEIGHTS = {
    "ax": 1.0,
    "innerText": 0.8,
    "aria": 0.9,
    "ocr": 0.5,
    "icon": 0.6,
    "role": 0.7,
}


def fuse_scores(c: Candidate, weights: Dict[str, float] | None = None) -> Tuple[float, Dict[str, float]]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    channel_scores: Dict[str, float] = {k: 0.0 for k in w}
    for e in c.evidence:
        channel_scores[e.source] = max(channel_scores.get(e.source, 0.0), e.score * w.get(e.source, 0.5))
    confidence = sum(channel_scores.values()) / max(len(channel_scores), 1)
    return confidence, channel_scores

