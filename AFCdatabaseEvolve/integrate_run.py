"""
AFCdatabaseEvolve.integrate_run

本模块负责“在已有全局 AFC 库的基础上，引入新的 run_dir 之后如何进化该库”的流程设计。

重要前提与分工说明：

1. **初始全局库的构建（冷启动）**
   - 当还没有任何全局库时（例如 abstract_skills_global.jsonl 不存在），
     由 `AFCdatabaseBuild/global_db.py` 负责：
       - 从单个或多个 run_dir 的 `afc_page_snapshot.json` / `afc_skill_snapshot.json` 出发，
         构建一个“初始版”的全局 AFC 库；
       - 为每个 SkillCase 初始化一份默认的 `theta_weights`（可选用 LLM 进行全局聚合）。
   - 这一步通常是离线批处理，可以清空旧库后重新生成。

2. **已有全局库基础上的“进化”**
   - 当全局库已经存在时（例如有了初始的 abstract_skills_global.jsonl），
     **本模块才登场**，它的职责是：
       - 在不清空全局库的前提下，引入新的 run_dir；
       - 更新现有 SkillCase 的 `R_history`（执行成功/失败统计）与 `theta_weights`；
       - 若有需要，还可以为新出现的 abstract_skill_id 创建新的条目，但不会重置已有数据。

因此：
  - `AFCdatabaseBuild` 关心的是“如何从 0 → 1 搭起一个全局 AFC 库，并给出合理的初始权重”；  
  - `AFCdatabaseEvolve` 关心的是“在 1 → N 的过程中，如何利用新 run_dir 和执行结果，让库越来越聪明”。

当前文件的目标：
  - 先以文档形式清晰描述 `integrate_run_with_evolution(...)` 这类函数应该做什么、使用什么手段；
  - 待你确认设计之后，再按照这里的注释逐步补齐实际实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json

from AFCdatabaseEvolve.loader import GlobalDb, JsonDict, load_global_db, save_global_db
from AFCdatabaseEvolve.update_case import update_skill_case
from AFCdatabaseBuild import global_db as build_global_db


def _read_json(path: Path) -> JsonDict:
    """读取 JSON 文件为 dict。"""
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"JSON at {path} is not an object")
    return obj


def _ensure_global_entry(db: GlobalDb, abstract_entry: JsonDict) -> JsonDict:
    """复用 AFCdatabaseBuild.global_db._ensure_global_entry 的逻辑，确保全局库中存在该 abstract_skill."""
    # 这里直接调用 Build 侧的内部实现，保证结构完全一致（afc_controls / concrete_skills / skill_cases 字段）。
    return build_global_db._ensure_global_entry(db, abstract_entry)  # type: ignore[attr-defined]


def _ensure_afc_refs(
    global_entry: JsonDict,
    *,
    domain: str,
    run_dir_str: str,
    afc_control_id: Optional[str],
    skill_id: Optional[str],
) -> None:
    """在 global_entry.afc_controls / concrete_skills 中补充本次 run_dir 的引用（若缺失）。"""
    # afc_controls
    if afc_control_id:
        controls = global_entry.get("afc_controls") or []
        key_set = {(c.get("domain"), c.get("run_dir"), c.get("control_id")) for c in controls}
        key = (domain, run_dir_str, afc_control_id)
        if key not in key_set:
            controls.append({"domain": domain, "run_dir": run_dir_str, "control_id": afc_control_id})
        global_entry["afc_controls"] = controls

    # concrete_skills
    if skill_id:
        skills = global_entry.get("concrete_skills") or []
        key_set = {(s.get("domain"), s.get("run_dir"), s.get("skill_id")) for s in skills}
        key = (domain, run_dir_str, skill_id)
        if key not in key_set:
            skills.append({"domain": domain, "run_dir": run_dir_str, "skill_id": skill_id})
        global_entry["concrete_skills"] = skills


def _ensure_skill_case(
    global_entry: JsonDict,
    *,
    domain: str,
    run_dir_str: str,
    afc_control_id: str,
    skill_id: Optional[str],
    S_new: Optional[JsonDict],
) -> Tuple[JsonDict, Optional[JsonDict]]:
    """在 global_entry.skill_cases 中找到 / 创建指定 (run_dir, control_id, skill_id) 的 SkillCase。

    返回 (case, old_S)，其中 old_S 为更新前的 S_invariant（若为新建 case 则为 None）。
    """
    cases: List[JsonDict] = global_entry.get("skill_cases") or []
    skill_id_str = skill_id or ""

    # 先尝试定位已有 case
    for case in cases:
        if (
            str(case.get("run_dir") or "") == run_dir_str
            and str(case.get("afc_control_id") or "") == afc_control_id
            and str(case.get("skill_id") or "") == skill_id_str
        ):
            old_S = case.get("S_invariant") if isinstance(case.get("S_invariant"), dict) else None
            if isinstance(S_new, dict):
                case["S_invariant"] = S_new
            global_entry["skill_cases"] = cases
            return case, old_S

    # 若不存在，则新建一个基础 SkillCase
    # theta_weights：优先复用同一 abstract_skill 下现有 case 的权重，否则使用默认初始化
    theta_ref = None
    for c in cases:
        tw = c.get("theta_weights")
        if isinstance(tw, dict):
            theta_ref = dict(tw)
            break
    if theta_ref is None:
        theta_ref = build_global_db._init_theta_weights()  # type: ignore[attr-defined]

    case: JsonDict = {
        "run_dir": run_dir_str,
        "domain": domain,
        "afc_control_id": afc_control_id,
        "skill_id": skill_id_str or None,
        "S_invariant": S_new,
        "A_template": {
            "program_entry": None,
            "args_schema": None,
        },
        "R_history": {
            "exec_success": 0,
            "exec_fail": 0,
        },
        "theta_weights": theta_ref,
        "levels": None,
        "rebuild_grade": None,
    }
    cases.append(case)
    global_entry["skill_cases"] = cases
    return case, None


def _compress_skill_cases(global_entry: JsonDict, *, max_cases: int = 3) -> None:
    """对单个 abstract_skill_entry 的 skill_cases 做容量压缩，保留至多 max_cases 条代表样本。

    代表性评分规则（越高越优先保留）：
      - score = 2 * exec_success - exec_fail
      - 若得分相同，成功+失败总次数更多者优先（说明被更多次使用/观察）
    """
    cases: List[JsonDict] = global_entry.get("skill_cases") or []
    if len(cases) <= max_cases:
        return

    def _score(c: JsonDict) -> Tuple[int, int]:
        rh = c.get("R_history") or {}
        succ = int(rh.get("exec_success") or 0)
        fail = int(rh.get("exec_fail") or 0)
        total = succ + fail
        return 2 * succ - fail, total

    sorted_cases = sorted(cases, key=_score, reverse=True)
    global_entry["skill_cases"] = sorted_cases[:max_cases]


def integrate_run_with_evolution(
    global_db_path: Path,
    run_dir: Path,
    *,
    exec_log: Optional[JsonDict] = None,
    use_llm_rating: bool = False,
) -> None:
    """
    在“已有全局库”的前提下，引入一个新的 run_dir，对 AFC 库做一次“进化更新”。

    参数设计（说明阶段，不立即实现）：
      - global_db_path:
          - 现有全局 AFC 库文件的路径，例如：
              workspace/AFCdatabase/db/abstract_skills_global.jsonl
          - 函数会从该路径加载库，并在更新后覆盖写回。

      - run_dir:
          - 新增的 Detect+Skill 产物目录，例如：
              workspace/data/ctrip_com/20251216193438
          - 假定其中已经存在：
              afc/afc_page_snapshot.json
              afc/afc_skill_snapshot.json
          - 如不存在，可在实现中优先调用 AFCdatabaseBuild.afc_page_snapshot / skill_snapshot 先生成。

      - exec_log（可选）:
          - 用于携带“这个 run_dir 上技能真实执行/修复的结果”，例如：
            {
              "skill_cases": [
                {
                  "abstract_skill_id": "...",
                  "run_dir": "...",
                  "afc_control_id": "...",
                  "skill_id": "...",
                  "sim_S": 0.63,
                  "reuse_A": 0.55,
                  "L_S": 1,
                  "L_A": 1,
                  "rebuild_grade": 2,
                  "exec_success": true,
                  "notes": "selector updated, program reused."
                },
                ...
              ]
            }
          - 这些信息通常由 Repair/AID 模块在一次修复/执行后产出，
            是“进化阶段”更新权重和历史的主要依据。

      - use_llm_rating:
          - 若为 True，则在必要时可以调用 LLM（例如使用 AFCdatabaseEvolve/prompt/evolve_rating.md）
            对某些 case 的 (L_S, L_A, rebuild_grade) 做辅助判断；
          - 默认 False，即仅依赖程序化指标更新库。

    预期的整体流程（设计思路）：

    1. 加载全局库
       - 使用 AFCdatabaseEvolve.loader.load_global_db(global_db_path) 读取当前库；
       - 得到一个 GlobalDb(rows, index)，其中 index 是 abstract_skill_id -> entry。

    2. 将新 run_dir 作为“证据”而不是“完整纳入库”
       - 与 AFCdatabaseBuild 的职责不同，在“进化阶段”，我们不希望简单把
         新 run_dir 的所有控件/SkillCase 一股脑塞进全局库（那样库会无限膨胀）。
       - 推荐做法：
         1) 单独读取新 run_dir 的 `afc_page_snapshot.json` / `afc_skill_snapshot.json`，
            视为一批新的观测样本（observations），**只在内存使用，不直接写入库**；
         2) 对于每个 `abstract_skill_id`：
              - 找到全局库中对应 entry；如果不存在，**允许显式创建一个新的 abstract_skill
                条目**（冷门/新功能可以在演化阶段被“发现”并加入库中）；
              - 将 run_dir 中该 abstract_skill 的 S_invariant 与已有 SkillCase 做比对，
                计算相似度 / 漂移类型；
              - 用这些观测样本来“吸收信息”：更新该 entry 的统计量与权重，而不是简单追加
                新 SkillCase 记录。
       - 换句话说：新 run_dir 更像是一批“训练样本”，用来调整已有抽象技能的参数，
         而不是必须永久保存在库中的完整实例。

    3. 基于 exec_log / diff 信息更新 SkillCase（演化核心）
       - 若 exec_log 提供了具体的 skill_cases 列表，流程可为：
         1) 针对每条 case_update：
            - 定位到全局库中的对应 SkillCase：
              - 通过 abstract_skill_id + run_dir + afc_control_id + skill_id 四元组定位；
            - 将 exec_log 中的字段写入该 SkillCase，例如：
              - 更新 R_history.exec_success / exec_fail（+1）；
              - 写入 sim_S / reuse_A / L_S / L_A / rebuild_grade 等指标；
         2) 权重更新策略（theta_weights）：
            - 基础规则（无需 LLM）：
              - 若 exec_success=True 且 rebuild_grade 较小（重度修复），
                则对在本次匹配中贡献较大的特征（如 norm_label/clean_text）适度增权；
              - 若 exec_success=False 或 rebuild_grade 很低（接近重构），
                则对本次匹配中表现不稳定的特征适度降权；
              - 更新可用简单加减实现，例如：
                theta_new = theta_old + α * sign(贡献) ，并限制在 [0,1]。
            - 可选 LLM 辅助（use_llm_rating=True 时）：
              - 构造一个包含 old/new S_invariant、代码 diff 摘要、预计算的 sim_S/reuse_A
                的 context JSON；
              - 使用 `AFCdatabaseBuild/prompt/LLM_evolve_rating.md`，请 LLM 给出
                (L_S, L_A, rebuild_grade) 的建议与文字解释；
              - 将 LLM 的建议与程序计算结果做简单融合（例如只在边界样本上采用 LLM
                的判断，或对程序打分做小幅修正）。

    4. （可选）全局统计/压缩（真正的“吸收”）
       - 为避免全局库无限膨胀，可以在演化阶段对每个 abstract_skill_id 做有限容量的
         表示。本项目中约定：
         - **每个 abstract_skill_id 最多保留 3 条“代表性” SkillCase**；
           - 新样本进来时，如果已有 < 3 条，可以考虑插入一条新的代表样本；
           - 若已满 3 条，则要通过一定策略（例如“代表性评分”或“最近使用”）决定：
             替换其中一条旧样本，或者只用该样本更新统计量/权重而不新增记录。
       - 代表性与压缩的具体策略可以包括：
         - 仅保留最近或最常用的 SkillCase，或按某种“代表性评分”选择；
         - 对高相似的 SkillCase 进行合并，将它们的统计量（成功/失败次数、权重更新）
           聚合到同一个记录里；
         - 对明显过时的样本（长时间未被访问、权重极低）进行淘汰。
       - 这样做的目标是：让新 run_dir 的信息“吸收到权重与代表性样本中”，而不是简单
         将所有 run_dir 一直累加到库里。

    5. 保存更新后的全局库
       - 使用 AFCdatabaseBuild.global_db.save_global_db(db, global_db_path) 覆盖写回；
       - 保证后续 Planner / Repair / Browser 使用的是最新的 AFC 库。

    实现注意事项：
      - 本函数不应在任何情况下清空全局库；若需要完全重建，应当回到 AFCdatabaseBuild 进行；
      - 权重更新要尽量保持可解释性（例如在 SkillCase 中记录每次更新的理由或来源）；
      - LLM 调用要可配置（use_llm_rating 开关 + 温度/模型参数），避免对实验结果造成不可控影响；
      - 所有对 theta_weights 的更新建议限制在 [0.0, 1.0] 范围，并可加入衰减/正则化，防止数值爆炸。

    当前实现状态：
      - 上述步骤已经在本函数下方的代码中落地实现，包括：
        * 读取全局库与 run_dir 下的 AfcPage/AfcSkill 快照；
        * 按 exec_log.skill_cases 更新 SkillCase 的 R_history / levels / rebuild_grade；
        * 对 abstract_skill 级别的 theta_weights 做小幅增减；
        * 对每个 abstract_skill 压缩 skill_cases（最多保留 max_cases=3 条代表样本）；
        * 将更新后的 GlobalDb 写回 JSONL 文件。
      - 若后续需要修改演化策略（例如更复杂的权重更新或压缩规则），建议优先修改本 docstring
        中的设计说明，再同步调整下面的实现，以保持“注释 = 设计”的一致性。
    """

    # 1. 读取全局库
    db: GlobalDb = load_global_db(global_db_path)

    # 2. 读取新 run_dir 的 AFC 快照（仅作为“观测样本”）
    run_dir = Path(run_dir)
    page_path = run_dir / "afc" / "afc_page_snapshot.json"
    skill_path = run_dir / "afc" / "afc_skill_snapshot.json"
    if not page_path.is_file():
        raise FileNotFoundError(f"AfcPageSnapshot not found for evolution: {page_path}")
    if not skill_path.is_file():
        raise FileNotFoundError(f"AfcSkillSnapshot not found for evolution: {skill_path}")

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
    abstract_by_id: Dict[str, JsonDict] = {}
    for abstract_entry in abstract_skills:
        if not isinstance(abstract_entry, dict):
            continue
        aid = abstract_entry.get("abstract_skill_id")
        if isinstance(aid, str):
            abstract_by_id[aid] = abstract_entry

    # 3. 基于 exec_log 更新 SkillCase 与权重
    skill_cases_updates: List[JsonDict] = []
    if exec_log and isinstance(exec_log.get("skill_cases"), list):
        for item in exec_log.get("skill_cases") or []:
            if isinstance(item, dict):
                skill_cases_updates.append(item)

    run_dir_str_default = str(run_dir)

    for upd in skill_cases_updates:
        abstract_skill_id = upd.get("abstract_skill_id")
        if not isinstance(abstract_skill_id, str):
            continue

        # 找到 / 创建 abstract_skill 对应的全局 entry
        abstract_entry = abstract_by_id.get(abstract_skill_id)
        if abstract_entry is None:
            # 若当前 run_dir 的 afc_skill_snapshot 中没有该 id，则构造一个最简条目；
            # 主要用于兼容日志中略超前的场景。
            abstract_entry = {"abstract_skill_id": abstract_skill_id}
        global_entry = _ensure_global_entry(db, abstract_entry)

        # 解析本次观测的 run_dir / control / skill
        run_dir_str = str(upd.get("run_dir") or run_dir_str_default)
        afc_control_id = str(upd.get("afc_control_id") or "")
        skill_id = upd.get("skill_id")
        skill_id_str = str(skill_id) if skill_id is not None else None
        if not afc_control_id:
            # 没有 control_id 的记录无法映射到具体控件，暂时跳过
            continue

        # 在 afc_controls / concrete_skills 中登记引用
        _ensure_afc_refs(
            global_entry,
            domain=domain,
            run_dir_str=run_dir_str,
            afc_control_id=afc_control_id,
            skill_id=skill_id_str,
        )

        # 计算本次观测对应的 S_invariant
        control = controls_by_id.get(afc_control_id)
        S_new: Optional[JsonDict]
        if isinstance(control, dict):
            # 复用 Build 侧的 S_invariant 构造逻辑，保持字段一致
            S_new = build_global_db._build_s_invariant(control)  # type: ignore[attr-defined]
        else:
            S_new = None

        # 找到 / 创建具体的 SkillCase
        case, old_S = _ensure_skill_case(
            global_entry,
            domain=domain,
            run_dir_str=run_dir_str,
            afc_control_id=afc_control_id,
            skill_id=skill_id_str,
            S_new=S_new,
        )

        # 构造 exec_result / diff_info，并委托 update_case.update_skill_case 做精细更新
        upd = dict(upd)  # 避免在原始 exec_log 上产生副作用
        exec_result: JsonDict = {
            "exec_success": bool(upd.get("exec_success")),
            "error_type": upd.get("error_type"),
            "timestamp": upd.get("timestamp"),
            "run_dir": run_dir_str,
            "afc_control_id": afc_control_id,
            "skill_id": skill_id_str,
        }
        diff_info: JsonDict = {
            "sim_S": upd.get("sim_S"),
            "reuse_A": upd.get("reuse_A"),
            "L_S": upd.get("L_S"),
            "L_A": upd.get("L_A"),
            "rebuild_grade": upd.get("rebuild_grade"),
            "drift_E": upd.get("drift_E"),
            "notes": upd.get("notes"),
            "code_diff_summary": upd.get("code_diff_summary"),
            "precomputed_metrics": upd.get("precomputed_metrics"),
        }

        update_skill_case(
            case,
            exec_result=exec_result,
            diff_info=diff_info,
            use_llm=use_llm_rating,
        )

        # 将该 SkillCase 更新后的 theta_weights 同步到同一 abstract_skill 下的其他 SkillCase，
        # 保持“抽象技能级别的 θ”这一近似。
        theta = case.get("theta_weights")
        if isinstance(theta, dict):
            for c2 in global_entry.get("skill_cases") or []:
                c2["theta_weights"] = dict(theta)

    # 4. 对所有 abstract_skill_entry 做一次容量压缩：每个最多保留 3 个代表性 SkillCase
    for entry in db.rows:
        if not isinstance(entry, dict):
            continue
        _compress_skill_cases(entry, max_cases=3)

    # 5. 写回全局库
    save_global_db(db, global_db_path)


__all__ = ["integrate_run_with_evolution"]
