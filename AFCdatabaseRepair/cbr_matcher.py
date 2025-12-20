"""
AFCdatabaseRepair.cbr_matcher

基于 SkillCase 的 S_invariant + theta_weights 对新页面上的 AfcControl 做 CBR 风格匹配。

目标：
  - 已知某个抽象技能在旧版本页面上的一个成功 SkillCase（包含 S_invariant / theta_weights）；
  - 在新版本页面的 AfcPageSnapshot 中，为该抽象技能找到最可能对应的控件候选；
  - 提供一个简单、可解释的相似度计算结果，供 Repair 阶段后续调用 LLM 或规则做 selector / 代码适配。

核心接口：

    find_candidate_controls(
        skill_case: Dict[str, Any],
        page_snapshot: Dict[str, Any],
        top_k: int = 3,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:

返回值为候选列表，按 score 从高到低排序，每个元素形如：

    {
      "control_id": str,
      "score": float,                # 0.0–1.0 之间的相似度（加权和后归一化）
      "feature_scores": {...},       # 每个特征维度的局部相似度，便于诊断
      "control": {...AfcControl...}  # 原始 AfcControl JSON
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import math

from AFCdatabaseBuild import global_db as build_global_db  # type: ignore[import]


JsonDict = Dict[str, Any]


def _norm_str(s: Any) -> str:
    """简单字符串归一化：转小写并去掉两端空白。"""
    if s is None:
        return ""
    return str(s).strip().lower()


def _jaccard(tokens_a: List[str], tokens_b: List[str]) -> float:
    """计算两个 token 列表的 Jaccard 相似度，返回 0.0–1.0。"""
    set_a = {t for t in (_norm_str(x) for x in tokens_a) if t}
    set_b = {t for t in (_norm_str(x) for x in tokens_b) if t}
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / float(len(union)) if union else 0.0


def _bool_eq(a: Any, b: Any) -> float:
    """布尔相等相似度：相等返回 1.0，否则 0.0（None 视为未知，返回 0.0）。"""
    if a is None or b is None:
        return 0.0
    return 1.0 if a == b else 0.0


def _url_pattern_sim(p1: Any, p2: Any) -> float:
    """URL 模式相似度的简单启发式：相等→1.0，前缀/后缀重合→0.5，否则 0.0。"""
    s1 = _norm_str(p1)
    s2 = _norm_str(p2)
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0
    if s1.startswith(s2) or s2.startswith(s1):
        return 0.5
    return 0.0


def compute_control_similarity(skill_case: JsonDict, control: JsonDict) -> Tuple[float, JsonDict]:
    """针对单个 SkillCase 与单个 AfcControl 计算相似度。

    返回：
      - score: float，0.0–1.0 之间的相似度（按 theta_weights 加权并简单归一化）；
      - feature_scores: dict，每个特征维度的局部相似度，便于诊断。
    """
    S_ref = skill_case.get("S_invariant") or {}
    if not isinstance(S_ref, dict):
        S_ref = {}

    # 新控件的 S_invariant 通过 Build 侧逻辑构造，保证字段对齐
    S_new = build_global_db._build_s_invariant(control)  # type: ignore[attr-defined]

    theta = skill_case.get("theta_weights")
    if not isinstance(theta, dict):
        theta = build_global_db._init_theta_weights()  # type: ignore[attr-defined]

    # 各维度局部相似度
    sim_clean = _jaccard(S_ref.get("clean_text") or [], S_new.get("clean_text") or [])
    sim_norm = _bool_eq(S_ref.get("norm_label"), S_new.get("norm_label"))
    sim_action = _bool_eq(S_ref.get("action"), S_new.get("action"))
    sim_role = _jaccard(S_ref.get("role") or [], S_new.get("role") or [])
    sim_url = _url_pattern_sim(S_ref.get("url_pattern"), S_new.get("url_pattern"))

    env_ref = S_ref.get("env") or {}
    env_new = S_new.get("env") or {}
    sim_env_login = _bool_eq(env_ref.get("login_state"), env_new.get("login_state"))

    feature_scores: JsonDict = {
        "clean_text": sim_clean,
        "norm_label": sim_norm,
        "action": sim_action,
        "role": sim_role,
        "url_pattern": sim_url,
        "env.login_state": sim_env_login,
    }

    # 加权求和 + 归一化（总权重为 0 时返回 0.0）
    total_weight = 0.0
    weighted_sum = 0.0
    for key, sim_val in feature_scores.items():
        w = float(theta.get(key, 0.0) or 0.0)
        if w <= 0.0:
            continue
        total_weight += w
        weighted_sum += w * float(sim_val)

    if total_weight <= 0.0:
        score = 0.0
    else:
        score = weighted_sum / total_weight

    # 确保 score 在 [0,1] 内
    score = max(0.0, min(1.0, score))
    return score, feature_scores


def find_candidate_controls(
    skill_case: JsonDict,
    page_snapshot: JsonDict,
    *,
    top_k: int = 3,
    min_score: float = 0.0,
) -> List[JsonDict]:
    """在给定 AfcPageSnapshot 中，为某个 SkillCase 找到 top-k 候选控件。

    参数：
      - skill_case:
          - 来自全局 AFC 库的单条 SkillCase，必须包含 S_invariant；
      - page_snapshot:
          - run_dir/afc/afc_page_snapshot.json 解析后的 dict，内部包含 "controls" 列表；
      - top_k:
          - 返回的候选个数上限（按 score 从高到低排序）；
      - min_score:
          - 过滤阈值，score < min_score 的候选将被丢弃。

    返回：
      - 每个元素形如：
        {
          "control_id": str,
          "score": float,
          "feature_scores": { ... },
          "control": { ...AfcControl... }
        }
    """
    controls = page_snapshot.get("controls") or []
    candidates: List[JsonDict] = []

    for ctl in controls:
        if not isinstance(ctl, dict):
            continue
        cid = ctl.get("control_id")
        if not isinstance(cid, str):
            continue
        try:
            score, feature_scores = compute_control_similarity(skill_case, ctl)
        except Exception:
            continue
        if score < float(min_score):
            continue
        candidates.append(
            {
                "control_id": cid,
                "score": score,
                "feature_scores": feature_scores,
                "control": ctl,
            }
        )

    # 按 score 从高到低排序并截断
    candidates.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    if top_k > 0:
        candidates = candidates[:top_k]
    return candidates


__all__ = ["compute_control_similarity", "find_candidate_controls"]

