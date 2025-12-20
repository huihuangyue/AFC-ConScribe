"""
AFCdatabaseRepair.code_adapter

根据旧技能 + 新页面候选控件 + SkillCase 信息，生成“适配新页面”的 Python 代码建议。

设计目标：
  - Repair 层不直接操作 LLM 细节，而是调用本模块：

      propose_repaired_skill(
          old_skill_path=Path(...),
          candidate_control={...来自 AfcPageSnapshot.controls[*]...},
          skill_case={...来自全局 AFC 库的 SkillCase...},
          run_dir_new=Path(...),
          use_llm=True,
      )

    返回一个结构化的修复建议：

      {
        "skill_id": str,           # 新技能 id（默认沿用旧 id）
        "program_path": Path,      # 建议写入的 skill JSON 路径（默认沿用旧路径）
        "code": str,               # 建议的新 Python 代码
        "notes": str,              # 对本次修复的简短说明
        "skill_json": dict,        # 更新 locators/preconditions 后的 skill JSON（不落盘）
      }

  - 默认使用 prompt/repair_code.md 进行提示词工程，让 LLM 参考：
      * 旧技能 JSON（locators / preconditions / program.code 摘要）；
      * 新页面候选控件的 AfcControl 描述；
      * SkillCase 的 S_invariant / theta_weights；
    生成新的 program_code 与 notes。
  - 如 LLM 调用失败或 use_llm=False，则退回“仅更新 locators”的规则修复：保持
    旧程序不变，但用 candidate_control 的 selector 替换 skill.locators.selector。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import json

from skill.llm_client import LLMConfig, complete_text


JsonDict = Dict[str, Any]


def _load_json(path: Path) -> JsonDict:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"Skill JSON at {path} is not a JSON object")
    return obj


def _write_json(path: Path, obj: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_template_between_markers(md_path: Path, start: str, end: str) -> str:
    if not md_path.is_file():
        return ""
    text = md_path.read_text(encoding="utf-8")
    i1, i2 = text.find(start), text.find(end)
    if i1 == -1 or i2 == -1 or i2 <= i1:
        return ""
    return text[i1 + len(start) : i2].strip()


def _load_repair_code_template() -> str:
    """从 prompt/repair_code.md 中加载 LLM 提示词模板。"""
    here = Path(__file__).resolve().parent
    md_path = here / "prompt" / "repair_code.md"
    return _load_template_between_markers(
        md_path, "<!-- REPAIR_CODE_PROMPT_BEGIN -->", "<!-- REPAIR_CODE_PROMPT_END -->"
    )


def _llm_call(template: str, context: JsonDict, *, max_tokens: int = 2048) -> Optional[JsonDict]:
    """通用 LLM 调用封装：template + CONTEXT_JSON → JSON dict 或 None。"""
    if not template:
        return None
    prompt = template.replace("{{CONTEXT_JSON}}", json.dumps(context, ensure_ascii=False, indent=2))
    try:
        cfg = LLMConfig()
        resp = complete_text(prompt, config=cfg, temperature=0.0, max_tokens=max_tokens, verbose=False)
        data = json.loads(resp)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _extract_old_program_summary(skill: JsonDict, max_chars: int = 4000) -> JsonDict:
    """从旧技能 JSON 中抽取用于 prompt 的精简 program 信息。"""
    prog = skill.get("program") or {}
    code = prog.get("code") or ""
    if not isinstance(code, str):
        code = str(code)
    code_snippet = code[:max_chars]
    return {
        "language": prog.get("language") or "python",
        "entry": prog.get("entry"),
        "code_snippet": code_snippet,
    }


def _update_locators_from_control(skill: JsonDict, candidate_control: JsonDict) -> None:
    """根据 AfcControl.structural_signature 更新 skill.locators.selector / selector_alt。"""
    locs = skill.get("locators") or {}
    struct_sig = candidate_control.get("structural_signature") or {}
    selector_candidates = struct_sig.get("selector_candidates") or []
    sel_primary = None
    if isinstance(selector_candidates, list) and selector_candidates:
        for s in selector_candidates:
            if isinstance(s, str) and s.strip():
                sel_primary = s.strip()
                break
    # 仅在新 selector 有意义时才覆盖
    if sel_primary:
        locs["selector"] = sel_primary
        # 若没有 selector_alt，则用 selector_candidates 作为备选
        if not locs.get("selector_alt") and isinstance(selector_candidates, list):
            locs["selector_alt"] = [s for s in selector_candidates if isinstance(s, str) and s.strip()]
    skill["locators"] = locs


@dataclass
class RepairProposal:
    """一个“建议的修复结果”的结构化表示。"""

    skill_id: str
    program_path: Path
    code: str
    notes: str
    skill_json: JsonDict

    def to_dict(self) -> JsonDict:
        return {
            "skill_id": self.skill_id,
            "program_path": str(self.program_path),
            "code": self.code,
            "notes": self.notes,
            "skill_json": self.skill_json,
        }


def propose_repaired_skill(
    old_skill_path: Path,
    candidate_control: JsonDict,
    skill_case: JsonDict,
    *,
    run_dir_new: Optional[Path] = None,
    use_llm: bool = True,
) -> RepairProposal:
    """基于旧技能 + 新控件 + SkillCase 生成“修复建议”。

    注意：
      - 本函数只在内存中构造新的 skill_json 和 program.code，不写回磁盘；
      - program_path 默认沿用 old_skill_path，调用方可根据需要决定是否另存为新文件；
      - 若 use_llm=False 或 LLM 调用失败，则只进行 locators 更新，不改动原有代码。
    """
    old_skill_path = Path(old_skill_path)
    skill = _load_json(old_skill_path)

    # 基础标识
    skill_id = str(skill.get("id") or "")
    if not skill_id:
        # 兜底：从文件名推一个 id
        skill_id = old_skill_path.stem
        skill["id"] = skill_id

    # 默认 program_path：沿用旧路径
    program_path = old_skill_path
    if run_dir_new is not None:
        # 可选：若传入新的 run_dir，则建议将修复后的技能写在该 run_dir/skill 下
        # 具体命名策略留给调用方，这里只保留用于参考的路径，不自动创建。
        program_path = Path(run_dir_new) / "skill" / old_skill_path.name

    # 先根据 candidate_control 更新 locators（无论是否用 LLM）
    _update_locators_from_control(skill, candidate_control)

    # 默认代码与说明：沿用旧代码，并记录“仅更新 locators”的说明
    prog = skill.get("program") or {}
    old_code = prog.get("code") or ""
    if not isinstance(old_code, str):
        old_code = str(old_code)
    new_code = old_code
    notes = "locator updated from candidate_control; program code unchanged (LLM disabled or not used)."

    if use_llm:
        template = _load_repair_code_template()
        if template:
            context: JsonDict = {
                "old_skill": {
                    "id": skill_id,
                    "domain": skill.get("domain"),
                    "locators": skill.get("locators"),
                    "preconditions": skill.get("preconditions"),
                    "program": _extract_old_program_summary(skill),
                },
                "candidate_control": candidate_control,
                "skill_case": {
                    "S_invariant": skill_case.get("S_invariant") or {},
                    "theta_weights": skill_case.get("theta_weights") or {},
                    "R_history": skill_case.get("R_history") or {},
                    "levels": skill_case.get("levels"),
                    "rebuild_grade": skill_case.get("rebuild_grade"),
                },
            }
            suggestion = _llm_call(template, context, max_tokens=4096)
            if isinstance(suggestion, dict):
                code_s = suggestion.get("program_code")
                notes_s = suggestion.get("notes")
                if isinstance(code_s, str) and code_s.strip():
                    new_code = code_s.strip()
                if isinstance(notes_s, str) and notes_s.strip():
                    notes = notes_s.strip()

    # 将新代码写入 skill_json（仅在内存中）
    prog = skill.get("program") or {}
    prog["language"] = prog.get("language") or "python"
    prog["code"] = new_code
    skill["program"] = prog

    return RepairProposal(
        skill_id=skill_id,
        program_path=program_path,
        code=new_code,
        notes=notes,
        skill_json=skill,
    )


__all__ = ["RepairProposal", "propose_repaired_skill"]

