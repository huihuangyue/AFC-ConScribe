"""
Refine preconditions deterministically using new run signals.

Rules:
  - Ensure exists contains final primary selector.
  - Build/refresh not_exists from overlay hits.
  - Keep viewport unchanged (or set minimal width 960 if missing).
"""

from __future__ import annotations

from typing import Any, Dict, List


def refine(skill: Dict[str, Any], diff_signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    ops: List[Dict[str, Any]] = []
    locs = skill.get("locators") or {}
    primary = locs.get("selector") or ""
    pre = skill.get("preconditions") or {}

    # ensure exists includes primary
    exists = list(pre.get("exists") or [])
    if primary and primary not in exists:
        ops.append({"op": "add", "path": "/preconditions/exists/-", "value": primary})

    # refresh not_exists from overlay hits
    hits = list(diff_signals.get("overlay_hits") or [])
    not_exists: List[str] = []
    if hits:
        if "modal" in hits:
            not_exists.append(".modal,.modal-mask,.ant-modal-wrap")
        if "mask" in hits or "backdrop" in hits:
            not_exists.append(".mask,.backdrop,.MuiBackdrop-root")
        if "overlay" in hits:
            not_exists.append(".overlay")
        if "dialog" in hits or "drawer" in hits:
            not_exists.append(".dialog,.drawer")
        if "toast" in hits or "snackbar" in hits:
            not_exists.append(".toast,.snackbar")
        if any(k in hits for k in ("loading", "spinner", "progress", "skeleton")):
            not_exists.append(".loading,.spinner,.progress,.skeleton")
    if not_exists:
        # replace whole list to keep it concise
        ops.append({"op": "replace", "path": "/preconditions/not_exists", "value": not_exists})

    # viewport minimal width default
    vp = pre.get("viewport") or {}
    if "min_width" not in vp:
        ops.append({"op": "add", "path": "/preconditions/viewport", "value": {"min_width": 960}})

    return ops


__all__ = ["refine"]

