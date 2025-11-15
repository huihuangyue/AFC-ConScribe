"""
mvfn_lite.candidate_generation
从采集目录的 DOM 简表/AX 树生成可操作控件候选。

输入：
  - <dir>/dom_summary.json（必须）
  - <dir>/ax.json（可选）

输出：
  - <dir>/AFC/candidates.json

规则：
  - 仅生成“可操作控件”：基于 tag/role/interactive_score/class('btn')。
  - bbox 优先使用 page_bbox（绝对坐标），否则退回 bbox（视口坐标）。
  - raw_texts 汇总 DOM 摘要中的 text/labels/title/aria/name。
"""

from __future__ import annotations

from typing import Any, Dict, List
import os

from .types import Candidate
from .utils_io import read_json, ensure_dir, write_json


CONTROL_TAGS = {"button", "input", "select", "textarea", "a"}
CONTROL_ROLES = {"button", "link", "textbox", "checkbox", "radio", "combobox"}
CONTENT_MIN_AREA = 20000


def _to_bbox(v: List[int] | None) -> List[int]:
    if not v or len(v) < 4:
        return [0, 0, 0, 0]
    try:
        x, y, w, h = int(v[0] or 0), int(v[1] or 0), int(v[2] or 0), int(v[3] or 0)
        return [x, y, w, h]
    except Exception:
        return [0, 0, 0, 0]


def _is_control(e: Dict[str, Any]) -> bool:
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


def _iou(a: List[int], b: List[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    inter_w = max(0, min(ax2, bx2) - max(ax, bx))
    inter_h = max(0, min(ay2, by2) - max(ay, by))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / max(1, union)


def _score_candidate(role: str | None, tag: str | None, cls: str | None, interactive: float | None, text_cnt: int, occ: float | None) -> float:
    s = 0.0
    if role and role.lower() in CONTROL_ROLES:
        s += 0.5
    if tag and tag.lower() in CONTROL_TAGS:
        s += 0.3
    if isinstance(interactive, (int, float)):
        s += 0.3 * max(0.0, min(1.0, float(interactive)))
    if cls and "btn" in cls.lower():
        s += 0.2
    if text_cnt > 0:
        s += 0.1
    if isinstance(occ, (int, float)) and float(occ) >= 0.7:
        s -= 0.2
    return max(0.0, min(1.0, s))


def _collect_raw_texts(e: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    aria = e.get("aria") or {}
    for k in ("aria-label", "aria-labelledby", "aria-placeholder"):
        v = aria.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    for k in ("name", "title"):
        v = e.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    labels = e.get("labels") or []
    if isinstance(labels, list):
        for v in labels:
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    txt = e.get("text")
    if isinstance(txt, str) and txt.strip():
        out.append(txt.strip())
    # 去重，保序
    seen = set()
    dedup: List[str] = []
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        dedup.append(t)
    return dedup


def generate_candidates(dir_path: str) -> str:
    dom_path = os.path.join(dir_path, "dom_summary.json")
    dom_scrolled_path = os.path.join(dir_path, "dom_summary_scrolled.json")
    tree_path = os.path.join(dir_path, "controls_tree.json")
    ax_path = os.path.join(dir_path, "ax.json")
    out_dir = ensure_dir(os.path.join(dir_path, "AFC"))

    # 读取 DOM（合并滚动后）
    dom = read_json(dom_path)
    els = (dom.get("elements") or [])
    dom_sc = read_json(dom_scrolled_path)
    els_sc = (dom_sc.get("elements") or [])

    # 合并策略改进：同一 index 同时出现在“初始/滚动后”两份快照时，
    # 选择 page_bbox.y 更小的版本（缓解 sticky/fixed 组件在滚动时被加上 scrollY 导致的绝对坐标上移问题），
    # 若 page_bbox 缺失，则优先 visible_adv=True 和 occlusion_ratio 较小者。
    by_dom: Dict[str, Dict[str, Any]] = {}
    def pick_better(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        if not old:
            return new
        pbo = old.get("page_bbox") or [0, 0, 0, 0]
        pbn = new.get("page_bbox") or [0, 0, 0, 0]
        yo = float(pbo[1] or 0)
        yn = float(pbn[1] or 0)
        has_pbo = bool(pbo and len(pbo) >= 4 and (pbo[2] or 0) and (pbo[3] or 0))
        has_pbn = bool(pbn and len(pbn) >= 4 and (pbn[2] or 0) and (pbn[3] or 0))
        if has_pbo and has_pbn:
            return new if yn < yo else old
        if has_pbn and not has_pbo:
            return new
        if has_pbo and not has_pbn:
            return old
        # 都没有 page_bbox：选可见且遮挡小者
        vo = old.get("visible_adv") if old.get("visible_adv") is not None else old.get("visible")
        vn = new.get("visible_adv") if new.get("visible_adv") is not None else new.get("visible")
        if vn and not vo:
            return new
        if vo and not vn:
            return old
        occo = float(old.get("occlusion_ratio") or 1.0)
        occn = float(new.get("occlusion_ratio") or 1.0)
        return new if occn < occo else old

    for lst in (els, els_sc):
        for e in lst:
            try:
                idx = int(e.get("index"))
            except Exception:
                continue
            nid = f"d{idx}"
            if nid in by_dom:
                by_dom[nid] = pick_better(by_dom[nid], e)
            else:
                by_dom[nid] = e

    # Universe 统计：以合并后的 by_dom 大小近似代表 DOM 总量
    els_all = list(by_dom.values())

    # AX 预留
    _ax = read_json(ax_path) if os.path.exists(ax_path) else {}
    del _ax

    cands: List[Candidate] = []
    # 统计计数（用于生成筛选率）
    stats = {
        "universe_dom_total": len(els_all),
        "universe_tree_nodes": 0,
        "control_selected": 0,
        "visible_bbox_pass": 0,
        "dedup_final": 0,
        "path": None,  # "tree" or "dom"
    }

    use_tree = False
    tree = read_json(tree_path) if os.path.exists(tree_path) else {}
    nodes = tree.get("nodes") or []
    if nodes:
        use_tree = True
        stats["path"] = "tree"
        stats["universe_tree_nodes"] = len(nodes)
        for n in nodes:
            nid = n.get("id")
            if not nid:
                continue
            ntype = (n.get("type") or "").lower()
            if ntype != "control":
                # 仅控件；如需纳入内容卡片，可在此扩展
                continue
            stats["control_selected"] += 1
            g = n.get("geom") or {}
            pbb = _to_bbox(g.get("page_bbox"))
            bb = _to_bbox(g.get("bbox"))
            use_bb = pbb if (pbb[2] > 0 and pbb[3] > 0) else bb
            if use_bb[2] <= 0 or use_bb[3] <= 0:
                continue
            d = by_dom.get(nid) or {}
            vis = d.get("visible_adv") if d.get("visible_adv") is not None else d.get("visible")
            if vis is False:
                continue
            stats["visible_bbox_pass"] += 1
            tag = (d.get("tag") or "").lower()
            role = d.get("role") or None
            cls = (d.get("class") or "")
            occ = d.get("occlusion_ratio")
            raw_texts = _collect_raw_texts(d)
            sc = _score_candidate(role, tag, cls, d.get("interactive_score"), len(raw_texts), occ)
            cands.append(
                Candidate(
                    id=nid,
                    role=role,
                    bbox=bb,
                    page_bbox=pbb if (pbb[2] > 0 and pbb[3] > 0) else None,
                    visible=bool(vis) if vis is not None else True,
                    raw_texts=raw_texts,
                    dom_ref=int(d.get("index")) if d.get("index") is not None else None,
                    ax_ref=None,
                    source="tree",
                    selector=n.get("selector"),
                    occlusion_ratio=float(occ) if isinstance(occ, (int, float)) else None,
                    score=sc,
                )
            )
    else:
        # 回退到 DOM 简表生成（旧逻辑）
        stats["path"] = "dom"
        for e in els_all:
            try:
                idx = int(e.get("index"))
            except Exception:
                continue
            vis = e.get("visible_adv") if e.get("visible_adv") is not None else e.get("visible")
            bb = _to_bbox(e.get("bbox"))
            if not vis or (bb[2] <= 0 or bb[3] <= 0):
                continue
            if _is_control(e):
                stats["control_selected"] += 1
            else:
                continue
            pid = f"d{idx}"
            pbb = _to_bbox(e.get("page_bbox"))
            role = e.get("role")
            raw_texts = _collect_raw_texts(e)
            sc = _score_candidate(role, e.get("tag"), e.get("class"), e.get("interactive_score"), len(raw_texts), e.get("occlusion_ratio"))
            stats["visible_bbox_pass"] += 1
            cands.append(
                Candidate(
                    id=pid,
                    role=role,
                    bbox=bb,
                    page_bbox=pbb if (pbb[2] > 0 and pbb[3] > 0) else None,
                    visible=bool(vis),
                    raw_texts=raw_texts,
                    dom_ref=idx,
                    ax_ref=None,
                    source="dom",
                    selector=None,
                    occlusion_ratio=float(e.get("occlusion_ratio")) if isinstance(e.get("occlusion_ratio"), (int, float)) else None,
                    score=sc,
                )
            )

    # 去重：selector 相同且 IOU>0.9 认为重复；保留分高者
    dedup: List[Candidate] = []
    for c in cands:
        use_bb = c.page_bbox if (c.page_bbox and c.page_bbox[2] > 0 and c.page_bbox[3] > 0) else c.bbox
        kept = True
        for i, kc in enumerate(dedup):
            kb = kc.page_bbox if (kc.page_bbox and kc.page_bbox[2] > 0 and kc.page_bbox[3] > 0) else kc.bbox
            same_sel = (c.selector and kc.selector and c.selector == kc.selector)
            if same_sel and _iou(use_bb, kb) > 0.9:
                # 取分高者
                if c.score > kc.score:
                    dedup[i] = c
                kept = False
                break
        if kept:
            dedup.append(c)
    stats["dedup_final"] = len(dedup)

    # 计算筛选率（相对于 Universe）
    base = stats["universe_tree_nodes"] or stats["universe_dom_total"] or 1
    def _rate(x: int) -> float:
        return round(float(x) / float(base), 4)
    rates = {
        "control_selected_rate": _rate(stats["control_selected"]),
        "visible_bbox_pass_rate": _rate(stats["visible_bbox_pass"]),
        "dedup_final_rate": _rate(stats["dedup_final"]),
    }

    out_path = os.path.join(out_dir, "candidates.json")
    write_json(
        out_path,
        {
            "version": "v0.1",
            "dir": dir_path,
            "source": "controls_tree" if use_tree else "dom_summary",
            "count": len(dedup),
            "stats": stats,
            "rates": rates,
            "candidates": [c.to_dict() for c in dedup],
        },
    )
    return out_path


__all__ = ["generate_candidates"]
