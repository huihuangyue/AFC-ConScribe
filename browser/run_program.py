#!/usr/bin/env python3
"""
Run a Skill Program (program.code) inside a Playwright browser environment.

Usage:
  python -m browser.run_program --skill <skill.json> [--url <start_url>] [--args-json '{"text":"上海"}'] [--no-headless]

Outputs:
  - Prints metrics: TTF(s), ok/message, final URL, viewport size
  - Exit code 0 on ok=True, else 1
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, Optional

from .env import make_env


SAFE_BUILTINS = {
    "len": len,
    "range": range,
    "min": min,
    "max": max,
    "sum": sum,
    "enumerate": enumerate,
    "any": any,
    "all": all,
    "sorted": sorted,
    "map": map,
    "filter": filter,
    "zip": zip,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "__import__": __import__,
    "Exception": Exception,
    "BaseException": BaseException,
}


def _load_skill(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _compile_program(code: str) -> Dict[str, Any]:
    ns: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
    exec(compile(code, "<skill_program>", "exec"), ns, ns)
    return ns


def _choose_entry(ns: Dict[str, Any]) -> str:
    for k, v in ns.items():
        if k.startswith("program__") and callable(v):
            return k
    if callable(ns.get("program")):
        return "program"
    raise RuntimeError("no program entry defined in program.code")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run a Skill Program inside a browser env")
    ap.add_argument("--skill", required=True, help="Path to skill JSON with program.code")
    ap.add_argument("--url", default=None, help="Optional start URL to goto before running program")
    ap.add_argument("--no-headless", dest="headless", action="store_false", help="Run headed browser")
    ap.add_argument("--args-json", default=None, help="JSON string for Program args (default: {})")
    ap.set_defaults(headless=True)
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    skill = _load_skill(args.skill)
    locators = skill.get("locators") or {}
    prog = (skill.get("program") or {}).get("code") or ""
    if not prog.strip():
        print("[ERROR] program.code is empty")
        return 2
    try:
        args_obj = json.loads(args.args_json) if args.args_json else {}
    except Exception as e:
        print(f"[ERROR] args-json parse error: {e}")
        return 2

    ns = _compile_program(prog)
    entry_name = _choose_entry(ns)
    entry = ns[entry_name]

    t0 = time.perf_counter()
    with make_env(args.url, headless=args.headless) as env:
        try:
            res = entry(env, locators, args_obj, {"run_id": f"run-{int(time.time())}"})
        except Exception as e:
            dt = time.perf_counter() - t0
            print(f"[METRIC] TTF(s)={dt:.2f}")
            print(f"[RESULT] ok=false message=PROGRAM_ERROR: {e}")
            print(f"[ENV] url={env.current_url()} viewport={env.viewport_size()}")
            return 1
    dt = time.perf_counter() - t0
    ok = False
    msg = ""
    if isinstance(res, dict):
        ok = bool(res.get("ok"))
        msg = str(res.get("message", ""))
    print(f"[METRIC] TTF(s)={dt:.2f}")
    print(f"[RESULT] ok={str(ok).lower()} message={msg}")
    print(f"[ENV] url={res.get('final_url') if isinstance(res, dict) else '' or env.current_url()} viewport={env.viewport_size()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
