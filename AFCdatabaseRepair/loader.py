"""
AFCdatabaseRepair.loader

为 Repair 层提供统一的“加载入口”：
  - 读取全局 AFC 库（复用 AFCdatabaseEvolve.loader 的实现）；
  - 读取单个 run_dir 下的 AfcPageSnapshot / AfcSkillSnapshot；
  - 根据 skill_id 在旧 run_dir 中找到对应的 abstract_skill_id 及其 abstract_skill_entry。

这样 Repair 模块就不需要直接关心 Build/Evolve 的内部路径与索引细节，可以通过本文件
提供的函数快速拿到所需结构。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import json
import os

from AFCdatabaseEvolve.loader import (  # type: ignore[import]
    GlobalDb,
    JsonDict,
    load_global_db as _load_global_db_evolve,
)
from AFCdatabaseBuild.abstract_skill_index import (  # type: ignore[import]
    build_abstract_skill_index,
)


def load_global_db(path: os.PathLike[str] | str) -> GlobalDb:
    """读取全局 AFC 库（抽象技能 + SkillCase），用于 Repair 侧检索。

    实现上直接复用 AFCdatabaseEvolve.loader.load_global_db，保证两个子系统看到的
    GlobalDb 结构完全一致。
    """
    return _load_global_db_evolve(path)


def load_page_snapshot(run_dir: os.PathLike[str] | str) -> JsonDict:
    """读取 run_dir 下的 AfcPageSnapshot JSON（afc/afc_page_snapshot.json）。

    若文件不存在，则抛出 FileNotFoundError，由调用方决定是否先运行 Build 流程。
    """
    run_dir = Path(run_dir)
    afc_path = run_dir / "afc" / "afc_page_snapshot.json"
    if not afc_path.is_file():
        raise FileNotFoundError(f"AfcPageSnapshot not found: {afc_path}")
    with afc_path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"AfcPageSnapshot must be a JSON object, got {type(obj).__name__}")
    return obj


def load_skill_snapshot(run_dir: os.PathLike[str] | str) -> JsonDict:
    """读取 run_dir 下的 AfcSkillSnapshot JSON（afc/afc_skill_snapshot.json）。

    若文件不存在，则调用 AFCdatabaseBuild.skill.build_skill_snapshot 先行生成，
    然后再读取。
    """
    from AFCdatabaseBuild.skill import build_skill_snapshot  # type: ignore[import]

    run_dir = Path(run_dir)
    afc_dir = run_dir / "afc"
    snap_path = afc_dir / "afc_skill_snapshot.json"
    if not snap_path.is_file():
        snap_path = Path(build_skill_snapshot(run_dir))
    with snap_path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"AfcSkillSnapshot must be a JSON object, got {type(obj).__name__}")
    return obj


def find_abstract_skill_for_skill_id(
    run_dir: os.PathLike[str] | str,
    skill_id: str,
) -> Optional[str]:
    """在单个 run_dir 内，根据具体 skill_id 找到对应的 abstract_skill_id。

    行为：
      1. 使用 AFCdatabaseBuild.abstract_skill_index.build_abstract_skill_index(run_dir)
         构建该 run_dir 的抽象技能索引；
      2. 遍历每个 abstract_skill_entry 的 concrete_skills 列表；
      3. 若某条 concrete_skills[*].skill_id == skill_id，则返回对应的 abstract_skill_id。

    若未找到，则返回 None。
    """
    idx = build_abstract_skill_index(run_dir)
    index: Dict[str, Dict[str, Any]] = idx.get("index") or {}
    for aid, entry in index.items():
        for sk in entry.get("concrete_skills") or []:
            if sk.get("skill_id") == skill_id:
                return aid
    return None


def get_abstract_entry_for_skill_id(
    run_dir: os.PathLike[str] | str,
    skill_id: str,
) -> Optional[JsonDict]:
    """返回给定 run_dir + skill_id 对应的 abstract_skill_entry（如存在）。

    该 entry 结构来自 AfcSkillSnapshot.abstract_skills[*]，通常包含：
      - abstract_skill_id
      - task_group / task_role / norm_label / action
      - semantic_signature
      - afc_controls[]
      - concrete_skills[]
    """
    idx = build_abstract_skill_index(run_dir)
    index: Dict[str, Dict[str, Any]] = idx.get("index") or {}
    for aid, entry in index.items():
        for sk in entry.get("concrete_skills") or []:
            if sk.get("skill_id") == skill_id:
                return entry
    return None


__all__ = [
    "JsonDict",
    "GlobalDb",
    "load_global_db",
    "load_page_snapshot",
    "load_skill_snapshot",
    "find_abstract_skill_for_skill_id",
    "get_abstract_entry_for_skill_id",
]

