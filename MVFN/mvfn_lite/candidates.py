"""候选生成。

作用：从 AX/DOM 生成候选控件，过滤不可见/重叠，输出 Candidate 列表。
输入：AX 节点、DOM 节点、截图几何与视口。
输出：Candidate[]
依赖：mvfn_lite.schema, mvfn_lite.utils.dom, mvfn_lite.utils.vision
"""

from typing import List, Dict, Any
from .schema import Candidate, BBox


def generate_candidates(ax_nodes: List[Dict[str, Any]], dom_nodes: List[Dict[str, Any]], viewport: BBox | None = None) -> List[Candidate]:
    """基于 AX/DOM 粗筛可操作控件（按钮、输入框、下拉等）。

    这里只提供最小骨架：
    - 选择 role/aria 可交互的节点
    - 填充基础 bbox/texts 字段
    - 视口过滤（如提供）
    """
    results: List[Candidate] = []
    idx = 0
    for node in (ax_nodes or []):
        role = str(node.get("role", ""))
        if role.lower() not in {"button", "textbox", "combobox", "link", "checkbox", "radio"}:
            continue
        bounds = node.get("bounds") or {}
        bbox = BBox(x=float(bounds.get("x", 0)), y=float(bounds.get("y", 0)), w=float(bounds.get("w", 0)), h=float(bounds.get("h", 0)))
        texts = [t for t in [node.get("name"), node.get("value")] if t]
        if viewport:
            if bbox.x + bbox.w < viewport.x or bbox.y + bbox.h < viewport.y:
                continue
        cid = f"cand_{idx}"
        idx += 1
        results.append(Candidate(id=cid, role=role, bbox=bbox, texts=texts, visibility=True, viewport=viewport))
    return results

