"""
detect.controls_tree
构建“极简控件树”并写出 JSON 产物：controls_tree.json。

节点字段：
- id: 以 DOM 索引为后缀（如 d45）
- type: control|content（交互控件或内容卡片）
- parent: 父节点 id（无则为 null）
- children: 子节点 id 列表
- selector: 简易 CSS 选择器（稳定优先，不保证唯一）
- geom: { bbox: [x,y,w,h], shape: rect|pill|round[, page_bbox] }
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 兼容包内/脚本直接运行导入
try:  # pragma: no cover
    from .utils import write_json
except Exception:  # pragma: no cover
    from utils import write_json  # type: ignore


CONTROL_TAGS = {"button", "input", "select", "textarea", "a"}
CONTROL_ROLES = {"button", "link", "textbox", "checkbox", "radio", "combobox"}
CONTENT_TAGS = {"article", "figure", "section", "li"}
CONTENT_ROLES = {"article", "listitem", "feed", "region", "group"}
_MIN_CONTENT_AREA = 20000  # 放宽面积阈值（约 142x142 或 250x80 以上）


def _is_control(e: Dict[str, Any]) -> bool:
    if e.get("is_control") is True:
        return True
    tag = (e.get("tag") or "").lower()
    role = (e.get("role") or "").lower()
    if tag in CONTROL_TAGS:
        return True
    if role in CONTROL_ROLES:
        return True
    if (e.get("interactive_score") or 0) >= 0.5:
        return True
    cls = (e.get("class") or "").lower()
    if "btn" in cls:
        return True
    return False


def _is_content(e: Dict[str, Any]) -> bool:
    """内容卡片/列表项的启发式。"""
    role = (e.get("role") or "").lower()
    if role in CONTENT_ROLES:
        return True
    tag = (e.get("tag") or "").lower()
    if tag in CONTENT_TAGS:
        return True
    cls = (e.get("class") or "").lower()
    for kw in ("card", "tile", "list-item", "listitem", "grid-item", "grid", "cell", "module", "result", "story", "news", "panel", "item", "block", "feed-card"):
        if kw in cls:
            return True
    return False


def _is_content_by_area(e: Dict[str, Any]) -> bool:
    """面积/尺寸兜底：较大的可见块视为内容卡片，过滤根容器。"""
    tag = (e.get("tag") or "").lower()
    if tag in {"html", "body"}:
        return False
    role = (e.get("role") or "").lower()
    if role in {"main", "application"}:
        return False
    bbox = e.get("bbox") or [0, 0, 0, 0]
    try:
        w = int(bbox[2] or 0)
        h = int(bbox[3] or 0)
    except Exception:
        return False
    if w < 96 or h < 80:
        return False
    if w * h < _MIN_CONTENT_AREA:
        return False
    return True


def _shape_from_radius(bbox: List[int], border_radius: Optional[float]) -> str:
    try:
        w = int(bbox[2])
        h = int(bbox[3])
    except Exception:
        return "rect"
    if not border_radius or w <= 0 or h <= 0:
        return "rect"
    m = min(w, h)
    br = float(border_radius)
    if br >= m * 0.45:  # 近似圆/圆角满
        return "round"
    if br >= m * 0.25:  # 胶囊/大圆角
        return "pill"
    return "rect"


def _stable_classes(class_str: Optional[str]) -> List[str]:
    if not class_str:
        return []
    parts = str(class_str).strip().split()
    good: List[str] = []
    for c in parts:
        if len(c) > 30:
            continue
        # 过滤明显哈希类名
        letters = sum(ch.isalpha() for ch in c)
        digits = sum(ch.isdigit() for ch in c)
        if digits > letters and digits > 3:
            continue
        good.append(c)
        if len(good) >= 2:
            break
    return good


def _build_selector(e: Dict[str, Any]) -> str:
    tag = (e.get("tag") or "").lower() or "*"
    idv = e.get("id")
    if idv:
        return f"#{idv}"
    name = e.get("name")
    if name:
        return f"{tag}[name='{name}']"
    role = e.get("role")
    if role:
        # role 作为备选
        cls = _stable_classes(e.get("class"))
        if cls:
            return f"{tag}.{'.'.join(cls)}[role='{role}']"
        return f"{tag}[role='{role}']"
    cls = _stable_classes(e.get("class"))
    if cls:
        return f"{tag}.{'.'.join(cls)}"
    return tag


def build_controls_tree(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    # 仅保留可见且 bbox>0 的候选（控件 + 内容）
    by_idx: Dict[int, Dict[str, Any]] = {}
    candidates: Dict[int, Dict[str, Any]] = {}
    kinds: Dict[int, str] = {}
    for e in elements:
        try:
            idx = int(e.get("index"))
        except Exception:
            continue
        by_idx[idx] = e
        bbox = e.get("bbox") or [0, 0, 0, 0]
        vis = e.get("visible_adv") if e.get("visible_adv") is not None else e.get("visible")
        if not vis:
            continue
        if (bbox[2] or 0) <= 0 or (bbox[3] or 0) <= 0:
            continue
        if _is_control(e):
            candidates[idx] = e
            kinds[idx] = "control"
        elif _is_content(e) or _is_content_by_area(e):
            candidates[idx] = e
            kinds[idx] = "content"

    # 父子关系：最近的控件祖先
    parent_for: Dict[int, Optional[int]] = {}
    for idx, e in candidates.items():
        p = e.get("parent_index")
        while p is not None:
            if p in candidates:
                parent_for[idx] = p
                break
            pe = by_idx.get(int(p)) if p is not None else None
            if pe is None:
                parent_for[idx] = None
                break
            p = pe.get("parent_index")
        if p is None:
            parent_for[idx] = None

    children_map: Dict[Optional[int], List[int]] = {}
    for idx, p in parent_for.items():
        children_map.setdefault(p, []).append(idx)

    # 构造节点（对于内容类，仅保留“叶子”候选，避免大容器打框）
    nodes: List[Dict[str, Any]] = []
    for idx, e in candidates.items():
        k = kinds.get(idx, "control")
        if k == "content":
            chs = children_map.get(idx, [])
            if any((c in candidates) for c in chs):
                continue
        nid = f"d{idx}"
        pid_idx = parent_for.get(idx)
        pid = f"d{pid_idx}" if pid_idx is not None else None
        bbox = e.get("bbox") or [0, 0, 0, 0]
        shape = _shape_from_radius(bbox, e.get("border_radius"))
        selector = _build_selector(e)
        children_ids = [f"d{i}" for i in children_map.get(idx, [])]
        nodes.append({
            "id": nid,
            "type": k,
            "parent": pid,
            "children": children_ids,
            "selector": selector,
            "geom": {"bbox": bbox, "shape": shape, **({"page_bbox": (e.get("page_bbox") or None)} if e.get("page_bbox") is not None else {})},
        })

    ctrl_cnt = sum(1 for n in nodes if n.get("type") == "control")
    cont_cnt = sum(1 for n in nodes if n.get("type") == "content")

    return {
        "meta": {
            "source": "dom+ax?",  # 若后续融合 AX，可在上游完善
            "rule_version": "r1.0.0",
            "count": len(nodes),
            "control_count": ctrl_cnt,
            "content_count": cont_cnt,
        },
        "nodes": nodes,
    }


def write_controls_tree(elements: List[Dict[str, Any]], out_path: str) -> None:
    tree = build_controls_tree(elements)
    write_json(out_path, tree)
