from __future__ import annotations

"""
planner.planner

基于本地候选筛选 + LLM，生成“使用哪些技能”的执行计划（只选技能，不填参、不执行）。

核心目标：在尽量少 token 的前提下，把自然语言任务映射为一个简单的步骤列表：
Plan = {
  "task": "...",
  "steps": [{"skill_id": "d316", "reason": "..."}, ...],
  "backups": ["d97", "d896"],
  "meta": {...}
}
"""

import argparse
import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .candidate_selector import CandidateSkill, select_candidates
from .config import get_llm_config, LLMConfig
from .llm_client import complete_json


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def _load_prompt(name: str) -> str:
    """从 planner/prompt 目录加载提示词模板。"""
    here = os.path.dirname(__file__)
    p = os.path.join(here, "prompt", name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        # 兜底：返回一个简单的英文说明，避免因缺失文件导致崩溃
        return (
            "You are a planner that selects skills from a candidate list.\n"
            "Return a JSON object with fields: steps (array of {skill_id, reason}) and backups (array of skill_id).\n"
            "Only use skill_id values that appear in the candidates.\n"
        )


@dataclass
class PlanStep:
    skill_id: str
    reason: str


@dataclass
class Plan:
    task: str
    steps: List[PlanStep]
    backups: List[str]
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "steps": [asdict(s) for s in self.steps],
            "backups": list(self.backups),
            "meta": dict(self.meta),
        }


def _load_page_summary(run_dir: str) -> Dict[str, Any]:
    p = os.path.join(run_dir, "page_summary.json")
    return _read_json(p)


def _load_skills_index(run_dir: str, skills_index_path: Optional[str] = None) -> Dict[str, Any]:
    if skills_index_path:
        p = skills_index_path
    else:
        p = os.path.join(run_dir, "skill", "skills_index.json")
    return _read_json(p)


def _llm_plan_from_candidates(
    task: str,
    candidates: List[CandidateSkill],
    page_summary: Dict[str, Any],
    *,
    config: Optional[LLMConfig] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """调用 LLM，在给定候选列表的前提下生成 plan JSON。"""
    meta = page_summary.get("meta") or {}
    blocks = page_summary.get("blocks") or []
    main_id = page_summary.get("main_block_id")
    main_block = None
    for b in blocks:
        if isinstance(b, dict) and str(b.get("id")) == str(main_id):
            main_block = b
            break
    def _summarise_candidate(c: CandidateSkill) -> Dict[str, Any]:
        """将候选技能整理成喂给 LLM 的精简结构。

        关键字段：
        - id/name：标识技能；
        - description：技能做什么的简短自然语言概述（来自技能主函数 docstring）；
        - arg_names/has_args：参数名列表与是否需要参数；
        - selectors/score/reason：供模型在需要时参考的附加线索。
        """
        raw = c.raw or {}
        args = raw.get("args") or []
        has_args = bool(args)
        arg_names = [a.get("name") for a in args if isinstance(a, dict) and a.get("name")]
        description = str(raw.get("description") or "").strip()
        return {
            "id": c.id,
            "name": c.name,
            "description": description,
            "selectors": c.selectors,
            "has_args": has_args,
            "arg_names": arg_names,
            "score": round(float(c.score), 3),
            "reason": c.reason,
        }

    cand_view = [_summarise_candidate(c) for c in candidates]

    prompt_obj = {
        "task": task,
        "page": {
            "url": meta.get("url"),
            "title": meta.get("title"),
            "domain": meta.get("domain"),
            "main_block": {
                "id": main_block.get("id") if isinstance(main_block, dict) else None,
                "selector": main_block.get("selector") if isinstance(main_block, dict) else None,
                "short_name": main_block.get("short_name") if isinstance(main_block, dict) else None,
                "short_desc": main_block.get("short_desc") if isinstance(main_block, dict) else None,
            },
        },
        "candidates": cand_view,
        "instructions": {
            "goal": "从候选技能列表中选出最合适的一到数个技能，用于完成任务。",
            "constraints": [
                "只能使用给定 candidates 中的 skill_id，不要发明新的。",
                "steps 应按执行顺序排列；若单一步即可完成任务，可以只给一个 step。",
                "backups 用于回退，通常 1~2 个即可；可以为空列表。",
            ],
            "output_schema": {
                "steps": [
                    {"skill_id": "string", "reason": "string in Chinese explaining why"}
                ],
                "backups": ["string"]
            },
        },
    }

    prompt = json.dumps(prompt_obj, ensure_ascii=False, indent=2)
    sys_msg = _load_prompt("plan_skill_selection.md")
    # 选择阶段使用温度 0，尽量避免随机性
    plan_json = complete_json(prompt, system=sys_msg, temperature=0.0, config=config, verbose=verbose)
    if not isinstance(plan_json, dict):
        raise ValueError("LLM 返回的 plan 不是 JSON 对象")
    return plan_json


def build_plan(
    task: str,
    run_dir: str,
    *,
    top_k: int = 5,
    current_url: Optional[str] = None,
    skills_index_path: Optional[str] = None,
    use_llm: bool = True,
    verbose: bool = True,
) -> Plan:
    """主入口：基于任务与 run_dir，生成一个简单的 Plan 结构。"""
    if verbose:
        print(f"[planner] run_dir={run_dir}")
    page_summary = _load_page_summary(run_dir)
    skills_index = _load_skills_index(run_dir, skills_index_path)
    url = current_url or (page_summary.get("meta") or {}).get("url")

    # 1) 本地候选筛选
    cands = select_candidates(
        task=task,
        page_summary=page_summary,
        skills_index=skills_index,
        current_url=url,
        top_k=top_k,
    )
    if verbose:
        print(f"[planner] candidates={[(c.id, round(c.score,3)) for c in cands]}")
    if not cands:
        return Plan(
            task=task,
            steps=[],
            backups=[],
            meta={
                "run_dir": run_dir,
                "reason": "no_candidates",
            },
        )

    # 2) 若未启用 LLM 或环境未配置 API_KEY，则退化为“只用 Top1”的简单计划
    cfg = get_llm_config()
    if (not use_llm) or (not cfg.api_key):
        top = cands[0]
        if verbose:
            print("[planner] LLM 不可用或未启用，退化为 Top1 计划")
        return Plan(
            task=task,
            steps=[PlanStep(skill_id=top.id, reason="top1_no_llm")],
            backups=[c.id for c in cands[1:3]],
            meta={
                "run_dir": run_dir,
                "llm_used": False,
            },
        )

    # 3) 调用 LLM 生成计划
    raw_plan = _llm_plan_from_candidates(task, cands, page_summary, config=cfg, verbose=verbose)
    # 4) 规范化与安全过滤
    cand_ids = {c.id for c in cands}
    steps_in: List[Dict[str, Any]] = []
    if isinstance(raw_plan.get("steps"), list):
        for s in raw_plan["steps"]:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("skill_id") or "").strip()
            reason = str(s.get("reason") or "").strip()
            if sid and sid in cand_ids:
                steps_in.append({"skill_id": sid, "reason": reason or "by_llm"})

    if not steps_in:
        # 若 LLM 输出不可用，退化为 Top1
        top = cands[0]
        if verbose:
            print("[planner] LLM 计划无有效步骤，退化为 Top1 计划")
        steps = [PlanStep(skill_id=top.id, reason="top1_fallback")]
        backups = [c.id for c in cands[1:3]]
    else:
        steps = [PlanStep(skill_id=s["skill_id"], reason=s["reason"]) for s in steps_in]
        # backups：LLM 提供的 backups + 其余高分候选
        backups_raw = raw_plan.get("backups") or []
        backups: List[str] = []
        if isinstance(backups_raw, list):
            for b in backups_raw:
                sid = str(b or "").strip()
                if sid and sid in cand_ids and sid not in backups and sid not in [s.skill_id for s in steps]:
                    backups.append(sid)
        for c in cands:
            if c.id not in backups and c.id not in [s.skill_id for s in steps]:
                backups.append(c.id)
        backups = backups[:5]

    return Plan(
        task=task,
        steps=steps,
        backups=backups,
        meta={
            "run_dir": run_dir,
            "llm_used": True,
            "candidate_count": len(cands),
        },
    )


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Build skill execution plan from task + detect run dir")
    ap.add_argument("--run-dir", required=True, help="Detect run dir (must contain page_summary.json and skill/skills_index.json)")
    ap.add_argument("--task", required=True, help="Natural language task description")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--url", type=str, default=None, help="Override current URL (default: meta.url)")
    ap.add_argument("--no-llm", dest="use_llm", action="store_false", help="Disable LLM and always fall back to Top1 plan")
    ap.add_argument("--out", type=str, default=None, help="Optional output path for plan.json")
    ap.add_argument("--no-verbose", dest="verbose", action="store_false")
    args = ap.parse_args()

    plan = build_plan(
        task=args.task,
        run_dir=args.run_dir,
        top_k=args.top_k,
        current_url=args.url,
        skills_index_path=None,
        use_llm=getattr(args, "use_llm", True),
        verbose=getattr(args, "verbose", True),
    )
    d = plan.to_dict()
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
