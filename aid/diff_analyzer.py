"""
Diff analyzer between old/new detect runs (deterministic).

Outputs DiffSignals: selector existence, attribute/text/role differences,
and overlay hits based on class keywords.
"""

from __future__ import annotations

from typing import Any, Dict, List

OVERLAY_KEYWORDS = (
    "modal",
    "mask",
    "backdrop",
    "overlay",
    "dialog",
    "drawer",
    "popup",
    "toast",
    "tooltip",
    "snackbar",
    "loading",
    "spinner",
    "progress",
    "skeleton",
)


def _find_element_by_selector(dom_summary: Dict[str, Any], selector: str) -> Dict[str, Any]:
    # dom_summary doesn't include real CSS matching; we approximate by id/name/role/class heuristics.
    els = dom_summary.get("elements") or []
    if not selector:
        return {}
    # id match
    if selector.startswith('#'):
        target = selector[1:]
        for e in els:
            if (e.get("id") or "") == target:
                return e
    # [name=]
    if "[name=" in selector:
        try:
            name = selector.split("[name=")[1].split("]")[0].strip("'\"")
            for e in els:
                if (e.get("name") or "") == name:
                    return e
        except Exception:
            pass
    # role
    if "[role=" in selector:
        try:
            role = selector.split("[role=")[1].split("]")[0].strip("'\"")
            for e in els:
                if (e.get("role") or "") == role:
                    return e
        except Exception:
            pass
    # class chain
    if "." in selector:
        classes = [c for c in selector.split('.') if c and ('[' not in c)]
        for e in els:
            cls = str(e.get("class") or "").split()
            if all(c in cls for c in classes[1:]):
                return e
    return {}


def _overlay_hits(dom_summary: Dict[str, Any]) -> List[str]:
    els = dom_summary.get("elements") or []
    hits = set()
    for e in els:
        cls = (e.get("class") or "").lower()
        for k in OVERLAY_KEYWORDS:
            if k in cls:
                hits.add(k)
    return sorted(hits)


def analyze(skill: Dict[str, Any], old_run: Dict[str, Any], new_run: Dict[str, Any]) -> Dict[str, Any]:
    """Return DiffSignals.

    Keys:
      selector_alive (bool)
      overlay_hits (list[str])
      role_changed/text_changed (bool)
      element_new (bool)
    """
    sel = ((skill.get("locators") or {}).get("selector") or "")
    new_el = _find_element_by_selector(new_run.get("dom_summary") or {}, sel)
    old_el = _find_element_by_selector(old_run.get("dom_summary") or {}, sel)
    selector_alive = bool(new_el)
    role_changed = False
    text_changed = False
    element_new = False
    if new_el and not old_el:
        element_new = True
    if new_el and old_el:
        role_changed = (str(new_el.get("role") or "") != str(old_el.get("role") or ""))
        # compare short text
        nt = str(new_el.get("text") or "")[:64]
        ot = str(old_el.get("text") or "")[:64]
        text_changed = (nt != ot)
    return {
        "selector_alive": selector_alive,
        "overlay_hits": _overlay_hits(new_run.get("dom_summary") or {}),
        "role_changed": role_changed,
        "text_changed": text_changed,
        "element_new": element_new,
    }


__all__ = ["analyze"]

