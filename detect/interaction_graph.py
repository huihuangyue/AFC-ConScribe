from __future__ import annotations

"""
interaction_graph

为每个主控件块做有限枚举式交互探索，构建 JSON 因果图：
graph = {
  "block_id": "b1",
  "root": {"selector": "#kakxi"},
  "edges": [
    {"action": "click", "target": {"selector":"button.hs_search"}, "effects": {"new_controls": 5, "notes": "弹出日历"}}
  ]
}

说明：此模块给出最小可运行骨架，依赖页面已在目标 URL，且 DetectHelpers 可用。
"""

import json
import os
from typing import Any, Dict, List, Optional

try:
    from .constants import ARTIFACTS  # type: ignore
except Exception:
    from constants import ARTIFACTS  # type: ignore


def _read_blocks(out_dir: str) -> List[Dict[str, Any]]:
    p = os.path.join(out_dir, ARTIFACTS["blocks"])
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f) or {}
        return [b for b in (doc.get("blocks") or []) if isinstance(b, dict)]
    except Exception:
        return []


def _snapshot_controls(page) -> List[str]:
    try:
        return page.evaluate("() => Array.from(document.querySelectorAll('[__actiontype]')).map(e=>e.getAttribute('__selectorid')||'#')") or []
    except Exception:
        return []


def _allowed_ids_for_block(out_dir: str, block: Dict[str, Any]) -> Optional[set[str]]:
    """Try to restrict effects/new_ids to descendants of the block root node in controls_tree.
    Matches by exact selector; returns a set of node ids like {'d321', ...}.
    If not found, returns None (no restriction).
    """
    try:
        ct_path = os.path.join(out_dir, 'controls_tree.json')
        if not os.path.exists(ct_path):
            return None
        with open(ct_path, 'r', encoding='utf-8') as f:
            tree = json.load(f) or {}
        nodes = [n for n in (tree.get('nodes') or []) if isinstance(n, dict)]
        by_id = {str(n.get('id')): n for n in nodes}
        target_sel = (block.get('selector') or '').strip()
        if not target_sel:
            return None
        root_id = None
        for n in nodes:
            if (n.get('selector') or '').strip() == target_sel:
                root_id = str(n.get('id'))
                break
        if not root_id:
            return None
        allowed: set[str] = set()
        q = [root_id]
        while q:
            nid = q.pop(0)
            if nid in allowed:
                continue
            allowed.add(nid)
            node = by_id.get(nid) or {}
            for c in (node.get('children') or []):
                if isinstance(c, str):
                    q.append(c)
        return allowed
    except Exception:
        return None


def explore_block(page, out_dir: str, block: Dict[str, Any], *, max_ops: int = 20, wait_ms: int = 500) -> Dict[str, Any]:
    selector = block.get("selector") or ""
    graph: Dict[str, Any] = {"block_id": block.get("id"), "root": {"selector": selector}, "edges": []}
    try:
        if selector:
            try:
                page.wait_for_selector(selector, timeout=2000)
            except Exception:
                pass
        allowed_ids = _allowed_ids_for_block(out_dir, block)
        base_all = set(_snapshot_controls(page))
        base = base_all if not allowed_ids else {i for i in base_all if i and i in allowed_ids}
        # 简化：调用 revealInteractively 批量做有限探索，并据 steps 构边
        try:
            steps = page.evaluate("opts => window.DetectHelpers && window.DetectHelpers.revealInteractively ? window.DetectHelpers.revealInteractively(opts) : ({ok:false,steps:[]})",
                                  {"maxActions": int(max_ops), "waitMs": int(wait_ms), "totalBudgetMs": int(max_ops*wait_ms*2)})
        except Exception:
            steps = {"ok": False, "steps": []}
        for st in (steps.get("steps") or []):
            try:
                after_all = set(_snapshot_controls(page))
                after = after_all if not allowed_ids else {i for i in after_all if i and i in allowed_ids}
                diff = [x for x in after if x not in base]
                base = after
                graph["edges"].append({
                    "action": st.get("action"),
                    "target": st.get("target"),
                    "effects": {"new_controls": len(diff), "new_ids": diff[:10]},
                })
            except Exception:
                continue
    except Exception:
        pass
    # 写文件
    gd = os.path.join(out_dir, ARTIFACTS["graphs_dir"])
    try:
        os.makedirs(gd, exist_ok=True)
        fp = os.path.join(gd, f"graph_{block.get('id')}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return graph


def explore_all_blocks(page, out_dir: str, *, max_ops_per_block: int = 20, wait_ms: int = 500) -> List[Dict[str, Any]]:
    blocks = _read_blocks(out_dir)
    res: List[Dict[str, Any]] = []
    for b in blocks:
        res.append(explore_block(page, out_dir, b, max_ops=max_ops_per_block, wait_ms=wait_ms))
    return res
