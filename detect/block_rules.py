from __future__ import annotations

"""
block_rules

严格规则版“主控件块”筛选（不打分）：
- 尺寸异常（0×0、过小、过大、极端长宽比）直接否决；
- 仅在“链表压缩”后的分叉节点（children 数≥2）上判断；
- 肯定条件：子树存在“提交按钮”（action==submit 或选择器含 search/submit）；
- 参考条件：节点自身类名命中“内层/容器”类词（inner/inner-wrap/innerwrap/list/items/nav/wrap/container/footer 等）。

当前版本相对最初实现做了“放宽”：
- 仍然保留尺寸否决与链表压缩逻辑；
- 但不再强制“提交按钮”作为唯一入口，只要命中容器类词（如导航栏/底部/列表容器）也可以成为块；
- 当 blocks_strict_require_inner=True 时，要求“内层类词 OR 提交按钮”，避免完全无语义支撑的大块。

产物：写入 blocks.json（ARTIFACTS["blocks"]），形式：
{
  "rules": "strict",
  "blocks": [ { id, selector, bbox, reasons: {...} }, ... ]
}
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

try:
    from .constants import ARTIFACTS, DEFAULT_VIEWPORT  # type: ignore
except Exception:
    from constants import ARTIFACTS, DEFAULT_VIEWPORT  # type: ignore


# “内层容器”类关键字：在严格规则里作为强语义信号
# 早期仅有 inner/inner-wrap/list/items，过于激进，容易只留下 1 个块；
# 这里扩展一些常见容器/导航类词，用于放宽筛选。
INNER_KWS = [
    "inner",
    "inner-wrap",
    "innerwrap",
    "list",
    "items",
    "nav",
    "wrap",
    "container",
    "panel",
    "footer",
]


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _viewport(out_dir: str) -> Dict[str, int]:
    meta = _read_json(os.path.join(out_dir, ARTIFACTS["meta"]))
    vp = meta.get("viewport") or DEFAULT_VIEWPORT
    try:
        return {"width": int(vp.get("width", 1280)), "height": int(vp.get("height", 800))}
    except Exception:
        return {"width": 1280, "height": 800}


def _load_tree(out_dir: str) -> Dict[str, Any]:
    return _read_json(os.path.join(out_dir, ARTIFACTS["controls_tree"]))


def _load_dom_elements(out_dir: str) -> List[Dict[str, Any]]:
    # 优先滚动后的摘要
    for key in ("dom_summary_scrolled", "dom_summary"):
        p = os.path.join(out_dir, ARTIFACTS[key])
        if os.path.exists(p):
            doc = _read_json(p)
            els = doc.get("elements") or []
            if isinstance(els, list):
                return els
    return []


def _index_from_node_id(nid: str) -> Optional[int]:
    try:
        if nid and nid.startswith("d"):
            return int(nid[1:])
    except Exception:
        return None
    return None


def _class_of_node(nid: str, elements: List[Dict[str, Any]]) -> str:
    idx = _index_from_node_id(nid)
    if idx is None:
        return ""
    # dom_summary 的 index 即 DOM 顺序，不保证稠密；遍历查找
    for e in elements:
        try:
            if int(e.get("index")) == idx:
                return (e.get("class") or "")
        except Exception:
            continue
    return ""


def _has_submit_in_subtree(root_id: str, by_id: Dict[str, Dict[str, Any]]) -> bool:
    # BFS 子树，查看是否存在 action==submit 或 selector 命中 search/submit
    q = [root_id]
    seen = set()
    while q:
        nid = q.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        node = by_id.get(nid) or {}
        action = (node.get("action") or "").lower()
        sel = (node.get("selector") or "").lower()
        if action == "submit" or ("search" in sel) or ("submit" in sel):
            return True
        for c in (node.get("children") or []):
            if isinstance(c, str):
                q.append(c)
    return False


def _bbox(node: Dict[str, Any]) -> Tuple[int, int, int, int]:
    g = node.get("geom") or {}
    bb = g.get("bbox") or [0, 0, 0, 0]
    try:
        return int(bb[0] or 0), int(bb[1] or 0), int(bb[2] or 0), int(bb[3] or 0)
    except Exception:
        return 0, 0, 0, 0


def _size_veto(bb: Tuple[int, int, int, int], vp: Dict[str, int]) -> Optional[str]:
    x, y, w, h = bb
    if w <= 0 or h <= 0:
        return "zero_size"
    if w < 96 or h < 80:
        return "too_small"
    vw, vh = int(vp.get("width", 1280)), int(vp.get("height", 800))
    if (w >= int(0.85 * vw) and h >= int(0.5 * vh)) or (w * h >= int(0.6 * vw * vh)):
        return "too_large"
    ratio = w / max(1, h)
    if ratio > 10 or (1 / ratio) > 10:
        return "extreme_ratio"
    return None


def segment_blocks_strict(out_dir: str, *, require_inner_kw: bool = False, max_blocks: int = 8) -> Dict[str, Any]:
    tree = _load_tree(out_dir)
    nodes = [n for n in (tree.get("nodes") or []) if isinstance(n, dict)]
    by_id: Dict[str, Dict[str, Any]] = {str(n.get("id")): n for n in nodes}
    vp = _viewport(out_dir)
    elements = _load_dom_elements(out_dir)

    # 构建 children 映射
    ch: Dict[str, List[str]] = {}
    for n in nodes:
        pid = n.get("parent")
        nid = str(n.get("id"))
        ch.setdefault(nid, n.get("children") or [])

    # 链表压缩：将“只有 1 个子节点”的链压到最后一个
    def chain_end(nid: str) -> str:
        cur = nid
        while True:
            kids = [c for c in (by_id.get(cur, {}).get("children") or []) if isinstance(c, str)]
            if len(kids) == 1:
                cur = kids[0]
                continue
            break
        return cur

    ends = set(chain_end(nid) for nid in by_id.keys())
    # 仅保留“多分叉”的节点
    candidates = []
    for nid in ends:
        node = by_id.get(nid) or {}
        kids = node.get("children") or []
        if not isinstance(kids, list) or len(kids) < 2:
            continue
        candidates.append(nid)

    picked: List[Dict[str, Any]] = []
    for nid in candidates:
        node = by_id.get(nid) or {}
        bb = _bbox(node)
        veto = _size_veto(bb, vp)
        if veto:
            continue
        # 肯定条件：提交按钮存在（表单/搜索模块），或命中“内层/容器”类词（导航栏、列表容器、底部等）
        has_submit = _has_submit_in_subtree(nid, by_id)
        cls = _class_of_node(nid, elements).lower()
        inner_hit = any(kw in cls for kw in INNER_KWS)

        # blocks_strict_require_inner=True 的语义调整为：
        #   需要（inner_hit OR has_submit），避免只靠面积选出毫无语义的大块。
        if require_inner_kw and not (inner_hit or has_submit):
            continue

        # 基本放宽规则：若既没有提交按钮、又没有容器类词，则直接跳过；
        # 即“尺寸 + 结构 + (提交按钮 OR 容器类名)”三要素缺一不可。
        if not (has_submit or inner_hit):
            continue

        # 进一步约束 selector：避免只选到纯标签（如 "div"、"section"），
        # 至少要求带有 id/class/属性之一，便于后续稳定定位。
        sel = (node.get("selector") or "").strip()
        if sel and not any(ch in sel for ch in ("#", ".", "[")):
            continue

        picked.append({
            "id": nid,
            "selector": sel,
            "bbox": [bb[0], bb[1], bb[2], bb[3]],
            "reasons": {
                "size_ok": True,
                "has_submit": has_submit,
                "inner_kw": inner_hit,
                "children_count": len(node.get("children") or []),
            }
        })
        if len(picked) >= int(max_blocks or 1):
            break

    out = {"rules": "strict", "blocks": picked}
    try:
        with open(os.path.join(out_dir, ARTIFACTS["blocks"]), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return out
