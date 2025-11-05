"""索引工具（占位）。"""

from typing import List


def normalize(vecs: List[List[float]]) -> List[List[float]]:
    out = []
    for v in vecs:
        s = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / s for x in v])
    return out

