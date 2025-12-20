"""
AFCdatabaseEvolve.cli

命令行入口：在已有全局 AFC 库的基础上，引入一个新的 run_dir，并可选地根据 exec_log
对库做一次“进化更新”。

典型用法（在仓库根目录）：

  # 仅将新的 run_dir 作为观测样本纳入全局库（不带执行日志）
  PYTHONPATH=. python -m AFCdatabaseEvolve.cli \
    --global-db workspace/AFCdatabase/db/abstract_skills_global.jsonl \
    --run-dir workspace/AFCdatabase/db/runs/jd_com__20251219213050

  # 同时使用一次执行/修复日志，对 SkillCase / theta 做进化更新
  PYTHONPATH=. python -m AFCdatabaseEvolve.cli \
    --global-db workspace/AFCdatabase/db/abstract_skills_global.jsonl \
    --run-dir workspace/AFCdatabase/db/runs/jd_com__20251219213050 \
    --exec-log workspace/进化/exec_log_jd.json \
    --use-llm-rating

说明：
  - 本 CLI 只是对 integrate_run_with_evolution 的轻薄封装，方便在命令行上调用；
  - 目前一次只处理一个 run_dir，如需批量可以在外层脚本中多次调用本 CLI；
  - exec_log 的 JSON 结构约定参见 AFCdatabaseEvolve.integrate_run.integrate_run_with_evolution
    的 docstring（至少包含 exec_log["skill_cases"] 列表）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .integrate_run import integrate_run_with_evolution


JsonDict = Dict[str, Any]


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "在已有 abstract_skills_global.jsonl 的基础上，引入一个新的 run_dir 并做一次 AFC 进化更新。\n\n"
            "示例：\n"
            "  PYTHONPATH=. python -m AFCdatabaseEvolve.cli \\\n"
            "    --global-db workspace/AFCdatabase/db/abstract_skills_global.jsonl \\\n"
            "    --run-dir workspace/AFCdatabase/db/runs/jd_com__20251219213050 \\\n"
            "    --exec-log workspace/进化/exec_log_jd.json \\\n"
            "    --use-llm-rating\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    ap.add_argument(
        "--global-db",
        required=True,
        help="全局 AFC 库 JSONL 文件路径，如 workspace/AFCdatabase/db/abstract_skills_global.jsonl",
    )
    ap.add_argument(
        "--run-dir",
        required=True,
        help=(
            "要整合的 run_dir 路径，需包含 afc/afc_page_snapshot.json 和 afc/afc_skill_snapshot.json。\n"
            "例如：workspace/AFCdatabase/db/runs/jd_com__20251219213050 或原始 workspace/data/<domain>/<ts>/。"
        ),
    )
    ap.add_argument(
        "--exec-log",
        default=None,
        help=(
            "可选：执行/修复日志 JSON 文件路径，内部需包含 exec_log['skill_cases'] 列表。\n"
            "若不提供，则仅将 run_dir 作为新的观测样本纳入全局库，不根据执行结果更新权重。"
        ),
    )
    ap.add_argument(
        "--use-llm-rating",
        action="store_true",
        help="启用 LLM 进行 (L_S, L_A, rebuild_grade) 与 theta 建议估计（默认关闭，仅用规则）。",
    )

    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    global_db_path = Path(args.global_db).resolve()
    run_dir = Path(args.run_dir).resolve()

    if not global_db_path.is_file():
        print(f"[evolve.cli] ERROR: global_db_path 不存在: {global_db_path}", file=sys.stderr)
        return 1
    if not run_dir.is_dir():
        print(f"[evolve.cli] ERROR: run_dir 不是目录或不存在: {run_dir}", file=sys.stderr)
        return 1

    exec_log: Optional[JsonDict] = None
    if args.exec_log:
        exec_log_path = Path(args.exec_log).resolve()
        if not exec_log_path.is_file():
            print(f"[evolve.cli] ERROR: exec_log 文件不存在: {exec_log_path}", file=sys.stderr)
            return 1
        try:
            with exec_log_path.open("r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                exec_log = obj
            else:
                raise ValueError("exec_log JSON 根节点必须是对象")
        except Exception as e:
            print(f"[evolve.cli] ERROR: 解析 exec_log JSON 失败: {e}", file=sys.stderr)
            return 1

    print(f"[evolve.cli] global_db = {global_db_path}")
    print(f"[evolve.cli] run_dir   = {run_dir}")
    if exec_log is None:
        print("[evolve.cli] exec_log = (None) 仅整合快照，不基于执行结果更新权重。")
    else:
        n_cases = len(exec_log.get("skill_cases") or [])
        print(f"[evolve.cli] exec_log = {args.exec_log} (skill_cases={n_cases})")
    print(f"[evolve.cli] use_llm_rating = {bool(args.use_llm_rating)}")

    try:
        integrate_run_with_evolution(
            global_db_path=global_db_path,
            run_dir=run_dir,
            exec_log=exec_log,
            use_llm_rating=bool(args.use_llm_rating),
        )
    except Exception as e:
        print(f"[evolve.cli] ERROR: integrate_run_with_evolution 失败: {e}", file=sys.stderr)
        return 1

    print("[evolve.cli] 进化更新完成，已写回全局 AFC 库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

