"""
Refine preconditions deterministically using new run signals.

Rules:
  - Ensure exists contains final primary selector.
  - Build/refresh not_exists from overlay hits.
  - Keep viewport unchanged (or set minimal width/height if missing).
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
        # 去重并保持稳定顺序，避免列表随修复次数膨胀
        dedup: List[str] = []
        seen: set[str] = set()
        for s in not_exists:
            if s and s not in seen:
                seen.add(s)
                dedup.append(s)
        not_exists = dedup
        # replace whole list to keep it concise
        ops.append({"op": "replace", "path": "/preconditions/not_exists", "value": not_exists})

    # viewport: 若已存在则补全缺失字段；否则给出保守基线
    vp = pre.get("viewport") or {}
    if not vp:
        # 没有 viewport 时，沿用旧行为但显式写出字段
        ops.append({"op": "add", "path": "/preconditions/viewport", "value": {"min_width": 960}})
    else:
        if "min_width" not in vp:
            ops.append({"op": "add", "path": "/preconditions/viewport/min_width", "value": 960})
        # 高度在旧技能中通常缺失，这里给一个温和下界，避免极端矮视口
        if "min_height" not in vp:
            ops.append({"op": "add", "path": "/preconditions/viewport/min_height", "value": 400})

    return ops


__all__ = ["refine"]
