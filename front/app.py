from __future__ import annotations

"""
Flask + 代码执行服务（基于现有 planner / llm_module / browser.invoke）。

用途：
  - 给定 run_dir（Detect 产物目录）与自然语言任务 task；
  - 调用 front.llm_module.plan_task 选出技能 + 生成调用代码；
  - 再调用 browser.invoke.main 实际在有头浏览器中执行该技能；
  - 通过 HTTP 返回规划结果与执行状态。

注意：
  - 这是一个“执行层”服务，只做一次性的“计划 + 执行”，不负责前端页面；
  - 右侧聊天 UI 仍由 front/run.py 提供，这里可以被视为后端执行 API 的雏形。
"""

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from flask import Flask, jsonify, request

# 为了能够直接 import front.llm_module / browser.invoke，确保仓库根目录在 sys.path 中
import sys

ROOT = Path(__file__).resolve().parents[1]  # /mnt/.../AFC-ConScribe
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from front.llm_module import plan_task  # type: ignore  # noqa: E402
from browser.invoke import main as browser_invoke_main  # type: ignore  # noqa: E402


app = Flask(__name__)


def _resolve_run_dir(p: str) -> str:
    """将 run_dir 解析为绝对路径（相对于仓库根目录）。"""
    if os.path.isabs(p):
        return p
    return os.path.abspath(ROOT / p)


DEFAULT_RUN_DIR = _resolve_run_dir(
    os.getenv("AFC_DEFAULT_RUN_DIR", "workspace/data/ctrip_com/20251116234238")
)


def execute_task_impl(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """核心执行逻辑：计划 + 执行一个任务，返回结果字典与 HTTP 状态码。

    请求 JSON（两种模式，二选一）：

    1) 规划 + 执行（推荐，少传参数）：
       {
         "run_dir": "workspace/data/xxx/... 或绝对路径（可选，默认使用环境变量或启动参数）",
         "task": "自然语言任务",
         "slow_mo_ms": 150,            # 可选：浏览器慢动作，默认 150
         "default_timeout_ms": 12000,  # 可选：默认超时
         "keep_open": true             # 可选：执行后是否保持浏览器打开（默认 true）
       }

    2) 直接执行已有计划（前端已拿到 skill_path + call_str）：
       {
         "skill_path": "/abs/path/to/Skill_*.json",
         "call_str": "search_hotel(page=page, ...)",
         "slow_mo_ms": 150,
         "default_timeout_ms": 12000,
         "keep_open": true
       }

    返回 JSON（成功时）：
    {
      "ok": true,
      "run_dir": "...",
      "task": "...",
      "skill_id": "...",
      "skill_path": "...",
      "call_str": "search_hotel(...",
      "warnings": [...],
      "invoke_exit_code": 0,
      "invoke_args": ["--skill", "...", "--invoke", "...", ...]
    }
    """
    run_dir_raw = str(payload.get("run_dir") or DEFAULT_RUN_DIR)
    task = str(payload.get("task") or "").strip()
    # 可选：前端已知的 skill_path + call_str（跳过重新规划）
    skill_path_in = str(payload.get("skill_path") or "").strip()
    call_str_in = str(payload.get("call_str") or "").strip()
    slow_mo_ms = int(payload.get("slow_mo_ms") or 150)
    default_timeout_ms = int(payload.get("default_timeout_ms") or 12000)
    keep_open = bool(payload.get("keep_open") if "keep_open" in payload else True)

    run_dir = _resolve_run_dir(run_dir_raw)
    plan_result: Dict[str, Any] = {}

    # ---------------- 模式 1：直接执行已有计划（skill_path + call_str 已给出） ----------------
    if skill_path_in and call_str_in:
        skill_path = skill_path_in
        call_str = call_str_in
        skill_id = Path(skill_path).stem  # 简单从文件名推断一个 id
        warnings: list[str] = []
    else:
        # ---------------- 模式 2：先规划，再执行 ----------------
        if not task:
            return {"ok": False, "error": "empty_task"}, 400

        plan_result = plan_task(
            run_dir=run_dir,
            task=task,
            top_k=5,
            use_llm_plan=True,
            use_llm_args=True,
            verbose=True,
        )
        if not plan_result.get("ok"):
            return {
                "ok": False,
                "error": plan_result.get("error") or "plan_failed",
                "plan_result": plan_result,
            }, 500

        skill_path = str(plan_result.get("skill_path") or "")
        call_str = str(plan_result.get("call_str") or "")
        skill_id = str(plan_result.get("skill_id") or "")
        warnings = plan_result.get("warnings") or []

    if not skill_path or not os.path.exists(skill_path):
        return {
            "ok": False,
            "error": f"skill_path_not_found:{skill_path}",
            "plan_result": plan_result if not (skill_path_in and call_str_in) else None,
        }, 500
    if not call_str.strip():
        return {
            "ok": False,
            "error": "empty_call_str",
            "plan_result": plan_result if not (skill_path_in and call_str_in) else None,
        }, 500

    # 2) 调用 browser.invoke.main 实际执行技能
    argv = [
        "--skill",
        skill_path,
        "--invoke",
        call_str,
        "--slow-mo-ms",
        str(int(slow_mo_ms)),
        "--default-timeout-ms",
        str(int(default_timeout_ms)),
    ]
    if keep_open:
        argv.append("--keep-open")
    else:
        argv.append("--no-keep-open")

    print("[app.execute_task] invoke browser.invoke with argv:", argv)
    exit_code = int(browser_invoke_main(argv))

    return {
        "ok": exit_code == 0,
        "run_dir": run_dir,
        "task": task,
        "skill_id": skill_id,
        "skill_path": skill_path,
        "call_str": call_str,
        "warnings": warnings,
        "invoke_exit_code": exit_code,
        "invoke_args": argv,
    }, 200 if exit_code == 0 else 500


@app.post("/execute_task")
def execute_task():
    """HTTP 封装：从请求中读取 JSON，调用 execute_task_impl，并返回 JSON 响应。"""
    payload = request.get_json(silent=True) or {}
    result, status = execute_task_impl(payload)
    return jsonify(result), status


def main(argv: list[str] | None = None) -> None:
    """启动 Flask 执行服务。"""
    global DEFAULT_RUN_DIR

    parser = argparse.ArgumentParser(description="Skill 执行服务（plan_task + browser.invoke）")
    parser.add_argument(
        "--run-dir",
        default=DEFAULT_RUN_DIR,
        help="默认 Detect run_dir / 技能库目录（可为相对仓库根目录的路径）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5001,
        help="Flask 监听端口（默认 5001）",
    )
    args = parser.parse_args(argv)
    DEFAULT_RUN_DIR = _resolve_run_dir(args.run_dir)
    print(f"[front.app] DEFAULT_RUN_DIR = {DEFAULT_RUN_DIR}")
    app.run(host="127.0.0.1", port=int(args.port), debug=True)


if __name__ == "__main__":  # pragma: no cover
    main()
