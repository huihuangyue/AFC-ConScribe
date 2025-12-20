"""
loader

读写 AFC 全局数据库（JSONL 形式）的工具函数。

约定：
  - 全局库通常位于 workspace/AFCdatabase/db/abstract_skills_global.jsonl；
  - 文件采用 JSON Lines 格式，每行一个 JSON 对象；
  - 每条记录至少包含 abstract_skill_id，用作索引键。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import json
import os


JsonDict = Dict[str, Any]


@dataclass
class GlobalDb:
    """内存中的 AFC 全局库表示。

    attributes:
        rows:   原始记录列表（每行一个 abstract_skill entry）
        index:  abstract_skill_id -> 该 entry 的映射视图
        path:   来源文件路径（如存在）
    """

    rows: List[JsonDict]
    index: Dict[str, JsonDict]
    path: Optional[Path] = None


def _ensure_path(path: os.PathLike[str] | str) -> Path:
    """将输入统一为 Path 对象。"""
    return path if isinstance(path, Path) else Path(path)


def load_global_db(path: os.PathLike[str] | str) -> GlobalDb:
    """从 JSONL 文件加载 AFC 全局库。

    规则：
      - 忽略空行和仅包含空白的行；
      - 若某行解析失败，将抛出 ValueError 并指出行号；
      - 若记录缺少 abstract_skill_id，则不会进入 index，但仍保留在 rows 中。
    """
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
            except Exception as e:  # pragma: no cover - 数据错误由调用方处理
                raise ValueError(f"failed to parse JSONL line {lineno} in {p}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"line {lineno} in {p} is not a JSON object")
            rows.append(obj)
            aid = obj.get("abstract_skill_id")
            if isinstance(aid, str):
                # 若存在重复 key，后面的记录覆盖前面的视图，但 rows 保留所有版本
                index[aid] = obj

    return GlobalDb(rows=rows, index=index, path=p)


def save_global_db(db: GlobalDb, path: os.PathLike[str] | str | None = None) -> Path:
    """将 GlobalDb 写回 JSONL 文件。

    参数：
      - db:   内存中的全局库对象；
      - path: 目标路径；若为空则使用 db.path，仍为空则报错。
    """
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
    """从记录序列构建 abstract_skill_id -> entry 的字典视图。

    若存在重复 abstract_skill_id，后出现的记录会覆盖之前的同名键。
    """
    idx: Dict[str, JsonDict] = {}
    for obj in rows:
        if not isinstance(obj, Mapping):
            continue
        aid = obj.get("abstract_skill_id")
        if isinstance(aid, str):
            # 存入一个浅拷贝，避免后续修改原对象导致意外共享
            idx[aid] = dict(obj)
    return idx


def load_exec_log(path: os.PathLike[str] | str) -> JsonDict:
    """读取 exec_log JSON，并做最基本的结构校验。

    约定：
      - 根节点必须是 JSON 对象（dict）；
      - 若存在 skill_cases，则必须是 list（内部元素结构由上层逻辑解释）；
      - 不做字段级强校验，字段缺失由使用方决定如何兜底。

    典型用法：

      from pathlib import Path
      from AFCdatabaseEvolve.loader import load_exec_log

      exec_log = load_exec_log(Path(\"workspace/进化/exec_log_jd.json\"))
      integrate_run_with_evolution(..., exec_log=exec_log, ...)
    """
    p = _ensure_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"exec_log not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if not isinstance(obj, dict):
        raise ValueError(f"exec_log root must be a JSON object, got {type(obj).__name__}")

    if "skill_cases" in obj and not isinstance(obj["skill_cases"], list):
        raise ValueError("exec_log['skill_cases'] must be a list when present")

    return obj


__all__ = [
    "JsonDict",
    "GlobalDb",
    "load_global_db",
    "save_global_db",
    "index_by_id",
    "load_exec_log",
]
