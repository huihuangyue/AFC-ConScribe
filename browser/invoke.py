#!/usr/bin/env python3
"""
Invoke a specific function from a Skill's program.code with a custom call string.

Features:
- Headed run by default with slow_mo for human-visible actions
- Does NOT auto-close the browser when --keep-open is set (default on)
- Accepts an invocation string like:
    search_hotel(
        html_content,
        destination="上海",
        checkin_year=2025, checkin_month=7, checkin_day=10,
        checkout_year=2025, checkout_month=7, checkout_day=15,
        rooms=1, adults=2, children=0,
        star_ratings=["五星（钻）"],
        keyword="外滩"
    )

Usage:
  python -m browser.invoke --skill <skill.json> --invoke "<python_call_string>" \
    [--url <start_url>] [--html-file <path>] [--vars-json '{"x":1}'] \
    [--slow-mo-ms 120] [--default-timeout-ms 10000] [--no-keep-open]
"""

from __future__ import annotations

import argparse
import json
import os
import textwrap
from typing import Any, Dict, Optional

from .env import make_env
import re


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
    "ValueError": ValueError,
    # 允许在技能程序中显式使用常见异常类型（与提示文档保持一致）
    "LookupError": LookupError,
    "BaseException": BaseException,
}


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _compile_program(code: str) -> Dict[str, Any]:
    ns: Dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
    exec(compile(code, "<skill_program>", "exec"), ns, ns)
    return ns


def _derive_start_url(skill: Dict[str, Any]) -> Optional[str]:
    """Derive a reasonable start URL from a skill JSON.

    Priority:
      1) skill.meta.url (if present)
      2) skill.domain → https://<domain>/
      3) skill.preconditions.url_matches[0] → extract host → https://<host>/
    """
    meta = skill.get("meta") or {}
    url = meta.get("url") or ""
    if isinstance(url, str) and url.startswith("http"):
        return url
    domain = (skill.get("domain") or meta.get("domain") or "").strip()
    if domain:
        return f"https://{domain}/"
    try:
        pre = skill.get("preconditions") or {}
        um = pre.get("url_matches") or []
        if um and isinstance(um, list):
            pat = str(um[0])
            m = re.search(r"https?://([^/]+)/", pat)
            if m:
                host = m.group(1)
                if host:
                    return f"https://{host}/"
    except Exception:
        pass
    return None


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Invoke program function with a custom call string in a headed browser")
    ap.add_argument("--skill", required=True, help="Path to skill JSON with program.code")
    ap.add_argument("--invoke", required=True, help="Python call string, possibly containing \n for line breaks")
    ap.add_argument("--url", default=None, help="Optional start URL to goto before running")
    ap.add_argument("--slow-mo-ms", type=int, default=120, help="Slow motion ms to visually demonstrate interactions (default: 120)")
    ap.add_argument("--default-timeout-ms", type=int, default=10000, help="Default Playwright timeout in ms (default: 10000)")
    ap.add_argument("--keep-open", dest="keep_open", action="store_true", help="Keep browser open after invocation (default: on)")
    ap.add_argument("--no-keep-open", dest="keep_open", action="store_false", help="Auto close browser when done")
    ap.add_argument("--html-file", default=None, help="If provided, loaded into variable `html_content`")
    ap.add_argument("--vars-json", default=None, help="Additional variables JSON injected into invocation namespace")
    # Visual highlight options
    ap.add_argument("--highlight-skill-primary", action="store_true", help="Highlight skill's primary selector before invocation")
    ap.add_argument("--highlight-color", default="rgba(255,0,0,0.9)", help="Highlight outline color (default: rgba(255,0,0,0.9))")
    ap.add_argument("--highlight-width-px", type=int, default=2, help="Highlight outline width in px (default: 2)")
    # Click flash options
    ap.add_argument("--flash-clicks", action="store_true", help="Temporarily fill clicked element for visibility")
    ap.add_argument("--flash-color", default="rgba(255,215,0,0.5)", help="Fill color for click flash (default: rgba(255,215,0,0.5))")
    ap.add_argument("--flash-duration-ms", type=int, default=1000, help="Duration of click flash in ms (default: 1000)")
    ap.add_argument("--flash-mode", default="background", choices=["background", "outline"], help="Flash mode: background or outline")
    ap.set_defaults(keep_open=True)
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    skill = _read_json(args.skill)
    locators = skill.get("locators") or {}
    code = (skill.get("program") or {}).get("code") or ""
    if not code.strip():
        print("[ERROR] program.code is empty")
        return 2

    ns = _compile_program(code)
    # Prepare evaluation locals
    extra_vars: Dict[str, Any] = {}
    if args.html_file:
        try:
            with open(args.html_file, "r", encoding="utf-8") as f:
                extra_vars["html_content"] = f.read()
        except Exception as e:
            print(f"[WARN] failed to load html_file: {e}")
    if args.vars_json:
        try:
            extra_vars.update(json.loads(args.vars_json))
        except Exception as e:
            print(f"[WARN] vars-json parse error: {e}")

    # Headed by default with slow_mo to make actions visible
    start_url = args.url or _derive_start_url(skill)
    # Extract optional cookie preconditions
    pre = skill.get("preconditions") or {}
    cookies_pre = None
    try:
        cookies_pre = (pre.get("cookies") or {}).get("set") if isinstance(pre.get("cookies"), dict) else None
    except Exception:
        cookies_pre = None

    with make_env(
        start_url,
        headless=False,
        slow_mo=max(0, int(args.slow_mo_ms or 0)),
        default_timeout_ms=max(1, int(args.default_timeout_ms or 1)),
        auto_close=not args.keep_open,
        cookies=cookies_pre,
    ) as env:
        # Provide env, raw page, and locators to invocation context
        locals_ns: Dict[str, Any] = {
            "env": env,
            "page": getattr(env, "_page", None),
            "locators": locators,
        }
        locals_ns.update(extra_vars)
        # Optional: highlight skill primary selector for better visibility
        try:
            if getattr(args, "highlight_skill_primary", False):
                primary = str((locators or {}).get("selector") or "")
                if primary:
                    env.highlight(primary, color=str(args.highlight_color or "rgba(255,0,0,0.9)"), width=int(args.highlight_width_px or 2))
        except Exception:
            pass
        # Optional: enable click flash
        try:
            if getattr(args, "flash_clicks", False):
                env.enable_click_flash(color=str(args.flash_color or "rgba(255,215,0,0.5)"), duration_ms=int(args.flash_duration_ms or 1000), mode=str(args.flash_mode or "background"))
        except Exception:
            pass
        call_str = args.invoke
        try:
            # Normalize possible \n escapes if user passed a single-line string
            call_eval = call_str
            # Execute the call (expression) in the program namespace
            result = eval(compile(call_eval, "<invoke>", "eval"), ns, locals_ns)
            print("[RESULT] invoke returned:", result)
        except Exception as e:
            print("[ERROR] invoke failed:", type(e).__name__, e)
            if args.keep_open:
                print("[HOLD] Browser is kept open. Press Enter to exit and close…")
                try:
                    input()
                except Exception:
                    pass
            return 1
        if args.keep_open:
            print("[HOLD] Invocation finished. Browser is kept open. Press Enter to exit and close…")
            try:
                input()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
