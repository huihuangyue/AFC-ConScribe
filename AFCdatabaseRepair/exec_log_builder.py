"""
AFCdatabaseRepair.exec_log_builder

将 Repair 阶段的多次执行尝试（trials）整理为统一的 exec_log JSON，供
AFCdatabaseEvolve.integrate_run_with_evolution 使用。

exec_log 的结构约定见：workspace/进化/exec_log.md

典型用法：

    from pathlib import Path
    from AFCdatabaseRepair.exec_log_builder import build_exec_log, write_exec_log

    exec_log = build_exec_log(
        run_dir_new=Path("workspace/data/ctrip_com/20251216193438"),
        abstract_skill_id="HotelSearch.Submit:Clickable_Submit",
        trials=[
            {
                "afc_control_id": "control_001",
                "skill_id": "d318",
                "exec_result": exec_result_obj,   # ExecResult 或 dict
                "sim_S": 0.93,
                "reuse_A": 0.80,
                "notes": "CBR+LLM 修复成功，selector 更新为新 class。"
            },
            ...
        ],
        task="在携程上查 1 月 1–2 日北京天坛附近酒店",
    )
    write_exec_log(Path("workspace/进化/exec_log_ctrip_20251216193438.json"), exec_log)
"""

from __future__ import annotations

from dataclasses import is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import json

from .exec_runner import ExecResult


JsonDict = Dict[str, Any]


def _exec_result_to_dict(er: ExecResult | Dict[str, Any]) -> JsonDict:
    """将 ExecResult 或类似 dict 转为统一的 dict 视图。"""
    if isinstance(er, ExecResult):
        return er.to_dict()
    if isinstance(er, dict):
        return dict(er)
    # 其他类型尽量容忍，调用方自己负责字段缺失
    return {"raw": er}


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    """将 time.time() 风格时间戳转为 ISO8601 字符串。"""
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def build_execution_case(
    abstract_skill_id: str,
    run_dir_new: Path,
    trial: Dict[str, Any],
) -> JsonDict:
    """从单次 trial 构造一条 exec_log.skill_cases[*] 记录（ExecutionCase）。

    trial 约定（宽松）：
      - 必需：
        - "afc_control_id": str
        - "skill_id": str 或 None
        - "exec_result": ExecResult 或 dict，至少包含 ok / exit_code / timestamps
      - 可选：
        - "sim_S", "reuse_A", "L_S", "L_A", "rebuild_grade", "drift_E"
        - "code_diff_summary": dict
        - "precomputed_metrics": dict
        - "notes": str
    """
    afc_control_id = str(trial.get("afc_control_id") or "")
    skill_id = trial.get("skill_id")
    skill_id_str = str(skill_id) if skill_id is not None else None

    er_dict = _exec_result_to_dict(trial.get("exec_result"))
    exec_success = bool(er_dict.get("ok"))
    error_type = er_dict.get("error_type")
    # 优先使用 ExecResult.finished_at 作为 timestamp
    finished_at = er_dict.get("finished_at")
    timestamp_iso = _ts_to_iso(finished_at) or _ts_to_iso(er_dict.get("started_at"))

    case: JsonDict = {
        "abstract_skill_id": abstract_skill_id,
        "run_dir": str(run_dir_new),
        "afc_control_id": afc_control_id,
        "skill_id": skill_id_str,
        "exec_success": exec_success,
        "error_type": error_type,
        "timestamp": timestamp_iso,
    }

    # 可选度量字段：直接透传 trial 或由调用方事先计算
    for key in ("sim_S", "reuse_A", "L_S", "L_A", "rebuild_grade", "drift_E"):
        if key in trial:
            case[key] = trial.get(key)

    code_diff = trial.get("code_diff_summary")
    if isinstance(code_diff, dict):
        case["code_diff_summary"] = code_diff
    pre_metrics = trial.get("precomputed_metrics")
    if isinstance(pre_metrics, dict):
        case["precomputed_metrics"] = pre_metrics

    notes = trial.get("notes")
    if isinstance(notes, str) and notes.strip():
        case["notes"] = notes.strip()

    return case


def build_exec_log(
    run_dir_new: Path,
    abstract_skill_id: str,
    trials: Iterable[Dict[str, Any]],
    *,
    task: Optional[str] = None,
    version: str = "0.1",
) -> JsonDict:
    """构造一个完整的 exec_log 根对象（不写盘）。

    参数：
      - run_dir_new: 新页面的 run_dir；
      - abstract_skill_id: 本批次修复主要针对的抽象技能 id；
      - trials: 一组 trial dict，每条由 build_execution_case 负责解释；
      - task: 可选，自然语言任务描述；
      - version: 协议版本号，便于未来演进。

    返回：
      - exec_log 根对象，形如：
        {
          "version": "0.1",
          "run_dir": str(run_dir_new),
          "abstract_skill_id": "...",
          "task": "...",
          "created_at": "2025-12-19T12:34:56Z",
          "skill_cases": [ ExecutionCase, ... ]
        }
    """
    run_dir_new = run_dir_new.resolve()
    created_at = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")

    cases: List[JsonDict] = []
    for trial in trials:
        if not isinstance(trial, dict):
            continue
        case = build_execution_case(abstract_skill_id, run_dir_new, trial)
        cases.append(case)

    return {
        "version": version,
        "run_dir": str(run_dir_new),
        "abstract_skill_id": abstract_skill_id,
        "task": task,
        "created_at": created_at,
        "skill_cases": cases,
    }


def write_exec_log(path: Path, exec_log: JsonDict) -> None:
    """将 exec_log 根对象写入指定路径（UTF-8, 缩进 2）。"""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(exec_log, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["build_execution_case", "build_exec_log", "write_exec_log"]

