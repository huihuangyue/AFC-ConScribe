from __future__ import annotations

"""
skill.select

从 controls_tree.json 过滤出更适合作为技能锚点的 selector 列表（不依赖检测阶段的 blocks.json）。

规则（可配置）：
- 尺寸：w>=min_w, h>=min_h, area>=min_area, area<max_area_ratio*viewport；长宽比不过于极端。
- 结构：children 数>=min_children（只关注“多分叉”容器节点）。
- 语义：可选 require_submit_in_subtree（子树中存在 action=submit/selector 含 search/submit），可选 require_inner_kw（selector 含 inner/inner-wrap/list/items 等）。
- 类型：可选仅 content/control（二选一或都要）。

用法：
  python -m skill.select --run-dir <dir> --min-children 2 --require-submit --require-inner-kw --top-k 5
"""

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _viewport(run_dir: str) -> Tuple[int, int]:
    mp = os.path.join(run_dir, "meta.json")
    vw, vh = 1280, 800
    try:
        if os.path.exists(mp):
            meta = _read_json(mp)
            vp = meta.get("viewport") or {}
            vw = int(vp.get("width", vw))
            vh = int(vp.get("height", vh))
    except Exception:
        pass
    return vw, vh


def _bbox(node: Dict[str, Any]) -> Tuple[int, int, int, int]:
    g = node.get("geom") or {}
    bb = g.get("bbox") or [0, 0, 0, 0]
    try:
        return int(bb[0] or 0), int(bb[1] or 0), int(bb[2] or 0), int(bb[3] or 0)
    except Exception:
        return 0, 0, 0, 0


def _has_submit_in_subtree(nid: str, by_id: Dict[str, Dict[str, Any]]) -> bool:
    q = [nid]
    seen = set()
    while q:
        x = q.pop(0)
        if x in seen:
            continue
        seen.add(x)
        n = by_id.get(x) or {}
        act = (n.get("action") or "").lower()
        sel = (n.get("selector") or "").lower()
        if act == "submit" or ("search" in sel) or ("submit" in sel):
            return True
        for c in (n.get("children") or []):
            if isinstance(c, str):
                q.append(c)
    return False


def filter_selectors(
    run_dir: str,
    *,
    top_k: int = 5,
    types: Optional[List[str]] = None,  # ["content","control"]
    min_w: int = 96,
    min_h: int = 80,
    min_area: int = 20000,
    max_area_ratio: float = 0.6,
    min_children: int = 2,
    require_submit_in_subtree: bool = True,
    require_inner_kw: bool = True,
    inner_kws: Optional[List[str]] = None,
) -> List[str]:
    tree_path = os.path.join(run_dir, "controls_tree.json")
    if not os.path.exists(tree_path):
        return []
    doc = _read_json(tree_path)
    nodes = [n for n in (doc.get("nodes") or []) if isinstance(n, dict)]
    by_id: Dict[str, Dict[str, Any]] = {str(n.get("id")): n for n in nodes}
    vw, vh = _viewport(run_dir)
    max_area = max(1, int(float(max_area_ratio) * vw * vh))
    inner_kws = inner_kws or ["inner", "inner-wrap", "innerwrap", "list", "items"]
    # 过滤
    cand: List[Tuple[int, Dict[str, Any]]] = []
    for n in nodes:
        try:
            t = (n.get("type") or "").lower()
            if types and t not in [x.lower() for x in types]:
                continue
            kids = n.get("children") or []
            if len(kids) < int(min_children):
                continue
            x, y, w, h = _bbox(n)
            area = w * h
            if w < int(min_w) or h < int(min_h) or area < int(min_area) or area >= max_area:
                continue
            ratio = w / max(1, h)
            if ratio > 10 or (1 / ratio) > 10:
                continue
            if require_submit_in_subtree and not _has_submit_in_subtree(str(n.get("id")), by_id):
                continue
            sel = (n.get("selector") or "").lower()
            if require_inner_kw and not any(kw in sel for kw in inner_kws):
                continue
            cand.append((area, n))  # area 作为简单排序依据
        except Exception:
            continue
    cand.sort(key=lambda t: t[0], reverse=True)
    out: List[str] = []
    for _, n in cand[: int(max(1, top_k))]:
        s = (n.get("selector") or "").strip()
        if s:
            out.append(s)
    return out


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Filter selectors from controls_tree.json for skill generation")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--types", type=str, default="content,control")
    ap.add_argument("--min-w", type=int, default=96)
    ap.add_argument("--min-h", type=int, default=80)
    ap.add_argument("--min-area", type=int, default=20000)
    ap.add_argument("--max-area-ratio", type=float, default=0.6)
    ap.add_argument("--min-children", type=int, default=2)
    ap.add_argument("--require-submit", dest="require_submit", action="store_true")
    ap.add_argument("--no-require-submit", dest="require_submit", action="store_false")
    ap.add_argument("--require-inner-kw", dest="require_inner_kw", action="store_true")
    ap.add_argument("--no-require-inner-kw", dest="require_inner_kw", action="store_false")
    ap.set_defaults(require_submit=True, require_inner_kw=True)
    args = ap.parse_args()
    types = [t.strip().lower() for t in (args.types or "").split(',') if t.strip()]
    sels = filter_selectors(
        args.run_dir,
        top_k=args.top_k,
        types=types,
        min_w=args.min_w,
        min_h=args.min_h,
        min_area=args.min_area,
        max_area_ratio=args.max_area_ratio,
        min_children=args.min_children,
        require_submit_in_subtree=getattr(args, "require_submit", True),
        require_inner_kw=getattr(args, "require_inner_kw", True),
    )
    for s in sels:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

