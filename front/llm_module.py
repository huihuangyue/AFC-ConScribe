from __future__ import annotations

"""
llm_module

前端后端桥接模块（接口层）：

给定：
  - Detect 产物目录 run_dir（包含 skill/Skill_*.json 等）；
  - 自然语言任务 task；

调用现有 planner 管线，输出：
  - 选中的技能 id 与技能 JSON 路径；
  - （可选）填参后的 args；
  - 对应的 Python 调用代码字符串（用于在前端展示“将要执行什么”）。

注意：本模块本身**不执行浏览器动作**，只负责“选技能 + 产出调用代码”。
真正的执行仍由 planner.run_task / browser.invoke 等模块负责。
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from planner.env_summary import build_page_summary
from planner.skill_index import build_skill_index
from planner.planner import build_plan
from planner.arg_fill import fill_args


def _read_json(path: str) -> Dict[str, Any]:
  with open(path, "r", encoding="utf-8") as f:
      return json.load(f) or {}


def _detect_main_func_name(code: str) -> Optional[str]:
    """从 program.code 中检测主函数名。

    逻辑与 planner.run_task._detect_main_func_name 一致：
      - 只匹配顶层函数定义；
      - 跳过以下划线开头的函数；
      - 优先选择非 set_/select_ 前缀的函数名。
    """
    import re as _re

    pat = _re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", _re.M)
    candidates: List[str] = []
    for m in pat.finditer(code or ""):
        name = m.group(1)
        if name.startswith("_"):
            continue
        if name.startswith(("set_", "select_")):
            candidates.append(name)
            continue
        return name
    if candidates:
        return candidates[0]
    return None


def _build_call_str(func_name: str, args: Dict[str, Any]) -> str:
    """根据函数名与参数字典生成 Python 调用代码（全部使用关键字参数）。

    与 planner.run_task._build_call_str 保持一致，默认始终传入 page=page。
    """
    lines: List[str] = []
    lines.append(f"{func_name}(")
    lines.append("    page=page,")
    for k, v in args.items():
        # page 已经单独传入
        if k == "page":
            continue
        lines.append(f"    {k}={repr(v)},")
    if len(lines) >= 2:
        lines[-1] = lines[-1].rstrip(",")
    lines.append(")")
    return "\n".join(lines)


def _find_skill_card(skills_index: Dict[str, Any], skill_id: str) -> Optional[Dict[str, Any]]:
    """在 skills_index 中查找指定 id 的技能卡片。"""
    for s in skills_index.get("skills") or []:
        if isinstance(s, dict) and str(s.get("id") or "") == str(skill_id):
            return s
    return None


def plan_task(
    run_dir: str,
    task: str,
    *,
    top_k: int = 5,
    use_llm_plan: bool = True,
    use_llm_args: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """对单个自然语言任务做“选技能 + 生成调用代码”的规划。

    返回结构示例：
    {
      "ok": true,
      "run_dir": "...",
      "task": "...",
      "skill_id": "d321",
      "skill_path": "/abs/path/to/Skill_xxx.json",
      "args": { ... },          # 可能为空
      "call_str": "search_hotel(\\n  page=page,...\\n)",
      "plan": { ... 原始 plan 字典 ... },
      "warnings": ["arg_fill_failed: ..."],
    }
    """
    run_dir = os.path.abspath(run_dir)
    if verbose:
        print(f"[llm_module] run_dir={run_dir}")
        print(f"[llm_module] task={task}")

    if not os.path.isdir(run_dir):
        return {
            "ok": False,
            "error": f"run_dir_not_found:{run_dir}",
            "run_dir": run_dir,
            "task": task,
        }

    # 1) 确保 page_summary.json / skills_index.json 存在（无则构建）
    try:
        build_page_summary(run_dir, verbose=verbose)
    except Exception as e:  # pragma: no cover - 建摘要失败直接返回错误
        return {
            "ok": False,
            "error": f"build_page_summary_failed:{type(e).__name__}:{e}",
            "run_dir": run_dir,
            "task": task,
        }

    skills_root = os.path.join(run_dir, "skill")
    try:
        skills_index = build_skill_index(skills_root, verbose=verbose)
    except Exception as e:
        return {
            "ok": False,
            "error": f"build_skill_index_failed:{type(e).__name__}:{e}",
            "run_dir": run_dir,
            "task": task,
        }

    # 2) 调用 planner.build_plan 选择技能
    try:
        plan_obj = build_plan(
            task=task,
            run_dir=run_dir,
            top_k=top_k,
            current_url=None,
            skills_index_path=None,  # 让 planner 自行从 run_dir/skill 读取
            use_llm=use_llm_plan,
            verbose=verbose,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"build_plan_failed:{type(e).__name__}:{e}",
            "run_dir": run_dir,
            "task": task,
        }

    if not plan_obj.steps:
        return {
            "ok": False,
            "error": "no_plan_steps",
            "run_dir": run_dir,
            "task": task,
            "plan": plan_obj.to_dict(),
        }

    # 选取第一步作为本次展示/执行的主技能
    step = plan_obj.steps[0]
    skill_id = step.skill_id

    card = _find_skill_card(skills_index, skill_id)
    if not card:
        return {
            "ok": False,
            "error": f"skill_not_found_in_index:{skill_id}",
            "run_dir": run_dir,
            "task": task,
            "plan": plan_obj.to_dict(),
        }

    skill_path = card.get("skill_path")
    if not isinstance(skill_path, str) or not os.path.exists(skill_path):
        return {
            "ok": False,
            "error": f"skill_path_invalid:{skill_path}",
            "run_dir": run_dir,
            "task": task,
            "plan": plan_obj.to_dict(),
            "skill_id": skill_id,
        }

    skill = _read_json(skill_path)

    warnings: List[str] = []

    # 3) （可选）调用 planner.arg_fill 填参
    args: Dict[str, Any] = {}
    if use_llm_args:
        try:
            args, w = fill_args(task, skill, verbose=verbose)
            warnings.extend(w or [])
        except Exception as e:
            warnings.append(f"arg_fill_failed:{type(e).__name__}:{e}")
    else:
        # 不启用 LLM 填参时，保留空 args（前端可只展示“将要调用的函数名”）
        args = {}

    # 4) 构建调用代码字符串
    prog = skill.get("program") or {}
    code = ""
    if isinstance(prog, dict):
        code = prog.get("code") or ""
    func_name = None
    if isinstance(prog, dict):
        func_name = prog.get("main_func") or prog.get("main") or None
    if not func_name:
        # 若 program.code 存在则尝试从中推断主函数名
        if isinstance(code, str) and code:
            func_name = _detect_main_func_name(code)
        if not func_name:
            func_name = prog.get("entry") or f"program__{skill_id}__auto"

    call_str = _build_call_str(func_name, args)

    return {
        "ok": True,
        "run_dir": run_dir,
        "task": task,
        "skill_id": skill_id,
        "skill_path": os.path.abspath(skill_path),
        "plan": plan_obj.to_dict(),
        "args": args,
        "call_str": call_str,
        "warnings": warnings,
    }


if __name__ == "__main__":  # pragma: no cover
    # 简单 CLI 调试：
    import argparse as _argparse

    p = _argparse.ArgumentParser(description="调试：从命令行测试 plan_task 接口")
    p.add_argument("--run-dir", required=True, help="Detect run_dir 目录")
    p.add_argument("--task", required=True, help="自然语言任务描述")
    p.add_argument("--no-llm-plan", dest="use_llm_plan", action="store_false")
    p.add_argument("--no-llm-args", dest="use_llm_args", action="store_false")
    p.set_defaults(use_llm_plan=True, use_llm_args=True)
    args = p.parse_args()
    result = plan_task(
        run_dir=args.run_dir,
        task=args.task,
        use_llm_plan=getattr(args, "use_llm_plan", True),
        use_llm_args=getattr(args, "use_llm_args", True),
        verbose=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
