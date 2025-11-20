from __future__ import annotations

"""
skill.description

从技能的 Python 代码（program.code）中自动为技能生成简短描述：
- 优先从主函数的 docstring 中提取“功能概述”；
- 默认写入 skill.meta.description，仅在原描述为空时生效；
- 不依赖 LLM，只用 Python AST 解析。

用法（代码内调用）:
  from skill.description import attach_description_from_program
  attach_description_from_program(skill_obj)

CLI（单技能文件批处理）:
  python -m skill.description --skill path/to/Skill_xxx.json
"""

import ast
import json
from typing import Any, Dict, Optional


def _pick_main_func(tree: ast.AST, *, preferred_name: Optional[str] = None) -> Optional[ast.FunctionDef]:
    """选择主函数节点。

    优先规则：
    1. 若提供 preferred_name，则在顶层函数中匹配该名称；
    2. 否则选取第一个顶层非下划线开头的 def。
    """
    body = getattr(tree, "body", []) or []
    if preferred_name:
        for node in body:
            if isinstance(node, ast.FunctionDef) and node.name == preferred_name:
                return node
    for node in body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            return node
    return None


def _summary_from_docstring(doc: Optional[str]) -> Optional[str]:
    """从 docstring 中提取一行简短描述。"""
    if not doc:
        return None
    text = doc.strip()
    if not text:
        return None
    # 只取第一行，避免长段落
    first = text.splitlines()[0].strip()
    return first or None


def attach_description_from_program(skill: Dict[str, Any], *, overwrite: bool = False) -> None:
    """在 skill.meta.description 上就地填充功能概述。

    规则：
    - 若已有非空 meta.description 且 overwrite=False，则保持不变；
    - 否则尝试从 program.code 的主函数 docstring 中提取第一行作为描述。
    """
    if not isinstance(skill, dict):
        return

    meta = skill.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    existing_desc = meta.get("description")
    if isinstance(existing_desc, str) and existing_desc.strip() and not overwrite:
        # 已有人为或先前生成的描述，则不覆盖
        return

    prog = skill.get("program") or {}
    if not isinstance(prog, dict):
        return
    code = prog.get("code") or ""
    if not isinstance(code, str) or not code.strip():
        return

    main_name: Optional[str] = None
    if isinstance(prog.get("main_func"), str):
        main_name = prog.get("main_func")  # type: ignore[assignment]

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return

    func = _pick_main_func(tree, preferred_name=main_name)
    if func is None:
        return

    doc = ast.get_docstring(func, clean=True)
    summary = _summary_from_docstring(doc)
    if not summary:
        return

    # 写入 meta.description（遵循 overwrite 规则）
    meta["description"] = summary
    skill["meta"] = meta

    # 同步一份到顶层 description，便于索引与人读（同样遵循 overwrite 语义）
    top_desc = skill.get("description")
    if overwrite or not (isinstance(top_desc, str) and top_desc.strip()):
        skill["description"] = summary


def update_skill_file_description(skill_path: str, *, overwrite: bool = False) -> None:
    """从磁盘加载 Skill_*.json，推断 description 并写回（用于批处理或手动修正）。"""
    with open(skill_path, "r", encoding="utf-8") as f:
        obj = json.load(f) or {}
    if not isinstance(obj, dict):
        raise RuntimeError("skill JSON 根对象必须是 dict")
    attach_description_from_program(obj, overwrite=overwrite)
    with open(skill_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Infer skill description from program main function docstring")
    ap.add_argument("--skill", required=True, help="Path to Skill_*.json")
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing meta.description if present",
    )
    args = ap.parse_args()
    update_skill_file_description(args.skill, overwrite=bool(args.overwrite))
    print(f"[skill.description] updated {args.skill}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
