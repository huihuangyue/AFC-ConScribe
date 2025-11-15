"""
LLM 驱动的修理提案：定位器精修、前置条件精修、程序修补、命名生成。

提示词模板位于 aid/prompts/ 下；本模块负责渲染占位符、调用 LLM 并解析输出为补丁。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .llm import render_template, call_llm_with_usage, safe_json
from .io import load_run_artifacts


PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
SKILL_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "skill", "prompt")


def _get_snippet_html(new_run_dir: str, skill_id: str) -> str:
    idx_path = os.path.join(new_run_dir, "snippets", "index.json")
    if not os.path.exists(idx_path):
        return ""
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            idx = json.load(f)
        for it in (idx.get("items") or []):
            if str(it.get("id")) == str(skill_id):
                fpath = os.path.join(new_run_dir, it.get("file") or "")
                if os.path.exists(fpath):
                    return open(fpath, "r", encoding="utf-8").read()
    except Exception:
        return ""
    return ""


def _locators_candidates(skill: Dict[str, Any], new_run: Dict[str, Any]) -> Dict[str, Any]:
    locs = skill.get("locators") or {}
    css = []
    if locs.get("selector"):
        css.append(locs.get("selector"))
    css += list(locs.get("selector_alt") or [])
    return {
        "css": css,
        "by_role": locs.get("by_role") or {},
        "by_text": locs.get("by_text") or [],
        "by_dom_index": locs.get("by_dom_index"),
    }


def llm_locators(skill: Dict[str, Any], new_run_dir: str, new_run: Optional[Dict[str, Any]] = None, *, verbose: bool = True) -> List[Dict[str, Any]]:
    """调用 LLM 进行定位器精修，返回 Patch ops 列表（对 /locators 路径）。"""
    sid = str(skill.get("id") or "")
    meta = (new_run or load_run_artifacts(new_run_dir)).get("meta") or {}
    locs = skill.get("locators") or {}
    mapping = {
        "meta.domain": meta.get("domain") or "",
        "meta.url": meta.get("url") or "",
        "ct.id": sid,
        "ct.selector": locs.get("selector") or "",
        "ct.action": skill.get("action") or "",
        "snippet_html": _get_snippet_html(new_run_dir, sid) or "",
        "candidates.css_json": json.dumps((_locators_candidates(skill, new_run or {})).get("css") or [] , ensure_ascii=False),
        "candidates.by_role_json": json.dumps((_locators_candidates(skill, new_run or {})).get("by_role") or {}, ensure_ascii=False),
        "candidates.by_text_json": json.dumps((_locators_candidates(skill, new_run or {})).get("by_text") or [], ensure_ascii=False),
        "candidates.by_dom_index": json.dumps((_locators_candidates(skill, new_run or {})).get("by_dom_index"), ensure_ascii=False),
        "feature.tag": (locs.get("tag") or ""),
        "feature.role": (locs.get("by_role") or {}).get("role", ""),
        "feature.aria_label": (locs.get("by_role") or {}).get("name", ""),
        "feature.classes": "",
        "feature.data_testid": "",
        "neighbor_texts_json": json.dumps(locs.get("by_text") or [], ensure_ascii=False),
    }
    # 复用 skill/prompt 的共享模板，避免与 aid 重复
    prompt_path = os.path.join(SKILL_PROMPTS_DIR, "locator_refine.md")
    prompt = render_template(prompt_path, mapping)
    if verbose:
        print(f"[aid.llm] locators prompt ({len(prompt)} chars)")
    out_text, usage = call_llm_with_usage(prompt)
    data = safe_json(out_text) or {}
    ops: List[Dict[str, Any]] = []
    primary = data.get("primary")
    if isinstance(primary, str) and primary:
        ops.append({"op": "replace", "path": "/locators/selector", "value": primary})
    for a in (data.get("selector_alt") or [])[:3]:
        if isinstance(a, str) and a:
            ops.append({"op": "add", "path": "/locators/selector_alt/-", "value": a})
    if isinstance(data.get("by_role"), dict) and data.get("by_role"):
        ops.append({"op": "replace", "path": "/locators/by_role", "value": data.get("by_role")})
    if isinstance(data.get("by_text"), list) and data.get("by_text"):
        ops.append({"op": "replace", "path": "/locators/by_text", "value": data.get("by_text")})
    if verbose:
        print(f"[aid.llm] locators ops={len(ops)} usage={usage}")
    return {"ops": ops, "usage": usage}


def llm_preconditions(skill: Dict[str, Any], diff_signals: Dict[str, Any], *, verbose: bool = True) -> List[Dict[str, Any]]:
    mapping = {
        "skeleton_preconditions_json": json.dumps(skill.get("preconditions") or {}, ensure_ascii=False),
        "signals.overlay_hits_json": json.dumps(diff_signals.get("overlay_hits") or [], ensure_ascii=False),
        "signals.visible_adv": json.dumps(diff_signals.get("visible_adv") if "visible_adv" in diff_signals else None, ensure_ascii=False),
        "signals.occlusion_ratio_avg": json.dumps(diff_signals.get("occlusion_ratio_avg") if "occlusion_ratio_avg" in diff_signals else None, ensure_ascii=False),
        "meta.viewport_json": json.dumps(((skill.get("meta") or {}).get("viewport") or {}), ensure_ascii=False),
        "is_mobile_bool": "false",
        "locators.selector": (skill.get("locators") or {}).get("selector") or "",
    }
    # 复用 skill/prompt 的共享模板
    prompt_path = os.path.join(SKILL_PROMPTS_DIR, "preconditions_refine.md")
    prompt = render_template(prompt_path, mapping)
    if verbose:
        print(f"[aid.llm] preconditions prompt ({len(prompt)} chars)")
    out_text, usage = call_llm_with_usage(prompt)
    data = safe_json(out_text) or {}
    ops: List[Dict[str, Any]] = []
    pre = data.get("preconditions")
    if isinstance(pre, dict) and pre:
        ops.append({"op": "replace", "path": "/preconditions", "value": pre})
    if verbose:
        print(f"[aid.llm] preconditions ops={len(ops)} usage={usage}")
    return {"ops": ops, "usage": usage}


def llm_program_fix(skill: Dict[str, Any], new_run_dir: str, *, verbose: bool = True) -> List[Dict[str, Any]]:
    sid = str(skill.get("id") or "")
    locs = skill.get("locators") or {}
    mapping = {
        "meta.domain": ((skill.get("domain") or "")),
        "ct.id": sid,
        "ct.selector": locs.get("selector") or "",
        "ct.action": skill.get("action") or "",
        "locators_json": json.dumps(locs, ensure_ascii=False),
        "args_schema_json": json.dumps(skill.get("args_schema") or {}, ensure_ascii=False),
        "current_code": (skill.get("program") or {}).get("code") or "",
        "snippet_html": _get_snippet_html(new_run_dir, sid) or "",
    }
    prompt_path = os.path.join(PROMPTS_DIR, "program_fix.md")
    prompt = render_template(prompt_path, mapping)
    if verbose:
        print(f"[aid.llm] program_fix prompt ({len(prompt)} chars)")
    code, usage = call_llm_with_usage(prompt, temperature=0.1)
    if verbose:
        print(f"[aid.llm] program_fix code_len={len(code)} usage={usage}")
    return {"ops": [{"op": "replace", "path": "/program/code", "value": code}], "usage": usage}


def llm_naming(skill: Dict[str, Any], new_run_dir: str, *, verbose: bool = True) -> List[Dict[str, Any]]:
    sid = str(skill.get("id") or "")
    locs = skill.get("locators") or {}
    mapping = {
        "meta.domain": (skill.get("domain") or ""),
        "feature.role": (locs.get("by_role") or {}).get("role", ""),
        "feature.aria_label": (locs.get("by_role") or {}).get("name", ""),
        "feature.text": (locs.get("by_text") or [""])[0] if (locs.get("by_text") or []) else "",
        "neighbor_texts_json": json.dumps(locs.get("by_text") or [], ensure_ascii=False),
        "ct.action": (skill.get("action") or ""),
    }
    # 复用 skill/prompt 的共享模板
    prompt_path = os.path.join(SKILL_PROMPTS_DIR, "naming.md")
    prompt = render_template(prompt_path, mapping)
    if verbose:
        print(f"[aid.llm] naming prompt ({len(prompt)} chars)")
    out_text, usage = call_llm_with_usage(prompt)
    data = safe_json(out_text) or {}
    ops: List[Dict[str, Any]] = []
    if isinstance(data.get("label"), str):
        ops.append({"op": "replace", "path": "/label", "value": data.get("label")})
    if isinstance(data.get("slug"), str):
        ops.append({"op": "replace", "path": "/slug", "value": data.get("slug")})
    if verbose:
        print(f"[aid.llm] naming ops={len(ops)} usage={usage}")
    return {"ops": ops, "usage": usage}


__all__ = [
    "llm_locators",
    "llm_preconditions",
    "llm_program_fix",
    "llm_naming",
]
