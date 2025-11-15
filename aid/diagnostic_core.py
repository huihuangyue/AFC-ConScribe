"""
Diagnostic core (no LLM): decide mismatch vs damage by checking preconditions
against new run artifacts.

Heuristic:
  - If any preconditions.exists selector cannot be found in new dom_summary -> mismatch
  - Else if primary selector appears to change (role/text changed) -> damage
  - Else -> damage (default)
"""

from __future__ import annotations

from typing import Any, Dict, List

from .diff_analyzer import analyze


def _exists(dom_summary: Dict[str, Any], selector: str) -> bool:
    els = dom_summary.get("elements") or []
    if selector.startswith('#'):
        idv = selector[1:]
        return any((e.get("id") or "") == idv for e in els)
    if "[name=" in selector:
        try:
            name = selector.split("[name=")[1].split("]")[0].strip("'\"")
            return any((e.get("name") or "") == name for e in els)
        except Exception:
            return False
    if "[role=" in selector:
        try:
            role = selector.split("[role=")[1].split("]")[0].strip("'\"")
            return any((e.get("role") or "") == role for e in els)
        except Exception:
            return False
    # naive class chain
    if "." in selector:
        classes = [c for c in selector.split('.') if c and ('[' not in c)]
        for e in els:
            cls = str(e.get("class") or "").split()
            if all(c in cls for c in classes[1:]):
                return True
    return False


def diagnose(skill: Dict[str, Any], old_run: Dict[str, Any], new_run: Dict[str, Any]) -> Dict[str, Any]:
    locs = skill.get("locators") or {}
    pre = skill.get("preconditions") or {}
    ds_new = new_run.get("dom_summary") or {}
    # check preconditions.exists
    missing: List[str] = []
    for sel in list(pre.get("exists") or []):
        if not _exists(ds_new, sel):
            missing.append(sel)
    diff = analyze(skill, old_run, new_run)
    if missing:
        res = {
            "root_cause": "mismatch",
            "signals": {"missing_exists": missing, **diff},
            "notes": "preconditions.exists not satisfied on new snapshot",
        }
    else:
        res = {
            "root_cause": "damage",
            "signals": diff,
            "notes": "exists satisfied; locators/program may require repair",
        }
    return res


__all__ = ["diagnose"]

