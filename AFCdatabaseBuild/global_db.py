"""
global_db

在 AFCdatabaseBuild 层面提供“全局模式下聚合 + 初始化权重”的能力：

- 从多个 run_dir 的 AFC 快照（afc_page_snapshot / afc_skill_snapshot）构建一个全局 AFC 库；
- 为每个 SkillCase 初始化一份 theta_weights；
- 可选地使用 prompt/LLM_global_afc_aggregate.md 对单个 abstract_skill_id 的全局语义与初始权重做精细化估计。

注意：
  - 这是“初始全局库”的构建模块，后续的动态进化（根据执行结果更新权重）建议放在 AFCdatabaseEvolve 中实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import json
import os

from skill.llm_client import LLMConfig, complete_text


JsonDict = Dict[str, Any]


@dataclass
class GlobalDb:
    """全局 AFC 库的内存表示."""

    rows: List[JsonDict]
    index: Dict[str, JsonDict]
    path: Optional[Path] = None


def _ensure_path(path: os.PathLike[str] | str) -> Path:
    return path if isinstance(path, Path) else Path(path)


def load_global_db(path: os.PathLike[str] | str) -> GlobalDb:
    """从 JSONL 文件加载 AFC 全局库（如不存在可在调用方创建空 GlobalDb）。"""
    p = _ensure_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"global AFC db not found: {p}")

    rows: List[JsonDict] = []
    index: Dict[str, JsonDict] = {}

    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except Exception as e:
                raise ValueError(f"failed to parse JSONL line {lineno} in {p}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"line {lineno} in {p} is not a JSON object")
            rows.append(obj)
            aid = obj.get("abstract_skill_id")
            if isinstance(aid, str):
                index[aid] = obj

    return GlobalDb(rows=rows, index=index, path=p)


def save_global_db(db: GlobalDb, path: os.PathLike[str] | str | None = None) -> Path:
    """将 GlobalDb 写回 JSONL 文件."""
    target = _ensure_path(path or db.path)
    if target is None:
        raise ValueError("save_global_db requires a target path when db.path is None")

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for obj in db.rows:
            f.write(json.dumps(obj, ensure_ascii=False))
            f.write("\n")
    return target


def index_by_id(rows: Iterable[Mapping[str, Any]]) -> Dict[str, JsonDict]:
    """从记录序列构建 abstract_skill_id -> entry 的索引视图."""
    idx: Dict[str, JsonDict] = {}
    for obj in rows:
        if not isinstance(obj, Mapping):
            continue
        aid = obj.get("abstract_skill_id")
        if isinstance(aid, str):
            idx[aid] = dict(obj)
    return idx


def _read_json(path: Path) -> JsonDict:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"JSON at {path} is not an object")
    return obj


def _build_s_invariant(control: JsonDict) -> JsonDict:
    """从 AfcControl 构造一个简化版 S_invariant."""
    semantic = control.get("semantic_signature") or {}
    action = control.get("action")
    return {
        "clean_text": semantic.get("clean_text") or [],
        "norm_label": semantic.get("norm_label"),
        "action": action,
        "role": semantic.get("role") or [],
        "url_pattern": semantic.get("url_pattern"),
        "form_context": semantic.get("form_context"),
        "env": {
            "login_state": semantic.get("login_state"),
            "cookies_required": semantic.get("cookies_required"),
            "viewport_min": semantic.get("viewport_min"),
            "env_sensitivity": semantic.get("env_sensitivity"),
        },
    }


def _init_theta_weights() -> JsonDict:
    """默认的初始特征权重（在未使用 LLM 估计时使用）。"""
    return {
        "clean_text": 1.0,
        "norm_label": 1.0,
        "action": 1.0,
        "role": 1.0,
        "url_pattern": 1.0,
        "env.login_state": 1.0,
    }


def _load_global_agg_template() -> str:
    """从 prompt/LLM_global_afc_aggregate.md 中加载全局聚合 prompt 模板。"""
    here = Path(__file__).resolve().parent  # AFCdatabaseBuild/
    md_path = here / "prompt" / "LLM_global_afc_aggregate.md"
    if not md_path.is_file():
        return ""
    text = md_path.read_text(encoding="utf-8")
    start = "<!-- GLOBAL_AFC_AGG_PROMPT_BEGIN -->"
    end = "<!-- GLOBAL_AFC_AGG_PROMPT_END -->"
    i1, i2 = text.find(start), text.find(end)
    if i1 == -1 or i2 == -1 or i2 <= i1:
        return ""
    return text[i1 + len(start) : i2].strip()


def _llm_estimate_theta(global_entry: JsonDict, observations: List[JsonDict]) -> Optional[JsonDict]:
    """可选地调用 LLM，对某个 abstract_skill_id 的初始 theta_weights 做精细估计。

    行为：
      - 若找不到 prompt 模板，或 LLM 调用失败，则返回 None；
      - 若成功，则返回一个包含 theta_weights / global_semantic_text / global_env_sensitivity 的 dict。
    """
    template = _load_global_agg_template()
    if not template:
        return None

    context = {
        "abstract_skill_id": global_entry.get("abstract_skill_id"),
        "task_group": (global_entry.get("semantic_signature_global") or {}).get("task_group"),
        "task_role": (global_entry.get("semantic_signature_global") or {}).get("task_role"),
        "semantic_text": (global_entry.get("semantic_signature_global") or {}).get("semantic_text"),
        "env_sensitivity": (global_entry.get("semantic_signature_global") or {}).get("env_sensitivity"),
        "observations": observations,
    }

    prompt = template.replace("{{CONTEXT_JSON}}", json.dumps(context, ensure_ascii=False, indent=2))
    try:
        cfg = LLMConfig()
        resp = complete_text(prompt, config=cfg, temperature=0.0, max_tokens=768, verbose=False)
        data = json.loads(resp)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    return data


def _ensure_global_entry(db: GlobalDb, abstract_entry: JsonDict) -> JsonDict:
    """确保全局库中存在某个 abstract_skill_id 的记录，不存在则创建。"""
    aid = abstract_entry.get("abstract_skill_id")
    if not isinstance(aid, str):
        raise ValueError("abstract_skill_entry missing valid abstract_skill_id")

    existing = db.index.get(aid)
    if existing is not None:
        return existing

    sem_sig = abstract_entry.get("semantic_signature") or {}
    global_entry: JsonDict = {
        "abstract_skill_id": aid,
        "semantic_signature_global": {
            "task_group": abstract_entry.get("task_group"),
            "task_role": abstract_entry.get("task_role"),
            "norm_label": abstract_entry.get("norm_label"),
            "action": abstract_entry.get("action"),
            "semantic_text": sem_sig.get("semantic_text"),
            "env_sensitivity": sem_sig.get("env_sensitivity"),
        },
        "afc_controls": [],
        "concrete_skills": [],
        "skill_cases": [],
    }
    db.rows.append(global_entry)
    db.index[aid] = global_entry
    return global_entry


def _existing_case_keys(entry: JsonDict) -> List[Tuple[str, str, str]]:
    """返回当前 entry 中已有 SkillCase 的 (run_dir, afc_control_id, skill_id) 键集合。"""
    keys: List[Tuple[str, str, str]] = []
    for case in entry.get("skill_cases") or []:
        rd = str(case.get("run_dir") or "")
        cid = str(case.get("afc_control_id") or "")
        sid = str(case.get("skill_id") or "")
        if rd and cid and sid:
            keys.append((rd, cid, sid))
    return keys


def integrate_run_dir(db: GlobalDb, run_dir: str | Path, *, use_llm: bool = False) -> None:
    """将单个 run_dir 的 AFC 快照合并进全局 AFC 库 db，并初始化权重。

    参数：
      - db:      GlobalDb 内存结构；
      - run_dir: 单个 Detect+Skill 产物目录；
      - use_llm: 若为 True，则尝试使用 LLM_global_afc_aggregate.md 对初始 theta_weights 进行精细估计。
    """
    run_dir = Path(run_dir)
    page_path = run_dir / "afc" / "afc_page_snapshot.json"
    skill_path = run_dir / "afc" / "afc_skill_snapshot.json"
    if not page_path.is_file():
        raise FileNotFoundError(f"AfcPageSnapshot not found: {page_path}")
    if not skill_path.is_file():
        raise FileNotFoundError(f"AfcSkillSnapshot not found: {skill_path}")

    page_obj = _read_json(page_path)
    skill_obj = _read_json(skill_path)

    domain = str(skill_obj.get("domain") or page_obj.get("domain") or "")
    controls = page_obj.get("controls") or []
    controls_by_id: Dict[str, JsonDict] = {}
    for c in controls:
        cid = c.get("control_id")
        if isinstance(cid, str):
            controls_by_id[cid] = c

    abstract_skills = skill_obj.get("abstract_skills") or []
    for abstract_entry in abstract_skills:
        if not isinstance(abstract_entry, dict):
            continue
        global_entry = _ensure_global_entry(db, abstract_entry)

        # afc_controls：追加 domain/run_dir/控制信息
        existing_controls = global_entry.get("afc_controls") or []
        existing_ctrl_keys = {(c.get("domain"), c.get("run_dir"), c.get("control_id")) for c in existing_controls}

        for ctrl_ref in abstract_entry.get("afc_controls") or []:
            cid = ctrl_ref.get("control_id")
            if not isinstance(cid, str):
                continue
            key = (domain, str(run_dir), cid)
            if key in existing_ctrl_keys:
                continue
            ctrl_record = {"domain": domain, "run_dir": str(run_dir), "control_id": cid}
            existing_controls.append(ctrl_record)
            existing_ctrl_keys.add(key)

        global_entry["afc_controls"] = existing_controls

        # concrete_skills：追加 domain/run_dir/skill_id
        existing_skills = global_entry.get("concrete_skills") or []
        existing_skill_keys = {(s.get("domain"), s.get("run_dir"), s.get("skill_id")) for s in existing_skills}

        for sk_ref in abstract_entry.get("concrete_skills") or []:
            sid = sk_ref.get("skill_id")
            if not isinstance(sid, str):
                continue
            key = (domain, str(run_dir), sid)
            if key in existing_skill_keys:
                continue
            sk_record = {"domain": domain, "run_dir": str(run_dir), "skill_id": sid}
            existing_skills.append(sk_record)
            existing_skill_keys.add(key)

        global_entry["concrete_skills"] = existing_skills

        # SkillCase：为本 run_dir 上的控件+技能组合创建初始 SkillCase
        existing_case_key_list = _existing_case_keys(global_entry)
        existing_case_keys = set(existing_case_key_list)
        cases = global_entry.get("skill_cases") or []

        control_ids = [
            c.get("control_id")
            for c in abstract_entry.get("afc_controls") or []
            if isinstance(c.get("control_id"), str)
        ]
        skill_ids = [
            s.get("skill_id")
            for s in abstract_entry.get("concrete_skills") or []
            if isinstance(s.get("skill_id"), str)
        ]

        # 收集当前 abstract_skill 在多 run_dir 上的观测，用于 LLM 估计权重
        observations: List[JsonDict] = []

        for cid in control_ids:
            ctl = controls_by_id.get(cid)
            if not isinstance(ctl, dict):
                continue
            S_inv = _build_s_invariant(ctl)
            observations.append(
                {"run_dir": str(run_dir), "domain": domain, "afc_control_id": cid, "S_invariant": S_inv}
            )
            for sid in skill_ids or [None]:
                sid_str = str(sid) if sid is not None else ""
                key = (str(run_dir), cid, sid_str)
                if key in existing_case_keys:
                    continue
                case: JsonDict = {
                    "run_dir": str(run_dir),
                    "domain": domain,
                    "afc_control_id": cid,
                    "skill_id": sid_str or None,
                    "S_invariant": S_inv,
                    "A_template": {
                        "program_entry": None,
                        "args_schema": None,
                    },
                    "R_history": {
                        "exec_success": 0,
                        "exec_fail": 0,
                    },
                    "theta_weights": _init_theta_weights(),
                    "levels": None,
                    "rebuild_grade": None,
                }
                cases.append(case)
                existing_case_keys.add(key)

        global_entry["skill_cases"] = cases

        # 可选：使用 LLM 对该 abstract_skill 的全局语义与 theta_weights 做一次估计
        if use_llm and observations:
            suggestion = _llm_estimate_theta(global_entry, observations)
            if isinstance(suggestion, dict):
                tw = suggestion.get("theta_weights")
                if isinstance(tw, dict):
                    for case in global_entry.get("skill_cases") or []:
                        case["theta_weights"] = dict(tw)
                gs = suggestion.get("global_semantic_text")
                if isinstance(gs, str) and gs.strip():
                    global_entry.setdefault("semantic_signature_global", {})["semantic_text"] = gs.strip()
                ge = suggestion.get("global_env_sensitivity")
                if isinstance(ge, dict):
                    global_entry.setdefault("semantic_signature_global", {})["env_sensitivity"] = ge
                ev = suggestion.get("evidence_summary")
                if isinstance(ev, str) and ev.strip():
                    global_entry.setdefault("evolve_notes", {})["global_agg_evidence"] = ev.strip()


__all__ = [
    "JsonDict",
    "GlobalDb",
    "load_global_db",
    "save_global_db",
    "index_by_id",
    "integrate_run_dir",
]

