from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

try:  # pragma: no cover
    from .utils import write_json  # type: ignore
    from .constants import ARTIFACTS  # type: ignore
except Exception:  # pragma: no cover
    from utils import write_json  # type: ignore
    from constants import ARTIFACTS  # type: ignore


def _write_tip_file(base_dir: str, out_dir: str, nid: str, selector: str, ntype: str, html: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{nid}.html")
    with open(path, "w", encoding="utf-8") as fo:
        fo.write(f"<!-- id={nid} type={ntype} selector={selector} -->\n")
        fo.write(html or "")
    return os.path.relpath(path, base_dir).replace("\\", "/")


def write_tips(page, out_dir: str, controls_tree_path: str) -> Tuple[int, str]:
    """Export tips/ for all nodes using JS batch; fallback to per-element.

    Returns (count, tips_index_path).
    """
    import json
    tips_dir = os.path.join(out_dir, ARTIFACTS["tips_dir"])
    os.makedirs(tips_dir, exist_ok=True)
    # load nodes
    try:
        with open(controls_tree_path, "r", encoding="utf-8") as f:
            tree_doc = json.load(f)
        nodes = [n for n in (tree_doc.get("nodes") or []) if isinstance(n, dict)]
    except Exception:
        nodes = []
    items = [{"id": (n.get("id") or ""), "selector": (n.get("selector") or ""), "type": (n.get("type") or "")} for n in nodes if (n.get("id") and n.get("selector"))]
    tips_index: List[Dict[str, Any]] = []
    # JS batch first
    js_res = None
    try:
        js_res = page.evaluate("items => window.DetectHelpers && window.DetectHelpers.getOuterHTMLs && window.DetectHelpers.getOuterHTMLs(items)", items)
    except Exception:
        js_res = None
    if isinstance(js_res, dict) and js_res.get("ok") and isinstance(js_res.get("items"), list):
        for it in (js_res.get("items") or []):
            try:
                nid = it.get("id") or ""
                sel = it.get("selector") or ""
                ntype = it.get("type") or ""
                if not nid or not sel:
                    continue
                if it.get("found"):
                    rel = _write_tip_file(out_dir, tips_dir, nid, sel, ntype, it.get("html") or "")
                    tips_index.append({"id": nid, "selector": sel, "type": ntype, "file": rel, "found": True})
                else:
                    ent = {"id": nid, "selector": sel, "type": ntype, "found": False}
                    if it.get("error"):
                        ent["error"] = it.get("error")
                    tips_index.append(ent)
            except Exception as ex:
                tips_index.append({"id": it.get("id"), "selector": it.get("selector"), "type": it.get("type"), "found": False, "error": str(ex)})
    else:
        # fallback per element
        for n in nodes:
            sel = n.get("selector") or ""
            nid = n.get("id") or ""
            ntype = n.get("type") or ""
            if not sel or not nid:
                continue
            try:
                el = page.query_selector(sel)
                if not el:
                    tips_index.append({"id": nid, "selector": sel, "type": ntype, "found": False})
                    continue
                html = el.evaluate("e => e.outerHTML")
                rel = _write_tip_file(out_dir, tips_dir, nid, sel, ntype, html or "")
                tips_index.append({"id": nid, "selector": sel, "type": ntype, "file": rel, "found": True})
            except Exception as ex:
                tips_index.append({"id": nid, "selector": sel, "type": ntype, "found": False, "error": str(ex)})
    # write index
    idx_path = os.path.join(out_dir, ARTIFACTS["tips_index"])
    write_json(idx_path, {"count": len(tips_index), "items": tips_index})
    return len(tips_index), idx_path


def write_snippets_first_layer(page, out_dir: str, controls_tree_path: str) -> int:
    """Export first-layer control snippets grouped by action. Returns count written."""
    import json
    base_dir = os.path.join(out_dir, ARTIFACTS["snippets_dir"], "first_layer_by_action")
    os.makedirs(base_dir, exist_ok=True)
    try:
        with open(controls_tree_path, "r", encoding="utf-8") as tf:
            tree = json.load(tf)
        nodes = [n for n in (tree.get("nodes") or []) if isinstance(n, dict)]
    except Exception:
        nodes = []
    first_layer = [n for n in nodes if n.get("type") == "control" and (n.get("parent") is None)]
    def _y(n):
        try:
            return int((n.get("geom") or {}).get("bbox", [0, 0, 0, 0])[1] or 0)
        except Exception:
            return 0
    first_layer.sort(key=_y)
    items = [{"id": str(n.get("id") or ""), "selector": str(n.get("selector") or ""), "type": str(n.get("type") or "")} for n in first_layer if n.get("selector")]
    js_snip = None
    try:
        js_snip = page.evaluate("items => window.DetectHelpers && window.DetectHelpers.getOuterHTMLs && window.DetectHelpers.getOuterHTMLs(items)", items)
    except Exception:
        js_snip = None
    written = 0
    if isinstance(js_snip, dict) and js_snip.get("ok") and isinstance(js_snip.get("items"), list):
        for it in js_snip.get("items") or []:
            try:
                sel = it.get("selector")
                fid = it.get("id") or "unknown"
                action = (next((n.get("action") for n in first_layer if n.get("id") == fid), "unknown") or "unknown").lower()
                cat_dir = os.path.join(base_dir, action)
                os.makedirs(cat_dir, exist_ok=True)
                if it.get("found"):
                    fpath = os.path.join(cat_dir, f"{fid}.html")
                    with open(fpath, "w", encoding="utf-8") as fo:
                        fo.write((it.get("html") or ""))
                    written += 1
                else:
                    # leave a warning file for debugging
                    pass
            except Exception:
                continue
    else:
        for n in first_layer:
            sel = n.get("selector")
            if not sel:
                continue
            action = (n.get("action") or "unknown").lower()
            cat_dir = os.path.join(base_dir, action)
            os.makedirs(cat_dir, exist_ok=True)
            try:
                el = page.query_selector(sel)
                if not el:
                    continue
                html = el.evaluate("e => e.outerHTML")
                fid = str(n.get("id") or "unknown")
                fpath = os.path.join(cat_dir, f"{fid}.html")
                with open(fpath, "w", encoding="utf-8") as fo:
                    fo.write(html or "")
                written += 1
            except Exception:
                continue
    return written

