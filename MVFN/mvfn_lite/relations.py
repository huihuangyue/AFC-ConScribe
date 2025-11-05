"""上下文关系构建。

作用：根据布局和归属建立节点关系（如同表单、同一行、邻接）。
输入：候选或 AFC 节点。
输出：relations 映射。
"""

from typing import Dict, List
from .schema import Candidate


def build_relations(candidates: List[Candidate]) -> Dict[str, List[str]]:
    # 占位：按相近 y 值粗略视为同一行
    relations: Dict[str, List[str]] = {}
    for i, a in enumerate(candidates):
        same_line = []
        for j, b in enumerate(candidates):
            if i == j or not a.bbox or not b.bbox:
                continue
            if abs(a.bbox.y - b.bbox.y) < 8:  # 粗略阈值
                same_line.append(b.id)
        if same_line:
            relations[a.id] = same_line
    return relations

