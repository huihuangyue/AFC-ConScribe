from __future__ import annotations

"""
planner.run_task

端到端接口（第二部分）：
- 输入：一个 detect run_dir（包含预处理产物）与自然语言任务 task；
- 输出：在真实浏览器上的可见操作（非无头），并在命令行打印：
  - 选中的技能 id 与路径；
  - LLM 填充后的参数；
  - 对应的 Python 调用代码；
  - 等价的 `python -m browser.invoke ...` 调用形式。

预期使用方式（示例）：

  python -m planner.run_task \\
    --run-dir "workspace/data/ctrip_com/20251116234238" \\
    --task "在携程首页搜索上海 2025年11月13日入住 11月15日退房 1间房 2位成人 0儿童 五星（钻），关键词外滩"
"""

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

from .env_summary import build_page_summary
from .skill_index import build_skill_index
from .planner import build_plan
from .arg_fill import fill_args
from .candidate_selector import CandidateSkill
from .llm_client import drain_usage_stats


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def _ensure_page_summary(run_dir: str, verbose: bool = True) -> Dict[str, Any]:
    p = os.path.join(run_dir, "page_summary.json")
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"page_summary.json not found under {run_dir}. "
            f"请先在预处理阶段运行: python -m planner.env_summary --run-dir \"{run_dir}\""
        )
    return _read_json(p)


def _ensure_skills_index(run_dir: str, verbose: bool = True) -> Dict[str, Any]:
    skills_root = os.path.join(run_dir, "skill")
    idx_path = os.path.join(skills_root, "skills_index.json")
    if not os.path.exists(idx_path):
        raise FileNotFoundError(
            f"skills_index.json not found under {skills_root}. "
            f"请先在预处理阶段运行: python -m planner.skill_index --skills-root \"{skills_root}\""
        )
    return _read_json(idx_path)


def _find_skill_card(skills_index: Dict[str, Any], skill_id: str) -> Optional[Dict[str, Any]]:
    for s in skills_index.get("skills") or []:
        if isinstance(s, dict) and str(s.get("id") or "") == str(skill_id):
            return s
    return None


def _detect_main_func_name(code: str) -> Optional[str]:
    """从 program.code 中检测主函数名（复制自 utils.skill_export 逻辑）。"""
    import re as _re

    # 只匹配顶层函数（行首无缩进），等价于 utils.skill_export._detect_main_func_name
    pat = _re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", _re.M)
    candidates: list[str] = []
    for m in pat.finditer(code or ""):
        name = m.group(1)
        if name.startswith("_"):
            continue
        # 跳过明显的辅助函数前缀（set_/select_），优先选择真正的“动作”函数
        if name.startswith(("set_", "select_")):
            candidates.append(name)
            continue
        # 第一个非辅助函数作为首选
        return name
    # 如果只找到辅助函数，就退而求其次取第一个
    if candidates:
        return candidates[0]
    return None


def _build_call_str(func_name: str, args: Dict[str, Any]) -> str:
    """根据函数名与参数字典生成 Python 调用代码（全部使用关键字参数）。"""
    lines: List[str] = []
    lines.append(f"{func_name}(")
    lines.append("    page=page,")
    for k, v in args.items():
        # page 已经单独传入
        if k == "page":
            continue
        lines.append(f"    {k}={repr(v)},")
    # 替最后一个逗号为无逗号，保持美观
    if len(lines) >= 2:
        lines[-1] = lines[-1].rstrip(",")
    lines.append(")")
    return "\n".join(lines)


def run_task(
    run_dir: str,
    task: str,
    *,
    top_k: int = 5,
    use_llm_plan: bool = True,
    verbose: bool = True,
    slow_mo_ms: int = 150,
    default_timeout_ms: int = 12000,
) -> int:
    """从 run_dir + 自然语言任务出发，选技能 + 填参 + 执行一次网页操作。"""
    t_global_0 = time.perf_counter()
    if verbose:
        print(f"[run_task] run_dir={run_dir}")
        print(f"[run_task] task={task}")

    t0 = time.perf_counter()
    page_summary = _ensure_page_summary(run_dir, verbose=verbose)
    t1 = time.perf_counter()
    skills_index = _ensure_skills_index(run_dir, verbose=verbose)
    t2 = time.perf_counter()

    # 1) 选技能：先用 planner.build_plan
    t_plan0 = time.perf_counter()
    plan = build_plan(
        task=task,
        run_dir=run_dir,
        top_k=top_k,
        current_url=None,
        skills_index_path=None,
        use_llm=use_llm_plan,
        verbose=verbose,
    )
    t_plan1 = time.perf_counter()
    if not plan.steps:
        print("[run_task] no steps produced by planner; abort.")
        return 1
    step = plan.steps[0]
    skill_id = step.skill_id
    if verbose:
        print(f"[run_task] chosen skill: {skill_id} (reason: {step.reason})")

    # 2) 找到技能 JSON 路径
    card = _find_skill_card(skills_index, skill_id)
    if not card:
        print(f"[run_task] skill {skill_id} not found in skills_index; abort.")
        return 1
    skill_path = card.get("skill_path")
    if not isinstance(skill_path, str) or not os.path.exists(skill_path):
        print(f"[run_task] skill_path invalid or missing for {skill_id}: {skill_path}")
        return 1
    if verbose:
        print(f"[run_task] skill JSON: {skill_path}")

    skill = _read_json(skill_path)

    # 3) LLM 填参（若该技能有 args_schema）
    args: Dict[str, Any] = {}
    warnings: List[str] = []
    if isinstance(skill.get("args_schema"), dict) and (skill["args_schema"].get("properties") or {}):
        t_fill0 = time.perf_counter()
        args, warnings = fill_args(task, skill, verbose=verbose)
        t_fill1 = time.perf_counter()
    else:
        # 无参数可填时，仍记录一个几乎为 0 的耗时区间，方便统计
        t_fill0 = t_fill1 = time.perf_counter()
        args, warnings = {}, []
    if verbose:
        print("[run_task] args:", args)
        print("[run_task] arg_fill warnings:", warnings)

    # 4) 根据 program.code 检测主函数名，并构造调用代码
    prog = skill.get("program") or {}
    code = (prog.get("code") or "") if isinstance(prog, dict) else ""
    if not code:
        print("[run_task] skill has no program.code; abort.")
        return 1
    # 优先使用显式指定的主函数名（program.main_func），否则根据代码结构推断
    func_name = None
    if isinstance(prog, dict):
        func_name = prog.get("main_func") or prog.get("main") or None
    if not func_name:
        func_name = _detect_main_func_name(code) or (prog.get("entry") or f"program__{skill_id}__auto")
    call_str = _build_call_str(func_name, args)

    print("=== Skill 调用代码 ===")
    print(call_str)

    # 5) 调用 browser.invoke 在真实浏览器中执行
    try:
        from browser.invoke import main as invoke_main  # type: ignore
    except Exception as e:
        print("[run_task] 导入 browser.invoke 失败:", type(e).__name__, e)
        return 2

    # 构造等价的 CLI（仅用于展示）
    cli_argv: List[str] = [
        "--skill",
        skill_path,
        "--invoke",
        call_str,
        "--slow-mo-ms",
        str(int(slow_mo_ms)),
        "--default-timeout-ms",
        str(int(default_timeout_ms)),
        "--keep-open",
    ]
    print("[run_task] 将执行命令：")
    print("  python -m browser.invoke \\")
    for i in range(0, len(cli_argv), 2):
        k = cli_argv[i]
        v = cli_argv[i + 1] if i + 1 < len(cli_argv) else ""
        print(f"    {k} {repr(v)} \\")

    # 真正执行（有头浏览器，动作可见）
    t_inv0 = time.perf_counter()
    rc = int(invoke_main(cli_argv))
    t_inv1 = time.perf_counter()

    # 汇总时间统计
    t_global_1 = time.perf_counter()
    if verbose:
        usage_stats = drain_usage_stats()
        total_prompt = sum((u.get("prompt_tokens") or 0) for u in usage_stats if isinstance(u, dict))
        total_completion = sum((u.get("completion_tokens") or 0) for u in usage_stats if isinstance(u, dict))
        total_tokens = sum((u.get("total_tokens") or (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)) for u in usage_stats if isinstance(u, dict))
        summary = {
            "page_summary_ms": (t1 - t0) * 1000.0,
            "skills_index_ms": (t2 - t1) * 1000.0,
            "plan_ms": (t_plan1 - t_plan0) * 1000.0,
            "arg_fill_ms": (t_fill1 - t_fill0) * 1000.0,
            "invoke_ms": (t_inv1 - t_inv0) * 1000.0,
            "total_ms": (t_global_1 - t_global_0) * 1000.0,
            "llm_calls": len(usage_stats),
            "llm_prompt_tokens": total_prompt,
            "llm_completion_tokens": total_completion,
            "llm_total_tokens": total_tokens,
        }
        timing = {k: round(v, 1) for k, v in summary.items() if k.endswith("_ms")}
        print("[run_task] timing_ms:", timing)
        print(
            "[run_task] llm_tokens:",
            {
                "calls": summary["llm_calls"],
                "prompt": summary["llm_prompt_tokens"],
                "completion": summary["llm_completion_tokens"],
                "total": summary["llm_total_tokens"],
            },
        )
    return rc


def _cli() -> int:
    ap = argparse.ArgumentParser(
        description="Run a natural language task in browser via pre-built skills (online phase only)"
    )
    ap.add_argument(
        "--run-dir",
        required=True,
        help="技能库根目录（需已包含 page_summary.json 与 skill/skills_index.json）",
    )
    ap.add_argument("--task", required=True, help="Natural language task description")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument(
        "--no-llm-plan",
        dest="use_llm_plan",
        action="store_false",
        help="Disable LLM in planner (default: on)",
    )
    ap.add_argument("--slow-mo-ms", type=int, default=150)
    ap.add_argument("--default-timeout-ms", type=int, default=12000)
    ap.add_argument("--no-verbose", dest="verbose", action="store_false")
    ap.set_defaults(use_llm_plan=True)
    args = ap.parse_args()
    return run_task(
        run_dir=args.run_dir,
        task=args.task,
        top_k=args.top_k,
        use_llm_plan=getattr(args, "use_llm_plan", True),
        verbose=getattr(args, "verbose", True),
        slow_mo_ms=args.slow_mo_ms,
        default_timeout_ms=args.default_timeout_ms,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
