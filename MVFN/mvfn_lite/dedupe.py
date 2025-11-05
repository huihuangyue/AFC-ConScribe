"""去重与合并。

作用：对相同/高度重叠的候选进行去重，避免重复输出。
输入：候选或 AFC 节点列表。
输出：去重后的列表。
"""

from typing import List
from .schema import Candidate


def _iou(a, b) -> float:
    if not a or not b:
        return 0.0
    xa1, ya1, xa2, ya2 = a.x, a.y, a.x + a.w, a.y + a.h
    xb1, yb1, xb2, yb2 = b.x, b.y, b.x + b.w, b.y + b.h
    inter_w = max(0.0, min(xa2, xb2) - max(xa1, xb1))
    inter_h = max(0.0, min(ya2, yb2) - max(ya1, yb1))
    inter = inter_w * inter_h
    area_a = (xa2 - xa1) * (ya2 - ya1)
    area_b = (xb2 - xb1) * (yb2 - yb1)
    union = max(area_a + area_b - inter, 1e-6)
    return inter / union


def dedupe_candidates(cands: List[Candidate], iou_thresh: float = 0.9) -> List[Candidate]:
    kept: List[Candidate] = []
    for c in cands:
        if any(_iou(c.bbox, k.bbox) >= iou_thresh for k in kept):
            continue
        kept.append(c)
    return kept

