from __future__ import annotations

import os
import re
from typing import Dict, Any, Optional


def _detect_main_func_name(code: str) -> Optional[str]:
    """Return the first likely public function name in the code string.

    Strategy: the first `def <name>(` whose name does not start with underscore.
    """
    pat = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.M)
    for m in pat.finditer(code or ""):
        name = m.group(1)
        if not name.startswith("_"):
            return name
    return None


def export_program_py(
    skill_obj: Dict[str, Any],
    skill_path: str,
    *,
    out_dir: Optional[str] = None,
    overwrite: bool = False,
) -> str:
    """Export program.code (or top-level code) from a skill JSON object to a .py file.

    - The filename defaults to <main_func>.py where <main_func> is inferred from code.
    - If main function cannot be detected, fallback to program_<id>.py
    - By default, writes next to the skill JSON; can override via out_dir.
    - If overwrite=False and file exists, auto-suffix _1, _2, ... to avoid collision.
    Returns the written absolute path.
    """
    prog = (skill_obj.get("program") or {}) if isinstance(skill_obj, dict) else {}
    code = (
        (prog.get("code") if isinstance(prog, dict) else None)
        or (skill_obj.get("code") if isinstance(skill_obj, dict) else None)
        or ""
    )
    if not code:
        raise RuntimeError("skill has no program.code or top-level code to export")

    main_name = _detect_main_func_name(code) or f"program_{skill_obj.get('id','unknown')}"
    base_dir = out_dir or os.path.dirname(os.path.abspath(skill_path))
    os.makedirs(base_dir, exist_ok=True)
    target = os.path.join(base_dir, f"{main_name}.py")
    if not overwrite and os.path.exists(target):
        # auto-unique: append _n
        n = 1
        while True:
            cand = os.path.join(base_dir, f"{main_name}_{n}.py")
            if not os.path.exists(cand):
                target = cand
                break
            n += 1

    with open(target, "w", encoding="utf-8") as f:
        f.write(code)
    return target

