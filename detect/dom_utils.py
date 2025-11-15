from __future__ import annotations

import os
import json
from typing import Any, Dict, List
try:  # 优先包内相对导入
    from .constants import ARTIFACTS, DEFAULT_VIEWPORT  # type: ignore
    from .utils import write_json  # type: ignore
except Exception:  # 兼容脚本直接运行
    from constants import ARTIFACTS, DEFAULT_VIEWPORT  # type: ignore
    from utils import write_json  # type: ignore


def _wait_images(page, timeout_ms: int = 30000) -> None:
    try:
        page.wait_for_function(
            "() => { const imgs = Array.from(document.images).filter(img => { const r = img.getBoundingClientRect(); const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0); const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0); return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<vh && r.left<vw; }).slice(0,256); return imgs.length===0 || imgs.every(img => img.complete && img.naturalWidth>0 && img.naturalHeight>0); }",
            timeout=max(1, int(timeout_ms)),
        )
    except Exception:
        pass


def _wait_backgrounds(page, limit: int = 256, timeout_ms: int = 5000) -> None:
    try:
        page.evaluate("async (p) => { return await (window.DetectHelpers && window.DetectHelpers.waitViewportBackgrounds ? window.DetectHelpers.waitViewportBackgrounds(p.limit, p.timeout) : true); }", {"limit": limit, "timeout": timeout_ms})
    except Exception:
        pass


def perform_scrolled_phase(
    page,
    out_dir: str,
    *,
    ensure_images_loaded: bool = True,
    images_wait_timeout_ms: int = 30000,
    ensure_backgrounds_loaded: bool = True,
    autoscroll_max_steps: int = 3,
    autoscroll_delay_ms: int = 1200,
    prefetch_positions: int = 5,
) -> Dict[str, Any]:
    """Scroll the page to reveal lazy content, take tail screenshot, and write dom_summary_scrolled and diff.

    Returns { scrolled_count, new_count, dom_summary_scrolled }.
    """
    # Prefetch several positions for better coverage
    try:
        last = page.evaluate("() => Math.max(document.body?.scrollHeight||0, document.documentElement?.scrollHeight||0)")
        vh = page.evaluate("() => Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0)")
        N = max(1, int(prefetch_positions or 1))
        positions = [max(0, min(int(last or 0), int(round(i * (last or 0) / (N - 1) if N > 1 else 0)))) for i in range(N)]
        for pos in positions:
            try:
                page.evaluate("(y)=>window.scrollTo(0,y)", int(pos))
                page.wait_for_timeout(200)
                if ensure_images_loaded:
                    _wait_images(page, images_wait_timeout_ms)
                if ensure_backgrounds_loaded:
                    _wait_backgrounds(page)
            except Exception:
                continue
    except Exception:
        pass
    # Autoscroll few more steps
    for _ in range(max(0, int(autoscroll_max_steps or 0))):
        try:
            page.evaluate("() => { const h = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0); window.scrollBy(0, Math.max(64, Math.floor(h*0.9))); }")
            page.wait_for_timeout(int(max(0, autoscroll_delay_ms or 0)))
            if ensure_images_loaded:
                _wait_images(page, images_wait_timeout_ms)
            if ensure_backgrounds_loaded:
                _wait_backgrounds(page)
        except Exception:
            break
    # Tail screenshot
    try:
        page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"]), full_page=True)
    except Exception:
        pass
    # dom_summary_scrolled
    try:
        dom_summary_scrolled = page.evaluate(
            "(p) => window.DetectHelpers && window.DetectHelpers.getDomSummaryAdvanced ? window.DetectHelpers.getDomSummaryAdvanced(p.limit, p.opts) : []",
            {"limit": 20000, "opts": {"occlusionStep": 8}},
        )
    except Exception:
        dom_summary_scrolled = []
    try:
        write_json(os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"]), {"count": len(dom_summary_scrolled) if isinstance(dom_summary_scrolled, list) else 0, "viewport": DEFAULT_VIEWPORT, "elements": dom_summary_scrolled})
    except Exception:
        pass
    # Diff new elements compared to initial dom_summary.json
    try:
        base_path = os.path.join(out_dir, ARTIFACTS["dom_summary"])
        base = []
        if os.path.exists(base_path):
            with open(base_path, "r", encoding="utf-8") as f:
                doc = json.load(f) or {}
            if isinstance(doc.get("elements"), list):
                base = doc.get("elements")
        new_count = write_dom_scrolled_diff(out_dir, base=base, scrolled=dom_summary_scrolled or [], diff_path=os.path.join(out_dir, ARTIFACTS["dom_scrolled_new"]))
    except Exception:
        new_count = 0
    return {"scrolled_count": len(dom_summary_scrolled or []), "new_count": new_count, "dom_summary_scrolled": dom_summary_scrolled}



def _fp(e: Dict[str, Any]) -> str:
    def s(v):
        return "" if v is None else str(v)
    bb = e.get("bbox") or [0, 0, 0, 0]
    return "|".join([
        s(e.get("tag")), s(e.get("id")), s(e.get("class")), s(e.get("role")), s(e.get("name")),
        (e.get("text") or "")[:80], f"{bb[0]}-{bb[1]}-{bb[2]}-{bb[3]}"
    ])


def merge_elements_for_tree(out_dir: str, *, base_path: str, scrolled_path: str) -> List[Dict[str, Any]]:
    """Merge dom_summary + dom_summary_scrolled elements uniquely for controls_tree.

    Returns a list of element dicts; missing files are tolerated.
    """
    parts: List[List[Dict[str, Any]]] = []
    for p in (scrolled_path, base_path):
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    doc = json.load(f) or {}
                if isinstance(doc.get("elements"), list):
                    parts.append(doc.get("elements"))
        except Exception:
            continue
    if not parts:
        return []
    seen = set()
    merged: List[Dict[str, Any]] = []
    for lst in parts:
        for e in lst or []:
            key = _fp(e)
            if key in seen:
                continue
            seen.add(key)
            merged.append(e)
    return merged


def write_dom_scrolled_diff(out_dir: str, *, base: List[Dict[str, Any]], scrolled: List[Dict[str, Any]], diff_path: str) -> int:
    """Compute and write dom_scrolled_new diff file; return new_count.
    Tolerates errors by writing minimal info.
    """
    from .utils import write_json  # lazy import to avoid cyclic
    try:
        base_set = set(_fp(x) for x in (base or []))
        only_scrolled = [e for e in (scrolled or []) if _fp(e) not in base_set]
        new_count = len(only_scrolled)
        write_json(diff_path, {
            "initial_count": len(base or []),
            "scrolled_count": len(scrolled or []),
            "new_count": new_count,
            "new_elements": only_scrolled,
        })
        return new_count
    except Exception:
        try:
            write_json(diff_path, {"initial_count": len(base or []), "scrolled_count": len(scrolled or []), "new_count": 0, "new_elements": []})
        except Exception:
            pass
        return 0
