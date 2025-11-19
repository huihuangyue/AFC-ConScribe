from __future__ import annotations

"""
skill.args_schema

从技能的 Python 代码（program.code）中自动推导 args_schema：
- 面向高层“表单类技能”（如 search_hotel/perform_hotel_search）；
- 不依赖 LLM，仅用 Python AST/类型注解；
- 默认只在原 args_schema 为空时填充，避免覆盖手工定义。
"""

import ast
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ParamInfo:
    name: str
    ann: Optional[ast.expr]
    has_default: bool


def _pick_main_func(tree: ast.AST) -> Optional[ast.FunctionDef]:
    """选择主函数：第一个顶层非下划线开头的 def。"""
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            return node
    return None


def _json_type_from_annotation(ann: Optional[ast.expr]) -> str:
    """根据简单的类型注解推断 JSON Schema 中的 type 字段。"""
    if ann is None:
        return "string"

    def _name(x: ast.expr) -> str:
        if isinstance(x, ast.Name):
            return x.id
        if isinstance(x, ast.Attribute):
            return x.attr
        return ""

    # 处理 Optional[T]/List[T] 等 Subscript
    if isinstance(ann, ast.Subscript):
        base = _name(ann.value)
        if base.lower() in {"optional", "union"}:
            # Optional[T] -> 退化为 T
            try:
                if hasattr(ann.slice, "value"):
                    inner = ann.slice.value  # type: ignore[attr-defined]
                else:
                    inner = ann.slice  # type: ignore[assignment]
            except Exception:
                inner = None
            return _json_type_from_annotation(inner)
        if base.lower() in {"list", "sequence", "tuple"}:
            return "array"

    base = _name(ann).lower()
    if base in {"str", "text", "string"}:
        return "string"
    if base in {"int", "integer"}:
        return "integer"
    if base in {"float", "double", "number"}:
        return "number"
    if base in {"bool", "boolean"}:
        return "boolean"
    if base in {"list", "tuple", "sequence"}:
        return "array"
    if base in {"dict", "mapping"}:
        return "object"
    return "string"


def _collect_params(func: ast.FunctionDef) -> List[ParamInfo]:
    """从函数定义中抽取参数信息（忽略 *args/**kwargs）。"""
    args = func.args.args or []
    defaults = func.args.defaults or []
    # 位置参数中，后 len(defaults) 个带默认值
    n = len(args)
    n_def = len(defaults)
    first_default_idx = n - n_def if n_def else n
    params: List[ParamInfo] = []
    for idx, a in enumerate(args):
        name = a.arg
        if name == "page":
            # 环境参数，不纳入 args_schema
            continue
        has_def = idx >= first_default_idx
        params.append(ParamInfo(name=name, ann=a.annotation, has_default=has_def))
    return params


def infer_args_schema_from_code(code: str, func_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """从一段 Python 代码中解析主函数并构建 args_schema。

    - 若 func_name 提供，则优先匹配该名字的函数；
    - 否则选取第一个顶层公开函数。
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None

    target: Optional[ast.FunctionDef] = None
    if func_name:
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                target = node
                break
    if target is None:
        target = _pick_main_func(tree)
    if target is None:
        return None

    params = _collect_params(target)
    if not params:
        return None

    props: Dict[str, Any] = {}
    required: List[str] = []
    for p in params:
        t = _json_type_from_annotation(p.ann)
        props[p.name] = {"type": t}
        if not p.has_default:
            required.append(p.name)

    schema: Dict[str, Any] = {
        "type": "object",
        "properties": props,
    }
    if required:
        schema["required"] = required
    return schema


def attach_args_schema_from_program(skill: Dict[str, Any], *, overwrite: bool = False) -> None:
    """在 skill 对象上就地填充 args_schema（若为空）。

    规则：
    - 若已有非空 args_schema.properties 且 overwrite=False，则保持不变；
    - 否则尝试从 program.code 推断并写入 skill["args_schema"]。
    """
    existing = skill.get("args_schema")
    if isinstance(existing, dict) and existing.get("properties") and not overwrite:
        return

    prog = skill.get("program") or {}
    code = ""
    if isinstance(prog, dict):
        code = str(prog.get("code") or "")
    if not code:
        return

    # 可选根据 entry 推断函数名，但大多数情况下首个公开函数即主函数
    entry = prog.get("entry")
    func_name: Optional[str] = None
    if isinstance(entry, str):
        # entry 通常是 program__<id>__auto，不直接对应函数名，这里暂不解析
        func_name = None

    schema = infer_args_schema_from_code(code, func_name=func_name)
    if not schema:
        return
    skill["args_schema"] = schema


def update_skill_file_args_schema(skill_path: str, *, overwrite: bool = False) -> None:
    """从磁盘加载 Skill_*.json，推断 args_schema 并写回（用于批处理或手动修正）。"""
    with open(skill_path, "r", encoding="utf-8") as f:
        obj = json.load(f) or {}
    if not isinstance(obj, dict):
        raise RuntimeError("skill JSON 根对象必须是 dict")
    attach_args_schema_from_program(obj, overwrite=overwrite)
    with open(skill_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Infer args_schema from skill.program.code and write back to JSON")
    ap.add_argument("--skill", required=True, help="Path to Skill_*.json")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing args_schema if present")
    args = ap.parse_args()
    update_skill_file_args_schema(args.skill, overwrite=bool(args.overwrite))
    print(f"[skill.args_schema] updated {args.skill}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())

