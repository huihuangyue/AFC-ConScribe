"""
AFCdatabaseRepair.cli

命令行入口：串联“用 AFC 库修复单个技能 + 生成 exec_log（可选触发进化）”的最小闭环。

典型用法（示例）::

    PYTHONPATH=. python -m AFCdatabaseRepair.cli \
      --global-db workspace/AFCdatabase/db/abstract_skills_global.jsonl \
      --run-dir-old workspace/data/ctrip_com/20251120004106 \
      --run-dir-new workspace/data/ctrip_com/20251216193438 \
      --skill-id d316 \
      --invoke "perform_hotel_search(page, destination='北京', check_in_date='1月1日', check_out_date='1月2日')" \
      --out-exec-log workspace/进化/exec_log_ctrip_20251216193438.json \
      --auto-evolve

流程概览：
  1. 从 run_dir_old + skill_id 找到对应的 abstract_skill_id；
  2. 在全局 AFC 库中读取该抽象技能，并选择一个代表性 SkillCase 作为 CBR 源案例；
  3. 读取 run_dir_new 的 AfcPageSnapshot，在新页面上用 cbr_matcher 找到候选控件；
  4. 对每个候选控件调用 code_adapter.propose_repaired_skill 生成修复建议；
  5. 通过 exec_runner.run_repair_proposal + browser.invoke 在真实浏览器中执行候选技能；
  6. 使用 exec_log_builder.build_exec_log 将试验结果整理为统一 exec_log JSON；
  7. 如指定 --auto-evolve，则调用 AFCdatabaseEvolve.integrate_run_with_evolution 吸收这批结果，
     驱动全局 AFC 库进化。

注意：
  - 本 CLI 默认不“聪明猜测”调用方式，必须显式提供 --invoke 调用字符串，
    语义与 browser.invoke 的 --invoke 完全一致；
  - 当前实现仅使用 CBR 规则相似度进行候选控件排序，未启用 repair_match 的 LLM 重排序，
    后续如果需要可以在此文件中增加一个可选的 --use-llm-match 开关。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import cbr_matcher
from .code_adapter import RepairProposal, propose_repaired_skill
from .exec_log_builder import build_exec_log, write_exec_log
from .exec_runner import ExecResult, run_repair_proposal
from .loader import (
    GlobalDb,
    JsonDict,
    find_abstract_skill_for_skill_id,
    get_abstract_entry_for_skill_id,
    load_global_db,
    load_page_snapshot,
)
from AFCdatabaseEvolve.integrate_run import integrate_run_with_evolution  # type: ignore[import]
from AFCdatabaseEvolve.loader import load_exec_log as load_exec_log_evolve  # type: ignore[import]


def _ensure_path(p: str | Path) -> Path:
    return p if isinstance(p, Path) else Path(p)


def _find_skill_json(run_dir: Path, skill_id: str) -> Path:
    """在 run_dir/skill 下通过 id 查找技能 JSON 文件."""
    skill_dir = run_dir / "skill"
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"skill directory not found: {skill_dir}")

    # 先尝试直接按约定文件名匹配（加快常见路径）
    candidates: List[Path] = []
    for p in skill_dir.rglob("*.json"):
        try:
            with p.open("r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        sid = obj.get("id")
        if isinstance(sid, str) and sid == skill_id:
            candidates.append(p)

    if not candidates:
        raise FileNotFoundError(f"no skill JSON with id={skill_id!r} found under {skill_dir}")
    if len(candidates) > 1:
        # 理论上单 run_dir 内 id 应唯一，这里若出现多条则取最短路径并给出提示
        candidates.sort(key=lambda p: (len(str(p)), str(p)))
    return candidates[0]


def _select_reference_skill_case(
    global_db: GlobalDb,
    abstract_skill_id: str,
    run_dir_old: Path,
) -> JsonDict:
    """从全局库中为某个抽象技能挑选一个代表性 SkillCase."""
    entry = global_db.index.get(abstract_skill_id)
    if not entry:
        raise KeyError(f"abstract_skill_id={abstract_skill_id!r} not found in global AFC db")

    cases = entry.get("skill_cases") or []
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"no skill_cases for abstract_skill_id={abstract_skill_id!r} in global AFC db")

    rd_old = str(run_dir_old.resolve())

    def _score(case: Dict[str, Any]) -> tuple[int, int, int]:
        score_primary = 0
        if str(case.get("run_dir") or "") == rd_old:
            score_primary += 10
        rh = case.get("R_history") or {}
        succ = int(rh.get("exec_success") or 0)
        fail = int(rh.get("exec_fail") or 0)
        total = succ + fail
        # 优先成功次数多、总试验多的样本
        return score_primary, succ, total

    best = max(cases, key=_score)
    if not isinstance(best, dict):
        raise ValueError("selected SkillCase is not a dict")
    return best


def _build_trials_for_candidates(
    abstract_skill_id: str,
    run_dir_new: Path,
    old_skill_path: Path,
    ref_case: JsonDict,
    candidates: Sequence[JsonDict],
    *,
    invoke_str: str,
    start_url: Optional[str],
    slow_mo_ms: int,
    default_timeout_ms: int,
    keep_open: bool,
    use_llm_code: bool,
) -> List[Dict[str, Any]]:
    """对一组候选控件依次生成修复方案并执行，返回 trial 列表."""
    trials: List[Dict[str, Any]] = []
    for idx, cand in enumerate(candidates, start=1):
        control_id = cand.get("control_id")
        if not isinstance(control_id, str) or not control_id:
            continue
        control = cand.get("control") or {}
        if not isinstance(control, dict):
            continue

        proposal: RepairProposal = propose_repaired_skill(
            old_skill_path=old_skill_path,
            candidate_control=control,
            skill_case=ref_case,
            run_dir_new=None,  # 当前实现直接在原 skill JSON 上迭代，避免路径爆炸
            use_llm=use_llm_code,
        )

        exec_res: ExecResult = run_repair_proposal(
            proposal,
            invoke_str,
            start_url=start_url,
            slow_mo_ms=slow_mo_ms,
            default_timeout_ms=default_timeout_ms,
            keep_open=keep_open,
            write_skill=True,
        )

        trial: Dict[str, Any] = {
            "afc_control_id": control_id,
            "skill_id": proposal.skill_id,
            "exec_result": exec_res,
            # CBR 层提供的基础相似度，先直接作为 sim_S 使用
            "sim_S": float(cand.get("score") or 0.0),
            "reuse_A": None,
            "notes": proposal.notes,
            "precomputed_metrics": {
                "sim_S": float(cand.get("score") or 0.0),
            },
        }
        trials.append(trial)
    return trials


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Use global AFC db + new run_dir to repair a broken skill and emit exec_log (optionally evolve db)."
    )
    ap.add_argument("--global-db", required=True, help="Path to abstract_skills_global.jsonl")
    ap.add_argument("--run-dir-old", required=True, help="Run dir where the original skill lives")
    ap.add_argument("--run-dir-new", required=True, help="Run dir for the new page to adapt to")
    ap.add_argument("--skill-id", required=True, help="Skill id (e.g. d316)")
    ap.add_argument(
        "--invoke",
        required=True,
        help="Python call string passed to browser.invoke --invoke, e.g. \"perform_hotel_search(page, ...)\"",
    )
    ap.add_argument(
        "--out-exec-log",
        required=True,
        help="Path to write exec_log JSON (see workspace/进化/exec_log.md)",
    )
    ap.add_argument("--task", default=None, help="Optional natural language task description, stored in exec_log.task")
    ap.add_argument("--start-url", default=None, help="Override start URL for browser.invoke (optional)")
    ap.add_argument("--top-k", type=int, default=3, help="Max number of candidate controls to try (default: 3)")
    ap.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum CBR similarity score to consider a candidate (default: 0.0)",
    )
    ap.add_argument(
        "--slow-mo-ms",
        type=int,
        default=150,
        help="Slow motion delay in ms for browser.invoke (default: 150)",
    )
    ap.add_argument(
        "--default-timeout-ms",
        type=int,
        default=12000,
        help="Default timeout in ms for browser.invoke (default: 12000)",
    )
    ap.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep browser open after each invocation (default: False)",
    )
    ap.add_argument(
        "--no-llm-code",
        action="store_true",
        help="Disable LLM-based code adaptation (only update locators from candidate_control)",
    )
    ap.add_argument(
        "--auto-evolve",
        action="store_true",
        help="After writing exec_log, call AFCdatabaseEvolve.integrate_run_with_evolution to update global db",
    )
    return ap.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    global_db_path = _ensure_path(args.global_db).resolve()
    run_dir_old = _ensure_path(args.run_dir_old).resolve()
    run_dir_new = _ensure_path(args.run_dir_new).resolve()
    out_exec_log_path = _ensure_path(args.out_exec_log).resolve()
    skill_id = str(args.skill_id)

    # 1) 加载全局 AFC 库
    global_db = load_global_db(global_db_path)

    # 2) 在旧 run_dir 中定位 abstract_skill_id
    abstract_skill_id = find_abstract_skill_for_skill_id(run_dir_old, skill_id)
    if not abstract_skill_id:
        raise SystemExit(
            f"[AFCdatabaseRepair.cli] skill_id={skill_id!r} not found in abstract_skill index of {run_dir_old}"
        )

    # 3) 选择参考 SkillCase
    ref_case = _select_reference_skill_case(global_db, abstract_skill_id, run_dir_old)

    # 4) 加载新页面的 AfcPageSnapshot 并用 CBR 检索候选控件
    page_snapshot_new = load_page_snapshot(run_dir_new)
    candidates = cbr_matcher.find_candidate_controls(
        ref_case,
        page_snapshot_new,
        top_k=int(args.top_k or 0) or 1,
        min_score=float(args.min_score or 0.0),
    )
    if not candidates:
        # 仍写出一个空 skill_cases 的 exec_log，便于后续分析
        exec_log_empty = build_exec_log(run_dir_new, abstract_skill_id, trials=[], task=args.task)
        write_exec_log(out_exec_log_path, exec_log_empty)
        print(
            f"[AFCdatabaseRepair.cli] no candidate controls above min_score={args.min_score} for "
            f"abstract_skill_id={abstract_skill_id!r}; wrote empty exec_log to {out_exec_log_path}"
        )
        return 1

    # 5) 找到旧技能 JSON 路径
    old_skill_json = _find_skill_json(run_dir_old, skill_id)

    # 6) 针对每个候选控件生成修复方案并执行
    trials = _build_trials_for_candidates(
        abstract_skill_id=abstract_skill_id,
        run_dir_new=run_dir_new,
        old_skill_path=old_skill_json,
        ref_case=ref_case,
        candidates=candidates,
        invoke_str=str(args.invoke),
        start_url=args.start_url,
        slow_mo_ms=int(args.slow_mo_ms or 0),
        default_timeout_ms=int(args.default_timeout_ms or 1),
        keep_open=bool(args.keep_open),
        use_llm_code=not bool(args.no_llm_code),
    )

    # 7) 构建并写出 exec_log
    exec_log = build_exec_log(run_dir_new, abstract_skill_id, trials, task=args.task)
    write_exec_log(out_exec_log_path, exec_log)
    print(f"[AFCdatabaseRepair.cli] wrote exec_log with {len(exec_log.get('skill_cases') or [])} cases to {out_exec_log_path}")

    # 8) 如需自动进化，则调用 integrate_run_with_evolution
    if args.auto_evolve:
        exec_log_loaded = load_exec_log_evolve(out_exec_log_path)
        integrate_run_with_evolution(
            global_db_path=global_db_path,
            run_dir=run_dir_new,
            exec_log=exec_log_loaded,
            use_llm_rating=True,
        )
        print(f"[AFCdatabaseRepair.cli] auto-evolve done for run_dir={run_dir_new}")

    # 返回值：若至少有一次执行成功则视为整体成功
    any_success = any(bool((t.get("exec_result") or {}).ok) for t in trials if isinstance(t.get("exec_result"), ExecResult))
    if not any_success:
        # 如果 ExecResult 被转成 dict，兜底再检查一遍
        for t in trials:
            er = t.get("exec_result")
            if isinstance(er, dict) and er.get("ok"):
                any_success = True
                break

    return 0 if any_success else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

