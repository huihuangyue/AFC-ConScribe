"""
抽象技能索引辅助模块（单 run_dir 版）。

说明：
- 为了满足“不跨 run_dir 操作”的约束，这里只提供**单 run_dir 内部**的索引视图；
- 若给定 run_dir 下尚未生成 afc_skill_snapshot.json，则调用 build_skill_snapshot 先行构建；
- 对外暴露 build_abstract_skill_index(run_dir)，返回一个简单的 in-memory 索引：
  {
    "run_dir": str,
    "domain": str,
    "index": {abstract_skill_id -> abstract_skill_entry(dict)}
  }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json

from .skill import build_skill_snapshot


def build_abstract_skill_index(run_dir: str | Path) -> Dict[str, Any]:
    """
    为单个 run_dir 构建抽象技能索引（内存结构）。

    行为：
      1. 确保 run_dir/afc/afc_skill_snapshot.json 存在，如无则调用 build_skill_snapshot(run_dir) 生成；
      2. 读取该 JSON，并以 abstract_skill_id 为键构造一个索引字典；
      3. 返回 {\"run_dir\", \"domain\", \"index\"}，其中 index 是 abstract_skill_id -> entry 的映射。
    """
    run_dir = Path(run_dir)
    afc_dir = run_dir / "afc"
    snap_path = afc_dir / "afc_skill_snapshot.json"
    if not snap_path.is_file():
        # 如尚未生成技能快照，则先构建一份
        snap_path = build_skill_snapshot(run_dir)

    with snap_path.open("r", encoding="utf-8") as f:
        obj: Dict[str, Any] = json.load(f)

    domain = obj.get("domain")
    entries = obj.get("abstract_skills") or []
    index: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        key = e.get("abstract_skill_id")
        if not isinstance(key, str):
            continue
        index[key] = e

    return {
        "run_dir": str(run_dir),
        "domain": domain,
        "index": index,
    }


__all__ = ["build_abstract_skill_index"]

