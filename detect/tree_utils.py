from __future__ import annotations

import os
import json
from typing import Any, Dict, List, Optional

try:  # pragma: no cover
    from .constants import ARTIFACTS
    from .dom_utils import merge_elements_for_tree
    from .controls_tree import write_controls_tree
except Exception:  # pragma: no cover
    from constants import ARTIFACTS  # type: ignore
    from dom_utils import merge_elements_for_tree  # type: ignore
    from controls_tree import write_controls_tree  # type: ignore


def _to_list(v: Optional[Any]) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, str):
        return [x.strip() for x in v.split(',') if x.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return None


def build_and_write_controls_tree(
    out_dir: str,
    *,
    expand_to_container: bool = False,
    inflate_px: int = 0,
    force_include_ids: Optional[List[str]] = None,
    force_include_selectors: Optional[List[str]] = None,
    include_roles: Optional[List[str]] = None,
    include_class_kw: Optional[List[str]] = None,
    include_min_controls: int = 3,
) -> str:
    """Merge DOM summaries and write controls_tree.json. Returns path to controls_tree.json."""
    controls_out = os.path.join(out_dir, ARTIFACTS["controls_tree"])
    elements_for_tree = merge_elements_for_tree(
        out_dir,
        base_path=os.path.join(out_dir, ARTIFACTS["dom_summary"]),
        scrolled_path=os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"]),
    )
    # fallback: try to load in-memory dom_summary_scrolled/dom_summary via artifacts if merge failed
    if not elements_for_tree:
        try:
            with open(os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"]), "r", encoding="utf-8") as f:
                doc = json.load(f) or {}
            elements_for_tree = (doc.get("elements") or []) if isinstance(doc.get("elements"), list) else []
        except Exception:
            elements_for_tree = []
        if not elements_for_tree:
            try:
                with open(os.path.join(out_dir, ARTIFACTS["dom_summary"]), "r", encoding="utf-8") as f:
                    doc = json.load(f) or {}
                elements_for_tree = (doc.get("elements") or []) if isinstance(doc.get("elements"), list) else []
            except Exception:
                elements_for_tree = []
    if not elements_for_tree:
        raise RuntimeError("no elements available for controls tree")

    write_controls_tree(
        elements_for_tree,
        controls_out,
        only_visible=False,
        filter_occluded=False,
        occ_threshold=0.98,
        expand_to_container=bool(expand_to_container),
        inflate_px=int(inflate_px or 0),
        force_include_ids=_to_list(force_include_ids),
        force_include_selectors=_to_list(force_include_selectors),
        auto_include_roles=_to_list(include_roles),
        auto_include_class_keywords=_to_list(include_class_kw),
        min_controls_in_subtree=int(include_min_controls or 3),
    )
    return controls_out


def live_outline_controls(
    page,
    controls_tree_path: str,
    *,
    limit: int = 200,
    color: str = "rgba(255,0,0,0.9)",
    width_px: int = 2,
) -> int:
    """Outline top-N control selectors on page for visual debugging. Returns count."""
    try:
        with open(controls_tree_path, "r", encoding="utf-8") as f:
            tree_doc = json.load(f) or {}
        nodes = [n for n in (tree_doc.get("nodes") or []) if isinstance(n, dict) and n.get("type") == "control"]
        def _y(n):
            try:
                return int((n.get("geom") or {}).get("bbox", [0,0,0,0])[1] or 0)
            except Exception:
                return 0
        nodes.sort(key=_y)
        selectors: List[str] = []
        seen = set()
        for n in nodes:
            sel = n.get("selector")
            if not sel or sel in seen:
                continue
            seen.add(sel)
            selectors.append(sel)
            if len(selectors) >= int(limit or 0):
                break
        if selectors:
            page.evaluate(
                "(p)=>{ const sels=p.sels||[]; const col=p.color||'rgba(255,0,0,0.9)'; const w=Math.max(1,Number(p.width)||2);\n"
                "for(const s of sels){ try{ const el=document.querySelector(s); if(!el) continue; el.style.setProperty('outline', w+'px solid '+col, 'important'); el.setAttribute('data-afc-live-outline','1'); }catch(_){} } }",
                {"sels": selectors, "color": str(color), "width": int(width_px)},
            )
        return len(selectors)
    except Exception:
        return 0

