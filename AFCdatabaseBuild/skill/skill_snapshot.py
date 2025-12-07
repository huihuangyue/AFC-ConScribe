"""
技能级 AFC 抽象快照（单 run_dir 版）。

对应 workspace/AFCdatabase/README.md 中的“步骤 2.2：实现 build_skill_snapshot”：
- 输入：已有 AfcPageSnapshot + Skill JSON 的单个 run_dir；
- 输出：run_dir/afc/afc_skill_snapshot.json（该 run_dir 内部的抽象技能索引）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json
import os

from skill.llm_client import LLMConfig, complete_text


@dataclass
class _AbstractSkillEntry:
    """聚合单个 run_dir 内某个 abstract_skill_id 的信息（中间结构）。"""

    abstract_skill_id: str
    task_group: str
    task_role: str
    norm_label: str
    action: str
    semantic_text: Optional[str] = None
    semantic_vector: Optional[Any] = None  # 留作将来 embedding 使用
    env_sensitivity: Optional[Dict[str, Any]] = None
    io_schema: Optional[Dict[str, Any]] = None
    preconditions_abstract: Optional[Dict[str, Any]] = None
    afc_controls: List[str] = field(default_factory=list)
    skill_ids: List[str] = field(default_factory=list)


def _load_abstract_skill_template() -> str:
    """
    从 AFCdatabaseBuild/prompt/LLM_abstract_skill.md 中加载 abstract_skill 的提示模板。

    约定该文件中存在：
      <!-- ABSTRACT_SKILL_PROMPT_BEGIN -->
      ...（模板正文，包含 {{CONTEXT_JSON}} 占位符）...
      <!-- ABSTRACT_SKILL_PROMPT_END -->

    若模板缺失，则回退到代码内置的简单英文 prompt。
    """
    here = Path(__file__).resolve().parents[1]  # AFCdatabaseBuild/
    md_path = here / "prompt" / "LLM_abstract_skill.md"
    if not md_path.is_file():
        return ""
    text = md_path.read_text(encoding="utf-8")
    start_marker = "<!-- ABSTRACT_SKILL_PROMPT_BEGIN -->"
    end_marker = "<!-- ABSTRACT_SKILL_PROMPT_END -->"
    i1 = text.find(start_marker)
    i2 = text.find(end_marker)
    if i1 == -1 or i2 == -1 or i2 <= i1:
        return ""
    body = text[i1 + len(start_marker) : i2]
    return body.strip()


def _build_abstract_skill_prompt(entry: _AbstractSkillEntry, skills_by_id: Dict[str, Dict[str, Any]]) -> str:
    """构造用于 abstract_skill LLM refine 的 prompt。

    输入上下文尽量包含：
    - 抽象技能的 norm_label / 现有 task_group/task_role；
    - 该技能下的控件 id 列表（用于提示“有多少实例”）；
    - 该技能下所有技能实现的 action / meta.description / preconditions。
    """
    # 构造一个紧凑的 context JSON，传给 LLM
    skills_ctx: List[Dict[str, Any]] = []
    for sid in entry.skill_ids:
        sk = skills_by_id.get(sid)
        if not isinstance(sk, dict):
            continue
        skills_ctx.append(
            {
                "id": sk.get("id"),
                "action": sk.get("action"),
                "description": (sk.get("meta") or {}).get("description") or sk.get("description"),
                "preconditions": sk.get("preconditions") or {},
            }
        )

    context = {
        "abstract_skill_id": entry.abstract_skill_id,
        "current_task_group": entry.task_group,
        "current_task_role": entry.task_role,
        "norm_label": entry.norm_label,
        "action": entry.action,
        "afc_control_ids": entry.afc_controls,
        "skills": skills_ctx,
        "current_semantic_text": entry.semantic_text,
        "current_env_sensitivity": entry.env_sensitivity,
    }

    template = _load_abstract_skill_template()
    if not template:
        # 简单英文 fallback：解释任务并要求返回 JSON
        return (
            "You are helping to define abstract skills for a web automation agent.\n\n"
            "We observed one abstract skill cluster. Its context JSON is:\n\n"
            f"CONTEXT_JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "Based on the skills' descriptions, actions and preconditions, choose:\n"
            "- task_group: a short high-level task family (e.g. HotelSearch, Login, Booking, Filter, Navigation, Misc)\n"
            "- task_role: a short role within the task (e.g. Submit, Search, OpenDetail, Filter, Login, Logout)\n"
            "- semantic_text: one English sentence describing this abstract skill.\n"
            "- env_sensitivity: a JSON object summarizing environment requirements, with keys like "
            "\"requires_login\" (true/false/null), \"requires_enterprise_account\" (true/false/null), "
            "\"device_sensitive\" (e.g. \"any\", \"mobile_only\", \"desktop_only\").\n\n"
            "Return ONLY a JSON object with keys: task_group, task_role, semantic_text, env_sensitivity."
        )

    return template.replace("{{CONTEXT_JSON}}", json.dumps(context, ensure_ascii=False, indent=2))


def _load_afc_page_snapshot(run_dir: Path) -> Dict[str, Any]:
    """读取 run_dir 下的 AfcPageSnapshot JSON。"""
    afc_path = run_dir / "afc" / "afc_page_snapshot.json"
    if not afc_path.is_file():
        raise FileNotFoundError(f"AfcPageSnapshot not found: {afc_path}")
    with afc_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_skills(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    """读取 run_dir/skill 下的 Skill_*.json，返回 id -> skill_json 映射。"""
    skills_dir = run_dir / "skill"
    result: Dict[str, Dict[str, Any]] = {}
    if not skills_dir.is_dir():
        return result

    def _load_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    for entry in skills_dir.iterdir():
        if entry.is_dir():
            json_path = entry / f"{entry.name}.json"
            if json_path.is_file():
                obj = _load_json(json_path)
                if not isinstance(obj, dict):
                    continue
                skill_id = obj.get("id")
                if isinstance(skill_id, str):
                    result[skill_id] = obj
        elif entry.is_file() and entry.name.startswith("Skill_") and entry.suffix == ".json":
            obj = _load_json(entry)
            if not isinstance(obj, dict):
                continue
            skill_id = obj.get("id")
            if isinstance(skill_id, str):
                result[skill_id] = obj
    return result


def _infer_abstract_key(control: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """从 AfcControl 推断 (task_group, task_role, norm_label, action)。

    目前控件级快照中尚未显式提供 task_group/task_role：
    - 优先从 semantic_signature 中读取（若将来补充）；否则使用占位符；
    - norm_label 取 semantic_signature.norm_label，缺省为 'UnknownLabel'；
    - action 直接使用 AfcControl.action，缺省为 'none'。
    """
    semantic = control.get("semantic_signature") or {}

    task_group = semantic.get("task_group") or "UnknownGroup"
    task_role = semantic.get("task_role") or "UnknownRole"
    norm_label = semantic.get("norm_label") or "UnknownLabel"
    action = control.get("action") or "none"

    return str(task_group), str(task_role), str(norm_label), str(action)


def _update_abstract_entry(entry: _AbstractSkillEntry, control: Dict[str, Any]) -> None:
    """用单个 AfcControl 的信息更新聚合条目中的语义字段。"""
    semantic = control.get("semantic_signature") or {}
    # semantic_text：取第一个非空值
    st = semantic.get("semantic_text")
    if isinstance(st, str) and st.strip() and not entry.semantic_text:
        entry.semantic_text = st.strip()
    # semantic_vector：当前先直接沿用第一个非空值（后续若有 embedding 可改为聚合）
    sv = semantic.get("semantic_vector")
    if sv is not None and entry.semantic_vector is None:
        entry.semantic_vector = sv
    # env_sensitivity：如存在，直接带过去
    es = semantic.get("env_sensitivity")
    if isinstance(es, dict) and entry.env_sensitivity is None:
        entry.env_sensitivity = es


def _llm_refine_abstract_skill(entry: _AbstractSkillEntry, skills_by_id: Dict[str, Dict[str, Any]]) -> None:
    """使用 LLM 对抽象技能进行 refine：补全 task_group/task_role/semantic_text/env_sensitivity。

    默认尝试调用 LLM；出错时静默失败，保留规则初稿。
    """
    try:
        cfg = LLMConfig()
        prompt = _build_abstract_skill_prompt(entry, skills_by_id)
        resp = complete_text(prompt, config=cfg, temperature=0.0, max_tokens=512, verbose=False)
        data = json.loads(resp)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        return

    tg = data.get("task_group")
    if isinstance(tg, str) and tg.strip():
        entry.task_group = tg.strip()
    tr = data.get("task_role")
    if isinstance(tr, str) and tr.strip():
        entry.task_role = tr.strip()
    st = data.get("semantic_text")
    if isinstance(st, str) and st.strip():
        entry.semantic_text = st.strip()
    es = data.get("env_sensitivity")
    if isinstance(es, dict):
        entry.env_sensitivity = es
    io = data.get("io_schema")
    if isinstance(io, dict):
        entry.io_schema = io
    pa = data.get("preconditions_abstract")
    if isinstance(pa, dict):
        entry.preconditions_abstract = pa


def build_skill_snapshot(run_dir: str | Path) -> Path:
    """
    为单个 run_dir 构建技能级 AFC 抽象快照。

    输入：
        run_dir: workspace/data/<domain>/<ts>/ 目录
                 要求其下已存在：
                 - afc/afc_page_snapshot.json
                 - skill/Skill_*/Skill_*.json（可选，但推荐存在）

    输出：
        run_dir/afc/afc_skill_snapshot.json 路径。
    """
    run_dir = Path(run_dir)
    snapshot = _load_afc_page_snapshot(run_dir)
    domain = snapshot.get("domain") or ""
    controls: List[Dict[str, Any]] = snapshot.get("controls") or []

    skills_by_id = _load_skills(run_dir)

    # 按 abstract_skill_id 聚合
    aggregated: Dict[str, _AbstractSkillEntry] = {}

    for c in controls:
        control_id = c.get("control_id")
        if not control_id:
            continue
        task_group, task_role, norm_label, action = _infer_abstract_key(c)
        abstract_skill_id = f"{task_group}.{task_role}:{norm_label}"

        entry = aggregated.get(abstract_skill_id)
        if entry is None:
            entry = _AbstractSkillEntry(
                abstract_skill_id=abstract_skill_id,
                task_group=task_group,
                task_role=task_role,
                norm_label=norm_label,
                action=action,
            )
            aggregated[abstract_skill_id] = entry

        # 记录 afc_controls
        if control_id not in entry.afc_controls:
            entry.afc_controls.append(str(control_id))

        # 记录语义信息
        _update_abstract_entry(entry, c)

        # 记录 concrete_skills
        links = c.get("skill_links") or []
        for lk in links:
            sid = lk.get("skill_id")
            if not isinstance(sid, str):
                continue
            if sid not in skills_by_id:
                # 该 skill id 在当前 run_dir 中不存在，跳过
                continue
            if sid not in entry.skill_ids:
                entry.skill_ids.append(sid)

    # 组装输出结构
    out_dir = run_dir / "afc"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "afc_skill_snapshot.json"

    # LLM refine：为每个 abstract_skill 默认运行一次 refine，填充 task_group/task_role/env_sensitivity
    for e in aggregated.values():
        _llm_refine_abstract_skill(e, skills_by_id)

    abstract_skills_payload: List[Dict[str, Any]] = []
    for key in sorted(aggregated.keys()):
        e = aggregated[key]
        abstract_skills_payload.append(
            {
                "abstract_skill_id": e.abstract_skill_id,
                "task_group": e.task_group,
                "task_role": e.task_role,
                "norm_label": e.norm_label,
                "action": e.action,
                "semantic_signature": {
                    "semantic_text": e.semantic_text,
                    "semantic_vector": e.semantic_vector,
                    "env_sensitivity": e.env_sensitivity,
                    "io_schema": e.io_schema,
                    "preconditions_abstract": e.preconditions_abstract,
                },
                "afc_controls": [{"control_id": cid} for cid in e.afc_controls],
                "concrete_skills": [{"skill_id": sid} for sid in e.skill_ids],
            }
        )

    out_obj: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "domain": domain,
        "abstract_skills": abstract_skills_payload,
    }

    out_path.write_text(
        json.dumps(out_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


__all__ = ["build_skill_snapshot"]
