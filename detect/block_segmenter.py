from __future__ import annotations

"""
block_segmenter

基于“主控件块”理念对整页做功能分块：
- 优先使用启发式（类名/角色/面积/子树控件密度）在本地离线可运行；
- 可选接入 LLM（未强制），用于命名/合并/精修；

产物：blocks.json
{
  "blocks": [
    {"id":"b1","name":"商品搜索框","selector":"#kakxi","score":0.86,
     "bbox":[x,y,w,h], "controls": 12, "reason":"inner-wrap; subtree_controls=12"}
  ],
  "log": [...]
}
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

try:  # 包内导入优先
    from .constants import ARTIFACTS  # type: ignore
except Exception:  # 兼容脚本直跑
    from constants import ARTIFACTS  # type: ignore


KW_INNER = ["inner", "inner-wrap", "innerwrap", "list", "items", "result", "panel", "container"]
ROLE_GOOD = {"search", "navigation"}


def _load_elements(out_dir: str) -> List[Dict[str, Any]]:
    parts: List[List[Dict[str, Any]]] = []
    for key in ("dom_summary_scrolled", "dom_summary"):
        p = os.path.join(out_dir, ARTIFACTS[key])
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f) or {}
            els = doc.get("elements") or []
            if isinstance(els, list):
                parts.append(els)
        except Exception:
            continue
    merged: List[Dict[str, Any]] = []
    seen = set()
    for arr in parts:
        for e in arr:
            try:
                idx = int(e.get("index"))
            except Exception:
                continue
            if idx in seen:
                continue
            seen.add(idx)
            merged.append(e)
    return merged


def _is_visible(e: Dict[str, Any]) -> bool:
    bb = e.get("bbox") or [0, 0, 0, 0]
    try:
        return int(bb[2] or 0) > 0 and int(bb[3] or 0) > 0
    except Exception:
        return False


def _is_control(e: Dict[str, Any]) -> bool:
    tag = (e.get("tag") or "").lower()
    role = (e.get("role") or "").lower()
    if tag in {"button", "input", "select", "textarea", "a"}:  # 简版
        return True
    if role in {"button", "link", "textbox", "checkbox", "radio", "combobox"}:
        return True
    cls = (e.get("class") or "").lower()
    if "btn" in cls:
        return True
    try:
        if float(e.get("interactive_score") or 0) >= 0.5:
            return True
    except Exception:
        pass
    return False


def _class_hit(e: Dict[str, Any], kws: List[str]) -> bool:
    cls = (e.get("class") or "").lower()
    return any(kw in cls for kw in kws)


def _subtree_controls_count(elements: List[Dict[str, Any]]) -> Dict[int, int]:
    by_idx = {int(e.get("index")): e for e in elements if isinstance(e.get("index"), (int, float))}
    children: Dict[int, List[int]] = {}
    for e in elements:
        try:
            i = int(e.get("index"))
        except Exception:
            continue
        p = e.get("parent_index")
        if p is None:
            continue
        try:
            pi = int(p)
        except Exception:
            continue
        children.setdefault(pi, []).append(i)
    memo: Dict[int, int] = {}

    def dfs(i: int) -> int:
        if i in memo:
            return memo[i]
        cnt = 1 if _is_control(by_idx.get(i, {})) else 0
        for c in children.get(i, []):
            cnt += dfs(c)
        memo[i] = cnt
        return cnt

    for i in by_idx.keys():
        dfs(i)
    return memo


def _score_block(e: Dict[str, Any], sub_ctrls: Dict[int, int]) -> Tuple[float, str]:
    try:
        idx = int(e.get("index"))
    except Exception:
        return 0.0, "no_index"
    if not _is_visible(e):
        return 0.0, "invisible"
    bb = e.get("bbox") or [0, 0, 0, 0]
    w, h = int(bb[2] or 0), int(bb[3] or 0)
    area = w * h
    if area < 20000:
        return 0.0, "small_area"
    score = 0.0
    reason: List[str] = []
    # 子树控件密度
    sc = int(sub_ctrls.get(idx, 0))
    if sc >= 3:
        score += min(0.6, sc / 50.0)
        reason.append(f"subtree_controls={sc}")
    # 类名命中
    if _class_hit(e, KW_INNER):
        score += 0.2
        reason.append("inner-like")
    # 角色命中
    role = (e.get("role") or "").lower()
    if role in ROLE_GOOD:
        score += 0.1
        reason.append(f"role={role}")
    # 宽高比例适中（排除极端横幅/极窄侧栏）
    if 0.2 <= (w / max(1, h)) <= 5:
        score += 0.1
        reason.append("ratio_ok")
    return score, ", ".join(reason) or "heuristic"


def segment_main_blocks(page, out_dir: str, *, max_blocks: int = 8, use_llm: bool = False) -> Dict[str, Any]:
    """启发式主控件块分割（可选接 LLM 做命名/精修）。
    返回摘要并写出 blocks.json。
    """
    els = _load_elements(out_dir)
    subc = _subtree_controls_count(els)
    candidates: List[Tuple[float, Dict[str, Any], str]] = []
    for e in els:
        s, why = _score_block(e, subc)
        if s <= 0:
            continue
        candidates.append((s, e, why))
    # 按得分排序并去重（简单的 bbox 重叠抑制）
    candidates.sort(key=lambda x: x[0], reverse=True)
    picked: List[Tuple[float, Dict[str, Any], str]] = []
    def iou(a, b) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax2, ay2 = ax + aw, ay + ah
        bx2, by2 = bx + bw, by + bh
        ix0, iy0 = max(ax, bx), max(ay, by)
        ix1, iy1 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
        inter = iw * ih
        if inter == 0:
            return 0.0
        union = aw * ah + bw * bh - inter
        return inter / max(1, union)
    for s, e, why in candidates:
        bb = e.get("bbox") or [0, 0, 0, 0]
        if any(iou(bb, pe.get("bbox") or [0, 0, 0, 0]) > 0.4 for _, pe, _ in picked):
            continue
        picked.append((s, e, why))
        if len(picked) >= int(max_blocks or 1):
            break

    blocks: List[Dict[str, Any]] = []
    for i, (s, e, why) in enumerate(picked, 1):
        sel = _build_selector_like(e)
        bb = e.get("bbox") or [0, 0, 0, 0]
        blocks.append({
            "id": f"b{i}",
            "name": _propose_name(e),
            "selector": sel,
            "score": round(float(s), 4),
            "bbox": [int(bb[0] or 0), int(bb[1] or 0), int(bb[2] or 0), int(bb[3] or 0)],
            "controls": int(subc.get(int(e.get("index")), 0)),
            "reason": why,
        })

    out = {"blocks": blocks, "log": [{"picked": len(picked), "candidates": len(candidates)}]}
    try:
        with open(os.path.join(out_dir, ARTIFACTS["blocks"]), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return out


def _build_selector_like(e: Dict[str, Any]) -> str:
    tag = (e.get("tag") or "").lower() or "*"
    i = (e.get("id") or "").strip()
    if i:
        return f"#{i}"
    nm = e.get("name")
    if nm:
        return f"{tag}[name='{nm}']"
    cls = (e.get("class") or "").strip().split()
    good = [c for c in cls if c and len(c) <= 30][:2]
    if good:
        return f"{tag}.{'/'.join(good)}".replace('/', '.')
    role = (e.get("role") or "").lower()
    if role:
        return f"{tag}[role='{role}']"
    return tag


def _propose_name(e: Dict[str, Any]) -> str:
    role = (e.get("role") or "").lower()
    cls = (e.get("class") or "").lower()
    if role == "search":
        return "搜索模块"
    if "nav" in cls or role == "navigation":
        return "导航栏"
    if "list" in cls or "result" in cls:
        return "结果列表"
    if "filter" in cls or "facet" in cls:
        return "筛选区"
    return "主控件块"

