"""
Programmatic API for repairing a skill JSON.

from aid.api import repair_skill

Usage:
  repaired, out_path = repair_skill(
      skill_path_or_obj,
      new_run_dir,
      old_run_dir=None,
      out_path=None,
      in_place=False,
      use_llm_locators=False,
      use_llm_preconditions=False,
      use_llm_program=False,
      use_llm_naming=False,
  )

Returns the repaired skill dict and the output path (if written), otherwise None.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .io import read_json, write_json, load_run_artifacts
from .repair_planner import plan_and_apply
from .patch_ops import apply_patch


def repair_skill(
    skill: str | Dict[str, Any],
    new_run_dir: str,
    *,
    old_run_dir: Optional[str] = None,
    out_path: Optional[str] = None,
    in_place: bool = False,
    use_llm_locators: bool = False,
    use_llm_preconditions: bool = False,
    use_llm_program: bool = False,
    use_llm_naming: bool = False,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Repair a broken skill using deterministic pipeline + optional LLM steps.

    Params
      skill: path to skill.json or already loaded skill dict
      new_run_dir: path to detect run dir (current snapshot)
      old_run_dir: path to old detect run dir (defaults to skill.meta.source_dir or new_run_dir)
      out_path: if provided, write the repaired JSON to this path
      in_place: overwrite the input file (only when `skill` is a path)
      use_llm_*: enable LLM-based refinements
    Returns: (repaired_skill_dict, written_path_or_None)
    """
    # Load inputs
    if isinstance(skill, str):
        skill_obj = read_json(skill)
        skill_path = skill
    else:
        skill_obj = dict(skill)
        skill_path = None

    if not old_run_dir:
        old_run_dir = (skill_obj.get("meta") or {}).get("source_dir") or new_run_dir

    old_art = load_run_artifacts(old_run_dir)
    new_art = load_run_artifacts(new_run_dir)

    # Deterministic repair first
    repaired = plan_and_apply(skill_obj, old_art, new_art)

    # Optional LLM refinements
    if any([use_llm_locators, use_llm_preconditions, use_llm_program, use_llm_naming]):
        from .llm_repair import (
            llm_locators,
            llm_preconditions,
            llm_program_fix,
            llm_naming,
        )
        ops: list[dict] = []
        if use_llm_locators:
            ops += llm_locators(repaired, new_run_dir, new_art)
        if use_llm_preconditions:
            from .diff_analyzer import analyze
            diff = analyze(repaired, old_art, new_art)
            ops += llm_preconditions(repaired, diff)
        if use_llm_program:
            ops += llm_program_fix(repaired, new_run_dir)
        if use_llm_naming:
            ops += llm_naming(repaired, new_run_dir)
        if ops:
            repaired = apply_patch(repaired, ops)

    # Write if requested
    write_to: Optional[str] = None
    if out_path:
        write_json(out_path, repaired)
        write_to = out_path
    elif in_place and skill_path:
        write_json(skill_path, repaired)
        write_to = skill_path

    return repaired, write_to


__all__ = ["repair_skill"]

