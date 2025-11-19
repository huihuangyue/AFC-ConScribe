from __future__ import annotations

"""
planner.arg_fill

基于自然语言任务 + 技能 JSON 的 args_schema，调用 LLM 生成参数 JSON。

设计要点：
- LLM 只看到：任务描述 + 参数表（名字/类型/说明/是否必填）+ 少量技能上下文（selectors/by_text）；
- 不直接喂整页 DOM，避免 token 爆炸；
- 本地严格校验生成的 JSON，确保字段齐全且类型大致合理。
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import complete_json


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def _load_prompt(name: str) -> str:
    """从 planner/prompt 目录加载提示词模板。"""
    here = os.path.dirname(__file__)
    p = os.path.join(here, "prompt", name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        # 兜底：简单英文说明，避免因提示词文件缺失导致崩溃
        return (
            "You are a helper that fills argument values based on a task and an argument schema.\n"
            "Return a single JSON object whose keys are argument names.\n"
        )


@dataclass
class ArgSpec:
    name: str
    type: str
    description: str
    required: bool


def _extract_args_schema(skill: Dict[str, Any]) -> List[ArgSpec]:
    """从技能 JSON 中抽取参数规格列表。"""
    schema = skill.get("args_schema") or skill.get("program", {}).get("args_schema")
    specs: List[ArgSpec] = []
    if not isinstance(schema, dict):
        return specs
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    if isinstance(required, list):
        required = [str(x) for x in required]
    for name, info in props.items():
        if not isinstance(info, dict):
            continue
        t = str(info.get("type") or "string")
        desc = str(info.get("description") or "")
        specs.append(
            ArgSpec(
                name=str(name),
                type=t,
                description=desc,
                required=str(name) in required,
            )
        )
    return specs


def _skill_context_brief(skill: Dict[str, Any]) -> Dict[str, Any]:
    loc = skill.get("locators") or {}
    by_text = loc.get("by_text") or []
    if isinstance(by_text, list):
        by_text = [str(x) for x in by_text if x]
    else:
        by_text = []
    selector = (loc.get("selector") or "") or ""
    return {
        "id": skill.get("id"),
        "selector": selector,
        "by_text": by_text[:3],
    }


def _build_prompt(
    task: str,
    skill: Dict[str, Any],
    args: List[ArgSpec],
) -> str:
    """构造给 LLM 的最小 JSON 化提示内容。"""
    ctx = _skill_context_brief(skill)
    prompt_obj = {
        "task": task,
        "skill": {
            "id": ctx["id"],
            "selector": ctx["selector"],
            "by_text": ctx["by_text"],
        },
        "args_schema": [
            {
                "name": a.name,
                "type": a.type,
                "description": a.description,
                "required": a.required,
            }
            for a in args
        ],
        "instructions": {
            "note": "实际参数填充规则请参见系统提示词，输出只需要一个 JSON 对象。",
        },
    }
    return json.dumps(prompt_obj, ensure_ascii=False, indent=2)


def _coerce_type(value: Any, target_type: str) -> Any:
    """做一些宽松的类型转换，失败则原样返回。"""
    t = (target_type or "string").lower()
    if t == "string":
        if value is None:
            return ""
        return str(value)
    if t in ("integer", "number"):
        try:
            return int(value)
        except Exception:
            try:
                return float(value)
            except Exception:
                return value
    if t == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "1", "yes", "y", "是"):
            return True
        if s in ("false", "0", "no", "n", "否"):
            return False
        return value
    # 其他类型暂不特殊处理
    return value


def _validate_and_normalize(
    raw_args: Dict[str, Any],
    specs: List[ArgSpec],
) -> Tuple[Dict[str, Any], List[str]]:
    """根据 specs 校验并尽量规整 LLM 输出的参数 JSON。

    返回： (normalized_args, warnings)
    """
    normalized: Dict[str, Any] = {}
    warnings: List[str] = []
    by_name = {a.name: a for a in specs}

    if not isinstance(raw_args, dict):
        warnings.append("llm_output_not_object")
        return {}, warnings

    # 1) 只保留 schema 中存在的字段，并做类型转换
    for k, v in raw_args.items():
        spec = by_name.get(k)
        if not spec:
            # 忽略多余字段
            continue
        normalized[k] = _coerce_type(v, spec.type)

    # 2) 检查必填字段
    missing_required = [a.name for a in specs if a.required and a.name not in normalized]
    if missing_required:
        warnings.append(f"missing_required:{','.join(missing_required)}")

    return normalized, warnings


def fill_args(
    task: str,
    skill: Dict[str, Any],
    *,
    verbose: bool = True,
) -> Tuple[Dict[str, Any], List[str]]:
    """对单个技能进行参数填充。

    返回： (args_dict, warnings)
    - args_dict: 通过校验的参数字典（可能不含所有非必填字段）。
    - warnings: 校验过程中的警告信息列表（如缺少必填字段）。
    """
    specs = _extract_args_schema(skill)
    if not specs:
        return {}, []

    prompt = _build_prompt(task, skill, specs)
    if verbose:
        print(f"[arg_fill] call LLM for skill {skill.get('id')} (args={len(specs)})")
    sys_msg = _load_prompt("fill_args.md")
    raw = complete_json(prompt, system=sys_msg, verbose=verbose)
    args_norm, warnings = _validate_and_normalize(raw, specs)
    if verbose:
        print(f"[arg_fill] filled args keys={list(args_norm.keys())} warnings={warnings}")
    return args_norm, warnings


__all__ = ["fill_args"]
