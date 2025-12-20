"""
AFCdatabaseRepair.exec_runner

在真实浏览器环境中执行一个 Skill（或修复建议），用于在 Repair 阶段验证候选方案。

设计目标：
  - 不直接依赖前端 Flask 服务，而是复用 `browser.invoke` 这一低层执行入口；
  - 封装好 argv 构造与基本元信息，返回一个结构化的执行结果对象，方便之后构建 exec_log；
  - 与 `AFCdatabaseRepair.code_adapter.RepairProposal` 协同工作：
      * 将 proposal.skill_json 写入指定路径；
      * 用给定的 call_str 调用 `browser.invoke.main`；
      * 记录 exit_code / 时间戳 / 调用参数等。

注意：
  - 本模块不解析浏览器输出的详细错误信息，只根据 exit_code 区分成功/失败；
  - 若需要更细粒度的错误类型（例如 NoSuchElement/Timeout），可以在 skill 程序内部
    将异常信息结构化返回，或在未来扩展 browser.invoke 的返回协议。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import json
import os
import time

from browser.invoke import main as browser_invoke_main  # type: ignore[import]

from .code_adapter import RepairProposal


JsonDict = Dict[str, Any]


@dataclass
class ExecResult:
    """一次对某个技能的执行结果（便于构建 exec_log.skill_cases[*]）。"""

    ok: bool
    exit_code: int
    skill_path: Path
    call_str: str
    started_at: float
    finished_at: float
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "skill_path": str(self.skill_path),
            "call_str": self.call_str,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_skill_json(path: Path, skill_json: JsonDict) -> None:
    """将 skill_json 写入指定路径（UTF-8, 缩进 2）。"""
    _ensure_parent_dir(path)
    path.write_text(json.dumps(skill_json, ensure_ascii=False, indent=2), encoding="utf-8")


def run_skill_file(
    skill_path: Path,
    call_str: str,
    *,
    start_url: Optional[str] = None,
    slow_mo_ms: int = 150,
    default_timeout_ms: int = 12000,
    keep_open: bool = False,
) -> ExecResult:
    """调用 browser.invoke.main 在真实浏览器中执行指定 skill。

    参数：
      - skill_path: 包含 program.code 的 skill JSON 路径；
      - call_str: 传给 `--invoke` 的 Python 调用字符串；
      - start_url: 可选，显式指定浏览器起始 URL（未提供则由 browser.invoke 自行推导）；
      - slow_mo_ms: 浏览器慢动作延时（默认 150）；
      - default_timeout_ms: 默认超时（毫秒，默认 12000）；
      - keep_open: 执行结束后是否保持浏览器打开（默认 False）。

    返回：
      - ExecResult：包含 ok / exit_code / 时间戳 / 基本错误类型说明。
    """
    skill_path = skill_path.resolve()
    if not skill_path.is_file():
        raise FileNotFoundError(f"skill JSON not found: {skill_path}")

    argv = [
        "--skill",
        str(skill_path),
        "--invoke",
        str(call_str),
        "--slow-mo-ms",
        str(int(slow_mo_ms)),
        "--default-timeout-ms",
        str(int(default_timeout_ms)),
    ]
    if start_url:
        argv.extend(["--url", str(start_url)])
    if keep_open:
        argv.append("--keep-open")
    else:
        argv.append("--no-keep-open")

    t0 = time.time()
    exit_code = int(browser_invoke_main(argv))
    t1 = time.time()

    ok = exit_code == 0
    # 目前 browser.invoke 只通过 stdout 打印错误，不提供结构化错误类型。
    # 这里先用一个简单的占位 error_type，真正的错误信息可以在技能代码中结构化返回。
    error_type = None if ok else "InvokeNonZeroExit"
    error_message = None

    return ExecResult(
        ok=ok,
        exit_code=exit_code,
        skill_path=skill_path,
        call_str=str(call_str),
        started_at=t0,
        finished_at=t1,
        error_type=error_type,
        error_message=error_message,
    )


def run_repair_proposal(
    proposal: RepairProposal,
    call_str: str,
    *,
    start_url: Optional[str] = None,
    slow_mo_ms: int = 150,
    default_timeout_ms: int = 12000,
    keep_open: bool = False,
    write_skill: bool = True,
) -> ExecResult:
    """执行一个 RepairProposal，并返回 ExecResult。

    行为：
      1. 如 write_skill=True，则将 proposal.skill_json 写入 proposal.program_path；
      2. 调用 run_skill_file(proposal.program_path, call_str, ...) 实际执行；
      3. 返回 ExecResult，供 exec_log_builder 等模块使用。

    注意：
      - 本函数不会修改 proposal 对象本身；
      - exec_log 中的更高层字段（如 abstract_skill_id / run_dir / sim_S 等）由调用方负责补充。
    """
    skill_path = proposal.program_path
    if write_skill:
        _write_skill_json(skill_path, proposal.skill_json)

    return run_skill_file(
        skill_path=skill_path,
        call_str=call_str,
        start_url=start_url,
        slow_mo_ms=slow_mo_ms,
        default_timeout_ms=default_timeout_ms,
        keep_open=keep_open,
    )


__all__ = ["ExecResult", "run_skill_file", "run_repair_proposal"]

