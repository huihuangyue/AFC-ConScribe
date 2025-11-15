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
 - action: 节点可执行的主要动作（click|type|select|toggle|navigate|submit|open|none）
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


def _infer_action(e: Dict[str, Any]) -> str:
    """根据 tag/role/input type 等推断控件动作类型。
    返回 click|type|select|toggle|navigate|submit|open|none。
    """
    tag = (e.get("tag") or "").lower()
    role = (e.get("role") or "").lower()
    if tag == "input":
        it = (e.get("type") or e.get("input_type") or "").lower()
        it = (e.get("aria", {}) or {}).get("type", it)
        if it in ("checkbox", "radio", "switch", "toggle"):
            return "toggle"
        if it in ("button", "submit", "image", "reset"):
            return "submit" if it == "submit" else "click"
        return "type"
    if tag == "textarea":
        return "type"
    if tag == "select":
        return "select"
    if tag == "a" or role == "link":
        return "navigate"
    if role == "button":
        return "click"
    # 若具有较高交互分，也视为 click
    try:
        if float(e.get("interactive_score") or 0) >= 0.8:
            return "click"
    except Exception:
        pass
    return "none"


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


def _inflate_bbox(bbox: List[int], px: int) -> List[int]:
    try:
        x, y, w, h = [int(b or 0) for b in bbox]
        d = int(max(0, px))
        return [x - d, y - d, max(0, w + 2 * d), max(0, h + 2 * d)]
    except Exception:
        return bbox


def build_controls_tree(
    elements: List[Dict[str, Any]], *,
    only_visible: bool = False,
    filter_occluded: bool = False,
    occ_threshold: float = 0.98,
    expand_to_container: bool = False,
    inflate_px: int = 0,
    force_include_ids: List[str] | None = None,
    force_include_selectors: List[str] | None = None,
    auto_include_roles: List[str] | None = None,
    auto_include_class_keywords: List[str] | None = None,
    min_controls_in_subtree: int = 3,
) -> Dict[str, Any]:
    # 仅保留可见（可配置）且 bbox>0 的候选（控件 + 内容）
    by_idx: Dict[int, Dict[str, Any]] = {}
    candidates: Dict[int, Dict[str, Any]] = {}
    kinds: Dict[int, str] = {}
    include_ids = set((force_include_ids or []))
    force_selectors = [s.strip() for s in (force_include_selectors or []) if str(s).strip()]
    auto_roles = set((auto_include_roles or []))
    auto_kw = [kw.lower() for kw in (auto_include_class_keywords or [])]
    # map id -> index for fast lookup
    id_to_index: Dict[str, int] = {}
    def _match_force_selector(e: Dict[str, Any]) -> bool:
        if not force_selectors:
            return False
        tag = (e.get("tag") or "").lower()
        idv = (e.get("id") or "").strip()
        classes = (e.get("class") or "").strip().split()
        role = (e.get("role") or "").lower()
        attrs = {k.lower(): (v if v is not None else "") for k, v in (e.get("attrs") or {}).items()} if isinstance(e.get("attrs"), dict) else {}
        # also expose common attributes directly
        attrs.setdefault("role", role)
        attrs.setdefault("id", idv)
        attrs.setdefault("class", " ".join(classes))
        for sel in force_selectors:
            s = sel.strip()
            if not s or " " in s:
                continue  # 不支持后代/并列选择器
            ok = True
            # [attr=value] 支持（可多个）
            import re as _re
            for m in _re.finditer(r"\[([a-zA-Z0-9_\-:]+)=\"?([^\]\"]+)\"?\]", s):
                ak, av = m.group(1).lower(), m.group(2)
                if str(attrs.get(ak) or "") != av:
                    ok = False
                    break
            if not ok:
                continue
            # 去除属性片段
            s_wo = _re.sub(r"\[[^\]]+\]", "", s)
            # #id 优先
            if s_wo.startswith("#"):
                if idv == s_wo[1:]:
                    return True
                continue
            # .class1.class2 或 tag.class1.class2
            parts = s_wo.split(".")
            if parts and parts[0]:
                # 有 tag 前缀
                if parts[0].lower() != tag:
                    ok = False
            classes_need = [p for p in parts[1:] if p]
            if ok and classes_need:
                for c in classes_need:
                    if c not in classes:
                        ok = False
                        break
            if ok and s_wo and not s_wo.startswith(('#', '.', '[')) and '.' not in s_wo:
                # 纯 tag 选择器
                if s_wo.lower() != tag:
                    ok = False
            if ok:
                return True
        return False

    for e in elements:
        try:
            idx = int(e.get("index"))
        except Exception:
            continue
        by_idx[idx] = e
        _idv = (e.get("id") or "").strip()
        if _idv and _idv not in id_to_index:
            id_to_index[_idv] = idx
        bbox = e.get("bbox") or [0, 0, 0, 0]
        vis_adv = e.get("visible_adv")
        vis = vis_adv if vis_adv is not None else e.get("visible")
        if only_visible:
            # 仅按可见标志过滤
            if not vis:
                continue
        if filter_occluded:
            # 无论是否按可见标志过滤，都可选择独立过滤高遮挡
            try:
                occ = float(e.get("occlusion_ratio") or 0.0)
                if occ >= float(occ_threshold):
                    continue
            except Exception:
                pass
        if (bbox[2] or 0) <= 0 or (bbox[3] or 0) <= 0:
            continue
        forced_match = _match_force_selector(e)
        if _is_control(e):
            candidates[idx] = e
            kinds[idx] = "control"
        elif _is_content(e) or _is_content_by_area(e) or forced_match:
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

    # 统计每个节点子树内的 control 数
    memo_cnt: Dict[int, int] = {}

    def subtree_control_count(i: int) -> int:
        if i in memo_cnt:
            return memo_cnt[i]
        total = 1 if kinds.get(i) == "control" else 0
        for c in children_map.get(i, []):
            total += subtree_control_count(c)
        memo_cnt[i] = total
        return total

    # 构造节点（对于内容类，仅保留“叶子”候选，避免大容器打框）
    nodes: List[Dict[str, Any]] = []
    for idx, e in candidates.items():
        k = kinds.get(idx, "control")
        if k == "content":
            chs = children_map.get(idx, [])
            # 对于强制包含的 id，不强制“叶子内容”约束
            _forced = False
            try:
                _eid = (e.get("id") or "").strip()
                _forced = bool(_eid and (_eid in include_ids)) or bool(_match_force_selector(e))
            except Exception:
                _forced = False
            # 自动包含（语义/结构）：role 命中或 class 含关键词，且子树内控件数达到阈值
            _auto = False
            try:
                role = (e.get("role") or "").lower()
                cls = (e.get("class") or "").lower()
                kw_hit = any((kw and kw in cls) for kw in auto_kw) if auto_kw else False
                role_hit = (role in auto_roles) if auto_roles else False
                if (role_hit or kw_hit) and subtree_control_count(idx) >= int(max(0, min_controls_in_subtree)):
                    _auto = True
            except Exception:
                _auto = False
            if not (_forced or _auto):
                if any((c in candidates) for c in chs):
                    continue
        nid = f"d{idx}"
        pid_idx = parent_for.get(idx)
        pid = f"d{pid_idx}" if pid_idx is not None else None
        bbox = e.get("bbox") or [0, 0, 0, 0]
        # 可选：将控件 bbox 扩展到最近的内容容器
        if k == "control" and expand_to_container:
            try:
                p = e.get("parent_index")
                best_bbox = bbox
                # 向上寻找第一个被判定为内容容器的祖先
                hop = 0
                while p is not None and hop < 8:  # 限制层级，防止过深
                    pe = by_idx.get(int(p))
                    if not pe:
                        break
                    if _is_content(pe) or _is_content_by_area(pe):
                        bb = pe.get("bbox") or best_bbox
                        # 若容器面积更大则采用
                        try:
                            if (bb[2] or 0) * (bb[3] or 0) > (best_bbox[2] or 0) * (best_bbox[3] or 0):
                                best_bbox = bb
                                break
                        except Exception:
                            best_bbox = bb
                            break
                    p = pe.get("parent_index")
                    hop += 1
                bbox = best_bbox
            except Exception:
                pass
        # 可选：对最终 bbox 做像素膨胀
        if inflate_px and int(inflate_px) > 0:
            bbox = _inflate_bbox(bbox, int(inflate_px))
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
            "action": _infer_action(e) if k == "control" else "none",
        })

    ctrl_cnt = sum(1 for n in nodes if n.get("type") == "control")
    cont_cnt = sum(1 for n in nodes if n.get("type") == "content")

    # 计算根节点（parent 为 None）
    roots = [n.get("id") for n in nodes if n.get("parent") is None]

    return {
        "meta": {
            "source": "dom+ax?",  # 若后续融合 AX，可在上游完善
            "rule_version": "r1.0.0",
            "count": len(nodes),
            "control_count": ctrl_cnt,
            "content_count": cont_cnt,
            "parent_logic": "dom_parent_chain",
        },
        "nodes": nodes,
        "roots": roots,
    }


def write_controls_tree(
    elements: List[Dict[str, Any]], out_path: str, *,
    only_visible: bool = False,
    filter_occluded: bool = False,
    occ_threshold: float = 0.98,
    expand_to_container: bool = False,
    inflate_px: int = 0,
    force_include_ids: List[str] | None = None,
    force_include_selectors: List[str] | None = None,
    auto_include_roles: List[str] | None = None,
    auto_include_class_keywords: List[str] | None = None,
    min_controls_in_subtree: int = 3,
) -> None:
    tree = build_controls_tree(
        elements,
        only_visible=only_visible,
        filter_occluded=filter_occluded,
        occ_threshold=occ_threshold,
        expand_to_container=expand_to_container,
        inflate_px=inflate_px,
        force_include_ids=force_include_ids,
        force_include_selectors=force_include_selectors,
        auto_include_roles=auto_include_roles,
        auto_include_class_keywords=auto_include_class_keywords,
        min_controls_in_subtree=min_controls_in_subtree,
    )
    write_json(out_path, tree)


def refine_tree_parent_child_by_snippet(tree_path: str, tips_index_path: str, *, verbose: bool = False) -> None:
    """Refine parent/children relations using snippet containment.

    Rule: if a node's HTML snippet is fully contained by another node's snippet,
    the latter is considered its parent; choose the smallest containing snippet as nearest parent.
    Updates the tree JSON in-place and attaches updated roots and meta.parent_logic.
    """
    import os
    import json
    try:
        with open(tree_path, "r", encoding="utf-8") as f:
            tree = json.load(f) or {}
    except Exception:
        return
    nodes = [n for n in (tree.get("nodes") or []) if isinstance(n, dict)]
    by_id: Dict[str, Dict[str, Any]] = {str(n.get("id")): n for n in nodes}
    # load tips index
    try:
        with open(tips_index_path, "r", encoding="utf-8") as f:
            tips_idx = json.load(f) or {}
        items = [it for it in (tips_idx.get("items") or []) if isinstance(it, dict)]
    except Exception:
        items = []
    if not items:
        return
    base_dir = os.path.dirname(os.path.abspath(tree_path))
    # map id -> html
    html_map: Dict[str, str] = {}
    for it in items:
        nid = str(it.get("id") or "")
        fp_rel = str(it.get("file") or "")
        if not nid or not fp_rel:
            continue
        fpath = os.path.join(base_dir, fp_rel)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                txt = fh.read()
            # 去掉前置注释，降低干扰
            if txt.startswith("<!--"):
                try:
                    end = txt.find("-->")
                    if end >= 0:
                        txt = txt[end+3:]
                except Exception:
                    pass
            html_map[nid] = txt
        except Exception:
            continue

    # build refined parent map
    ids = [nid for nid in by_id.keys() if nid in html_map]
    # pre-sort candidates by length asc to speed nearest ancestor lookup
    sorted_by_len = sorted(ids, key=lambda k: len(html_map.get(k, "")))
    refined_parent: Dict[str, Optional[str]] = {}
    for cid in ids:
        c_html = html_map.get(cid, "")
        if not c_html:
            refined_parent[cid] = by_id[cid].get("parent")
            continue
        parent_candidate: Optional[str] = None
        for pid in sorted_by_len:
            if pid == cid:
                continue
            p_html = html_map.get(pid, "")
            if not p_html:
                continue
            try:
                if c_html and p_html and (c_html in p_html) and (len(p_html) > len(c_html)):
                    parent_candidate = pid
                    break  # first (shortest) container is the nearest parent
            except Exception:
                continue
        refined_parent[cid] = parent_candidate

    # apply to nodes; rebuild children and roots
    children: Dict[str, List[str]] = {nid: [] for nid in by_id}
    for nid, node in by_id.items():
        pid = refined_parent.get(nid, node.get("parent"))
        # keep original if we didn't compute (node without html)
        if pid is None or (isinstance(pid, str) and pid in by_id):
            node["parent"] = pid
        else:
            # normalize unknown to None
            node["parent"] = None if pid not in by_id else pid
    for nid, node in by_id.items():
        p = node.get("parent")
        if p is not None and isinstance(p, str) and p in children:
            children[p].append(nid)
    for nid, node in by_id.items():
        node["children"] = children.get(nid, [])

    roots = [nid for nid, node in by_id.items() if node.get("parent") is None]
    tree["nodes"] = [by_id[nid] for nid in by_id]
    meta = tree.get("meta") or {}
    meta["parent_logic"] = "snippet_containment"
    tree["meta"] = meta
    tree["roots"] = roots
    try:
        write_json(tree_path, tree)
    except Exception:
        # fallback save
        with open(tree_path, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(tree, f, ensure_ascii=False, indent=2)
