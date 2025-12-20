"""
AFCdatabaseEvolve.update_case

本模块负责在“已有 SkillCase 的基础上”，结合一次或多次修复/执行结果，对 AFC 全局库中的
SkillCase 做精细更新（进化），核心围绕 Skill DNA 四元组：

    SkillCase = <S_invariant, A_template, R_history, theta_weights>

默认策略：
  - 优先使用提示词工程 + LLM 给出“建议更新”（prompt 在 AFCdatabaseEvolve/prompt 下）：
      * evolve_rating.md：补全 / 校正 (L_S, L_A, rebuild_grade)；
      * update_case_rating.md：对 theta_weights / 负样本标记等给出增量建议；
  - 然后用少量规则做数值裁剪 / 兜底，保证结果稳定可解释；
  - 调用方可以通过参数（如 use_llm=False / llm_mode）关闭或降级 LLM，退回纯规则路径。

本文件在上述设计基础上，提供了一个入口函数：

    def update_skill_case(
        case: Dict[str, Any],
        exec_result: Dict[str, Any],
        diff_info: Dict[str, Any],
        *,
        use_llm: bool = True,
    ) -> None:
        \"\"\"根据单次执行结果更新 SkillCase（就地修改 case）。\"\"\"

其实现遵循下方注释所述的步骤。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import json

from skill.llm_client import LLMConfig, complete_text
from AFCdatabaseBuild import global_db as build_global_db

JsonDict = Dict[str, Any]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """将数值限制在 [lo, hi] 区间。"""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _load_template_between_markers(md_path: Path, start: str, end: str) -> str:
    """从 md 文件中截取 start/end 标记之间的模板正文。"""
    if not md_path.is_file():
        return ""
    text = md_path.read_text(encoding="utf-8")
    i1, i2 = text.find(start), text.find(end)
    if i1 == -1 or i2 == -1 or i2 <= i1:
        return ""
    return text[i1 + len(start) : i2].strip()


def _load_evolve_rating_template() -> str:
    """加载 evolve_rating.md 中的评级 prompt 模板。"""
    here = Path(__file__).resolve().parent
    md_path = here / "prompt" / "evolve_rating.md"
    return _load_template_between_markers(
        md_path,
        "<!-- EVOLVE_RATING_PROMPT_BEGIN -->",
        "<!-- EVOLVE_RATING_PROMPT_END -->",
    )


def _load_update_case_rating_template() -> str:
    """加载 update_case_rating.md 中的 θ/负样本建议 prompt 模板。"""
    here = Path(__file__).resolve().parent
    md_path = here / "prompt" / "update_case_rating.md"
    return _load_template_between_markers(
        md_path,
        "<!-- UPDATE_CASE_RATING_PROMPT_BEGIN -->",
        "<!-- UPDATE_CASE_RATING_PROMPT_END -->",
    )


def _llm_call(template: str, context: JsonDict, *, max_tokens: int = 768) -> Optional[JsonDict]:
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


def _ensure_evolve_meta(case: JsonDict) -> JsonDict:
    """确保 case 中存在 evolve_meta 并返回之。"""
    em = case.get("evolve_meta")
    if not isinstance(em, dict):
        em = {}
        case["evolve_meta"] = em
    return em


def _update_r_history(case: JsonDict, exec_result: JsonDict) -> None:
    """更新 R_history.exec_success / exec_fail 计数。"""
    rh = case.get("R_history") or {}
    succ = int(rh.get("exec_success") or 0)
    fail = int(rh.get("exec_fail") or 0)
    exec_success = bool(exec_result.get("exec_success"))
    if exec_success:
        succ += 1
    else:
        fail += 1
    case["R_history"] = {"exec_success": succ, "exec_fail": fail}


def _ensure_levels_and_grade_with_llm(
    case: JsonDict,
    diff_info: JsonDict,
    *,
    use_llm: bool,
) -> None:
    """补全 / 校正 (L_S, L_A, rebuild_grade)，必要时调用 evolve_rating LLM。"""
    semantic = case.get("S_invariant") or {}
    L_S = diff_info.get("L_S")
    L_A = diff_info.get("L_A")
    grade = diff_info.get("rebuild_grade")

    need_llm = use_llm and (L_S is None or L_A is None or grade is None)
    if need_llm:
        template = _load_evolve_rating_template()
        if template:
            context = {
                "abstract_skill_id_old": None,
                "abstract_skill_id_new": None,
                "old_S": semantic,
                "new_S": semantic,
                "code_diff_summary": diff_info.get("code_diff_summary") or {},
                "precomputed_metrics": {
                    "sim_S": diff_info.get("sim_S"),
                    "reuse_A": diff_info.get("reuse_A"),
                },
            }
            suggestion = _llm_call(template, context, max_tokens=512)
            if isinstance(suggestion, dict):
                if L_S is None and isinstance(suggestion.get("L_S"), int):
                    L_S = suggestion["L_S"]
                if L_A is None and isinstance(suggestion.get("L_A"), int):
                    L_A = suggestion["L_A"]
                if grade is None and isinstance(suggestion.get("rebuild_grade"), int):
                    grade = suggestion["rebuild_grade"]

    # 规则兜底：确保数值在合法范围内
    if isinstance(L_S, int):
        L_S = max(0, min(2, L_S))
    else:
        L_S = None
    if isinstance(L_A, int):
        L_A = max(0, min(2, L_A))
    else:
        L_A = None
    if isinstance(grade, int):
        grade = max(0, min(4, grade))
    else:
        grade = None

    levels = None
    if L_S is not None and L_A is not None:
        levels = {"L_S": int(L_S), "L_A": int(L_A)}
    case["levels"] = levels
    if grade is not None:
        case["rebuild_grade"] = int(grade)


def _apply_theta_update(
    case: JsonDict,
    exec_result: JsonDict,
    diff_info: JsonDict,
    llm_theta_delta: Optional[JsonDict],
) -> None:
    """根据规则 + LLM 建议，对 theta_weights 做小幅更新。"""
    # 读取当前 θ，若不存在则初始化为默认值
    theta = case.get("theta_weights")
    if not isinstance(theta, dict):
        theta = build_global_db._init_theta_weights()  # type: ignore[attr-defined]

    # 1) 先应用 LLM 的 delta（若有）
    if isinstance(llm_theta_delta, dict):
        for key, delta in llm_theta_delta.items():
            try:
                dv = float(delta)
            except Exception:
                continue
            # 限制单次增量幅度
            dv = max(-0.5, min(0.5, dv))
            theta[key] = _clamp(float(theta.get(key, 1.0)) + dv)

    # 2) 再叠加规则微调（与 integrate_run 中的逻辑保持一致方向）
    exec_success = bool(exec_result.get("exec_success"))
    levels = case.get("levels") or {}
    L_S = levels.get("L_S")
    grade = case.get("rebuild_grade")

    alpha_success = 0.05
    alpha_fail = 0.1

    if exec_success:
        # 成功：整体略增强常用语义特征的权重
        for key in ["clean_text", "norm_label", "action", "role"]:
            theta[key] = _clamp(theta.get(key, 1.0) + alpha_success)
        # 若语义漂移较大但依然成功，说明 url_pattern / env 信息也很关键
        if isinstance(L_S, int) and L_S >= 1:
            for key in ["url_pattern", "env.login_state"]:
                theta[key] = _clamp(theta.get(key, 1.0) + alpha_success * 0.5)
    else:
        # 失败：整体稍微下降，表示现有特征组合不够可靠
        for key in ["clean_text", "norm_label", "action", "role", "url_pattern", "env.login_state"]:
            theta[key] = _clamp(theta.get(key, 1.0) - alpha_fail)

    if isinstance(grade, int) and grade <= 1:
        # 重建等级越低（接近 0），说明改动越大，可以适度拉高环境特征权重
        for key in ["url_pattern", "env.login_state"]:
            theta[key] = _clamp(theta.get(key, 1.0) + 0.05)

    case["theta_weights"] = dict(theta)


def update_skill_case(
    case: JsonDict,
    exec_result: JsonDict,
    diff_info: JsonDict,
    *,
    use_llm: bool = True,
) -> None:
    """根据单次执行结果更新 SkillCase（就地修改 case）。

    步骤概览：
      1. 更新 R_history.exec_success / exec_fail；
      2. 补全 / 校正 (L_S, L_A, rebuild_grade)，默认使用 evolve_rating.md；
      3. 构造 update_case_rating 的 context，调用 LLM 获取 theta_delta / flags 建议；
      4. 结合规则更新 theta_weights / levels / grade / 负样本标记；
      5. 在 evolve_meta.last_exec 中记录此次执行的摘要，便于调试 / 可视化。
    """
    # 1) 更新 R_history
    _update_r_history(case, exec_result)

    # 2) 补全 / 校正 levels 与 rebuild_grade
    _ensure_levels_and_grade_with_llm(case, diff_info, use_llm=use_llm)
    levels = case.get("levels") or {}
    L_S = levels.get("L_S")
    L_A = levels.get("L_A")
    grade = case.get("rebuild_grade")

    # 3) 调用 update_case_rating.md 获取 LLM 建议（theta_delta / flags）
    llm_theta_delta: Optional[JsonDict] = None
    flags: JsonDict = {}
    if use_llm:
        template = _load_update_case_rating_template()
        if template:
            context = {
                "skill_case": {
                    "S_invariant": case.get("S_invariant") or {},
                    "theta_weights": case.get("theta_weights") or {},
                    "R_history": case.get("R_history") or {},
                    "levels": case.get("levels"),
                    "rebuild_grade": case.get("rebuild_grade"),
                },
                "exec_result": exec_result,
                "diff_info": diff_info,
            }
            suggestion = _llm_call(template, context, max_tokens=768)
            if isinstance(suggestion, dict):
                td = suggestion.get("theta_delta")
                if isinstance(td, dict):
                    llm_theta_delta = td
                ov_levels = suggestion.get("override_levels")
                if isinstance(ov_levels, dict):
                    # 若 LLM 明确建议重写 levels，则采用（在合法范围内）
                    L_S2 = ov_levels.get("L_S")
                    L_A2 = ov_levels.get("L_A")
                    if isinstance(L_S2, int) and isinstance(L_A2, int):
                        L_S = max(0, min(2, L_S2))
                        L_A = max(0, min(2, L_A2))
                        case["levels"] = {"L_S": int(L_S), "L_A": int(L_A)}
                ov_grade = suggestion.get("override_rebuild_grade")
                if isinstance(ov_grade, int):
                    grade2 = max(0, min(4, ov_grade))
                    grade = grade2
                    case["rebuild_grade"] = int(grade)
                fl = suggestion.get("flags")
                if isinstance(fl, dict):
                    flags = fl

    # 4) 根据 LLM 建议 + 规则更新 theta_weights
    _apply_theta_update(case, exec_result, diff_info, llm_theta_delta)

    # 负样本 / 过时标记
    em = _ensure_evolve_meta(case)
    # negative_samples 列表
    if flags.get("mark_negative_sample"):
        neg_list = em.get("negative_samples")
        if not isinstance(neg_list, list):
            neg_list = []
        neg_list.append(
            {
                "run_dir": exec_result.get("run_dir"),
                "afc_control_id": exec_result.get("afc_control_id"),
                "skill_id": exec_result.get("skill_id"),
                "timestamp": exec_result.get("timestamp"),
                "error_type": exec_result.get("error_type"),
            }
        )
        em["negative_samples"] = neg_list
    # flags
    flg = em.get("flags")
    if not isinstance(flg, dict):
        flg = {}
    if flags.get("mark_maybe_obsolete"):
        flg["maybe_obsolete"] = True
    em["flags"] = flg

    # 5) 记录最近一次执行摘要
    em["last_exec"] = {
        "last_exec_success": bool(exec_result.get("exec_success")),
        "sim_S": diff_info.get("sim_S"),
        "reuse_A": diff_info.get("reuse_A"),
        "L_S": L_S,
        "L_A": L_A,
        "rebuild_grade": grade,
        "error_type": exec_result.get("error_type"),
        "timestamp": exec_result.get("timestamp"),
    }


__all__ = ["update_skill_case"]

