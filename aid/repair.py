"""
CLI entry to repair a single skill (non-LLM pipeline).

Usage:
  python -m aid.repair --skill <skill.json> --new-run-dir <dir> [--old-run-dir <dir>] [--out <file>] [--in-place]
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Optional
import time
import difflib

from .io import read_json, write_json, load_run_artifacts
from .repair_planner import plan_and_apply


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Repair a skill JSON using deterministic pipeline (no LLM)")
    ap.add_argument("--skill", required=True, help="Path to broken skill.json")
    ap.add_argument("--new-run-dir", required=True, help="New detect run dir")
    ap.add_argument("--old-run-dir", default=None, help="Old detect run dir (defaults to skill.meta.source_dir)")
    ap.add_argument("--out", default=None, help="Output repaired skill path (default <new_run_dir>/skill/Skill_(selector)_(id)_repaired.json)")
    ap.add_argument("--in-place", action="store_true", help="Overwrite input skill JSON in place")
    ap.add_argument("--log-dir", default=None, help="Directory to write repair logs (default: <new_run_dir>/skill/_repair_logs)")
    # LLM switches
    ap.add_argument("--use-llm-locators", action="store_true", help="Use LLM to refine locators")
    ap.add_argument("--use-llm-preconditions", action="store_true", help="Use LLM to refine preconditions")
    ap.add_argument("--use-llm-program", action="store_true", help="Use LLM to repair program.code")
    ap.add_argument("--use-llm-naming", action="store_true", help="Use LLM to suggest label/slug")
    ap.add_argument("--no-verbose", dest="verbose", action="store_false", help="Disable verbose logs (default: on)")
    return ap.parse_args(argv)


def _default_out_path(skill: Dict[str, Any], new_run_dir: str) -> str:
    sel = ((skill.get("locators") or {}).get("selector") or "selector").replace('/', '_')
    sid = str(skill.get("id") or "id")
    name = f"Skill_{sel}_{sid}_repaired.json"
    out_dir = os.path.join(new_run_dir, "skill")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, name)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    verbose = getattr(args, "verbose", True)
    if verbose:
        print("[aid.repair] load inputs …")
    skill = read_json(args.skill)
    old_dir = args.old_run_dir or ((skill.get("meta") or {}).get("source_dir") or "")
    if not old_dir:
        old_dir = args.new_run_dir  # fallback
    old_art = load_run_artifacts(old_dir)
    new_art = load_run_artifacts(args.new_run_dir)
    if verbose:
        print("[aid.repair] deterministic pipeline: plan_and_apply …")
    t_all_start = time.perf_counter()
    repaired = plan_and_apply(skill, old_art, new_art)
    # Optionally apply LLM-driven patches
    llm_logs: list[dict] = []
    if any([args.use_llm_locators, args.use_llm_preconditions, args.use_llm_program, args.use_llm_naming]):
        from .patch_ops import apply_patch
        from .llm_repair import llm_locators, llm_preconditions, llm_program_fix, llm_naming
        ops = []
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        if args.use_llm_locators:
            if verbose:
                print("[aid.repair] LLM locators …")
            _t0 = time.perf_counter()
            res = llm_locators(repaired, args.new_run_dir, new_art, verbose=verbose)
            _dt = time.perf_counter() - _t0
            ops += res.get("ops", [])
            usage = res.get("usage", {}) or {}
            total_tokens += int(usage.get("total_tokens") or 0)
            total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            total_completion_tokens += int(usage.get("completion_tokens") or 0)
            llm_logs.append({"step": "locators", "ops_count": len(res.get("ops") or []), "usage": usage, "duration_sec": round(_dt, 3)})
        if args.use_llm_preconditions:
            # 复用 deterministic diff 分析结果
            from .diff_analyzer import analyze
            diff = analyze(repaired, old_art, new_art)
            if verbose:
                print("[aid.repair] LLM preconditions …")
            _t0 = time.perf_counter()
            res = llm_preconditions(repaired, diff, verbose=verbose)
            _dt = time.perf_counter() - _t0
            ops += res.get("ops", [])
            usage = res.get("usage", {}) or {}
            total_tokens += int(usage.get("total_tokens") or 0)
            total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            total_completion_tokens += int(usage.get("completion_tokens") or 0)
            llm_logs.append({"step": "preconditions", "ops_count": len(res.get("ops") or []), "usage": usage, "duration_sec": round(_dt, 3)})
        if args.use_llm_program:
            if verbose:
                print("[aid.repair] LLM program_fix …")
            _t0 = time.perf_counter()
            res = llm_program_fix(repaired, args.new_run_dir, verbose=verbose)
            _dt = time.perf_counter() - _t0
            ops += res.get("ops", [])
            usage = res.get("usage", {}) or {}
            total_tokens += int(usage.get("total_tokens") or 0)
            total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            total_completion_tokens += int(usage.get("completion_tokens") or 0)
            llm_logs.append({"step": "program_fix", "ops_count": len(res.get("ops") or []), "usage": usage, "duration_sec": round(_dt, 3)})
        if args.use_llm_naming:
            if verbose:
                print("[aid.repair] LLM naming …")
            _t0 = time.perf_counter()
            res = llm_naming(repaired, args.new_run_dir, verbose=verbose)
            _dt = time.perf_counter() - _t0
            ops += res.get("ops", [])
            usage = res.get("usage", {}) or {}
            total_tokens += int(usage.get("total_tokens") or 0)
            total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            total_completion_tokens += int(usage.get("completion_tokens") or 0)
            llm_logs.append({"step": "naming", "ops_count": len(res.get("ops") or []), "usage": usage, "duration_sec": round(_dt, 3)})
        if ops:
            repaired = apply_patch(repaired, ops)
        # 打印简要指标（LLM Token 总数）
        if total_tokens:
            print(f"[METRIC] Token(total)={total_tokens}")
    out_path = args.out or (args.skill if args.in_place else _default_out_path(skill, args.new_run_dir))
    write_json(out_path, repaired)
    # Persist a structured repair log
    try:
        from datetime import datetime
        sid = str(repaired.get("id") or (skill.get("id") or "unknown"))
        sel = (repaired.get("locators") or {}).get("selector") or ((skill.get("locators") or {}).get("selector"))
        notes = (repaired.get("meta") or {}).get("repair_notes") or {}
        # runtime metrics
        total_sec = time.perf_counter() - t_all_start
        llm_step_secs = [x.get("duration_sec", 0.0) for x in llm_logs]
        avg_step_sec = round(sum(llm_step_secs) / len(llm_step_secs), 3) if llm_step_secs else 0.0
        # patch size metrics (code + structure)
        def _get_code(obj: Dict[str, Any]) -> str:
            p = (obj.get("program") or {}) if isinstance(obj, dict) else {}
            return (p.get("code") or obj.get("code") or "") if isinstance(p, dict) else (obj.get("code") or "")

        old_code = _get_code(skill)
        new_code = _get_code(repaired)
        uni = list(difflib.unified_diff(old_code.splitlines(), new_code.splitlines(), lineterm=""))
        added_lines = sum(1 for ln in uni if ln.startswith("+") and not ln.startswith("+++"))
        deleted_lines = sum(1 for ln in uni if ln.startswith("-") and not ln.startswith("---"))
        sm = difflib.SequenceMatcher(None, old_code, new_code)
        chars_added = 0
        chars_deleted = 0
        for tag, a0, a1, b0, b1 in sm.get_opcodes():
            if tag in ("insert", "replace"):
                chars_added += (b1 - b0)
            if tag in ("delete", "replace"):
                chars_deleted += (a1 - a0)
        old_pre = (skill.get("preconditions") or {}) if isinstance(skill, dict) else {}
        new_pre = (repaired.get("preconditions") or {}) if isinstance(repaired, dict) else {}
        pre_added_keys = sorted(list(set(new_pre.keys()) - set(old_pre.keys())))
        old_loc = (skill.get("locators") or {}) if isinstance(skill, dict) else {}
        new_loc = (repaired.get("locators") or {}) if isinstance(repaired, dict) else {}
        def _list(v):
            return [x for x in (v or []) if isinstance(x, str)]
        sa_added = len(set(_list(new_loc.get("selector_alt"))) - set(_list(old_loc.get("selector_alt"))))
        bt_added = len(set(_list(new_loc.get("by_text"))) - set(_list(old_loc.get("by_text"))))
        by_role_changed = int(bool(new_loc.get("by_role")) != bool(old_loc.get("by_role")) or (new_loc.get("by_role") != old_loc.get("by_role")))
        selector_changed = int((new_loc.get("selector") or "") != (old_loc.get("selector") or ""))
        # 复用率（reuse_ratio）计算：
        # - code：旧代码行中保持不变的比例（基于逐行对比）
        # - locators：旧定位器项（主 selector、by_role、selector_alt 列表项、by_text 列表项）在新技能中仍被保留的比例
        old_code_lines = (old_code.splitlines() if isinstance(old_code, str) else [])
        new_code_lines = (new_code.splitlines() if isinstance(new_code, str) else [])
        sm_lines = difflib.SequenceMatcher(None, old_code_lines, new_code_lines)
        equal_lines = 0
        for tag, a0, a1, b0, b1 in sm_lines.get_opcodes():
            if tag == "equal":
                equal_lines += (a1 - a0)
        reuse_ratio_code = (equal_lines / max(1, len(old_code_lines))) if old_code_lines else 0.0

        def _norm_list(v):
            return [x for x in (v or []) if isinstance(x, str)]

        base_cnt = 0
        kept_cnt = 0
        # 主 selector
        old_selector = (old_loc.get("selector") or "")
        if old_selector:
            base_cnt += 1
            if (new_loc.get("selector") or "") == old_selector:
                kept_cnt += 1
        # by_role
        old_role = old_loc.get("by_role") or {}
        if old_role:
            base_cnt += 1
            if (new_loc.get("by_role") or {}) == old_role:
                kept_cnt += 1
        # selector_alt（逐项计数交集）
        old_sa = set(_norm_list(old_loc.get("selector_alt")))
        new_sa = set(_norm_list(new_loc.get("selector_alt")))
        if old_sa:
            base_cnt += len(old_sa)
            kept_cnt += len(old_sa & new_sa)
        # by_text（逐项计数交集）
        old_bt = set(_norm_list(old_loc.get("by_text")))
        new_bt = set(_norm_list(new_loc.get("by_text")))
        if old_bt:
            base_cnt += len(old_bt)
            kept_cnt += len(old_bt & new_bt)
        reuse_ratio_locators = (kept_cnt / max(1, base_cnt)) if base_cnt else 0.0

        log = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "skill_id": sid,
            "selector": sel,
            "input_skill": args.skill,
            "out_path": out_path,
            "old_run_dir": old_dir,
            "new_run_dir": args.new_run_dir,
            "deterministic": notes,
            "llm": {
                "steps": llm_logs,
                "total_tokens": sum(int((x.get("usage") or {}).get("total_tokens") or 0) for x in llm_logs),
                "prompt_tokens": sum(int((x.get("usage") or {}).get("prompt_tokens") or 0) for x in llm_logs),
                "completion_tokens": sum(int((x.get("usage") or {}).get("completion_tokens") or 0) for x in llm_logs),
            },
            "metrics": {
                "runtime": {"total_sec": round(total_sec, 3), "avg_step_sec": avg_step_sec, "step_secs": llm_step_secs},
                "patch_size": {
                    "code": {
                        "lines_added": added_lines,
                        "lines_deleted": deleted_lines,
                        "lines_total": added_lines + deleted_lines,
                        "chars_added": chars_added,
                        "chars_deleted": chars_deleted,
                        "chars_total": chars_added + chars_deleted,
                    },
                    "structure_added": {
                        "preconditions_added_keys": pre_added_keys,
                        "locators": {
                            "selector_changed": selector_changed,
                            "by_role_changed": by_role_changed,
                            "selector_alt_added": sa_added,
                            "by_text_added": bt_added,
                        },
                    },
                },
                "reuse_ratio": {
                    "code": round(float(reuse_ratio_code), 4),
                    "locators": round(float(reuse_ratio_locators), 4),
                    # 附带基数细节，便于后续审计
                    "_detail": {
                        "code": {"old_lines": len(old_code_lines), "equal_lines": equal_lines},
                        "locators": {"base": base_cnt, "kept": kept_cnt},
                    },
                },
            },
        }
        log_dir = args.log_dir or os.path.join(args.new_run_dir, "skill", "_repair_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"repair_{sid}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json")
        write_json(log_path, log)
        if verbose:
            print(f"[aid.repair] log written: {log_path}")
    except Exception as le:
        if verbose:
            print(f"[aid.repair] write log failed: {type(le).__name__}: {le}")
    print(out_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
