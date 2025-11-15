"""
Validation runner (deterministic): light-weight checks without executing browsers.

Checks:
  - Required fields in skill JSON
  - Presence of primary locator and preconditions.url_matches/exists
  - Program presence (string; content may be empty in repair pipeline)
"""

from __future__ import annotations

from typing import Any, Dict, List


def validate_skill(skill: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    for k in ("id", "domain", "action", "locators", "preconditions", "program", "meta"):
        if k not in skill:
            errs.append(f"missing field: {k}")
    locs = skill.get("locators") or {}
    if not locs.get("selector"):
        errs.append("locators.selector required")
    pre = skill.get("preconditions") or {}
    if not pre.get("url_matches"):
        errs.append("preconditions.url_matches required")
    if not pre.get("exists"):
        errs.append("preconditions.exists required")
    prog = skill.get("program") or {}
    if prog.get("language") != "python":
        errs.append("program.language must be python")
    if not isinstance(prog.get("entry"), str):
        errs.append("program.entry required")
    if "code" not in prog:
        errs.append("program.code required (can be empty string)")
    return errs


__all__ = ["validate_skill"]

