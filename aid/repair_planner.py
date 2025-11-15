"""
Repair planner: combine deterministic modules to produce a repaired skill JSON.

Pipeline (no LLM):
  1) Diagnose mismatch/damage
  2) If damage or unknown, propose locator patches and preconditions refinement
  3) Apply patches in order and validate
"""

from __future__ import annotations

from typing import Any, Dict

from .diff_analyzer import analyze
from .diagnostic_core import diagnose
from .locator_repair import propose as propose_locators
from .preconditions_refiner import refine as refine_pre
from .patch_ops import apply_patch
from .validation_runner import validate_skill


def plan_and_apply(skill: Dict[str, Any], old_run: Dict[str, Any], new_run: Dict[str, Any]) -> Dict[str, Any]:
    diag = diagnose(skill, old_run, new_run)
    # Always compute diff
    diff = analyze(skill, old_run, new_run)

    # Collect patches (deterministic only)
    patches = []
    patches += propose_locators(skill, new_run)
    patches.append({"kind": "preconditions", "ops": refine_pre(skill, diff)})

    # Apply
    out = dict(skill)
    for p in patches:
        ops = p.get("ops") or []
        if not ops:
            continue
        out = apply_patch(out, ops)

    # Validate
    errs = validate_skill(out)
    out.setdefault("meta", {})
    out["meta"]["repair_notes"] = {
        "diagnostic": diag,
        "errors": errs,
    }
    return out


__all__ = ["plan_and_apply"]

