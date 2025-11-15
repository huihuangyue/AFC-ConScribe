"""
Deterministic locator repair proposals for a skill, based on new run artifacts.

Produces a list of LocatorPatch objects (simple dicts) ordered by robustness.
Each patch suggests add/replace on /locators fields.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _stable_classes(class_str: str) -> List[str]:
    parts = str(class_str or "").split()
    good: List[str] = []
    for c in parts:
        if len(c) > 30:
            continue
        letters = sum(ch.isalpha() for ch in c)
        digits = sum(ch.isdigit() for ch in c)
        if digits > letters and digits > 3:
            continue
        good.append(c)
        if len(good) >= 2:
            break
    return good


def _candidates_from_element(el: Dict[str, Any]) -> List[str]:
    tag = (el.get("tag") or "").lower() or "*"
    sels: List[str] = []
    if el.get("id"):
        sels.append(f"#{el['id']}")
    if el.get("name"):
        sels.append(f"{tag}[name='{el['name']}']")
    if el.get("role"):
        sels.append(f"{tag}[role='{el['role']}']")
    cls = _stable_classes(el.get("class") or "")
    if cls:
        sels.append(f"{tag}.{'.'.join(cls)}")
    return sels


def propose(skill: Dict[str, Any], new_run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of LocatorPatch candidates (dicts with ops field)."""
    ds = new_run.get("dom_summary") or {}
    els = ds.get("elements") or []
    locs = skill.get("locators") or {}
    sel = locs.get("selector") or ""
    target = None
    # naive match by id/name/class/role for current primary
    if sel.startswith('#'):
        idv = sel[1:]
        for e in els:
            if (e.get("id") or "") == idv:
                target = e
                break
    if target is None and "[name=" in sel:
        try:
            name = sel.split("[name=")[1].split("]")[0].strip("'\"")
            for e in els:
                if (e.get("name") or "") == name:
                    target = e
                    break
        except Exception:
            pass
    if target is None:
        # fallback: first visible control-like element from controls_tree.json alignment is out of scope here
        if els:
            target = els[0]

    if not target:
        return []

    cand = _candidates_from_element(target)
    ops_list: List[Dict[str, Any]] = []
    if not cand:
        return []

    primary = cand[0]
    alts = [c for c in cand[1:] if c != sel][:3]
    patch_ops: List[Dict[str, Any]] = []
    if primary and primary != sel:
        # replace primary, push old primary into selector_alt
        patch_ops.append({"op": "replace", "path": "/locators/selector", "value": primary})
        if sel:
            patch_ops.append({"op": "add", "path": "/locators/selector_alt/-", "value": sel})
    for a in alts:
        patch_ops.append({"op": "add", "path": "/locators/selector_alt/-", "value": a})

    return [{"kind": "locators", "ops": patch_ops, "meta": {"reason": "deterministic locator update"}}]


__all__ = ["propose"]

