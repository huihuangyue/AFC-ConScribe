from __future__ import annotations

"""
planner.env_summary

基于 detect 产物构建极简页面摘要（page_summary.json），
供后续技能调度使用。当前阶段只实现纯规则版本，针对携程场景做了
少量硬编码（如优先识别包含 #kakxi 的块）。
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class BlockSummary:
    """页面主控件块的简要摘要。"""

    id: str
    selector: str
    short_name: str
    short_desc: str


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _infer_block_name(selector: str, raw_block: Dict[str, Any]) -> BlockSummary:
    """针对携程首页做一点点启发式命名，其余统一视为“页面功能块”。

    selector: CSS 选择器（可能为空）
    raw_block: blocks.json 中的原始块记录（可选，用于 future 扩展）
    """
    sel = selector.strip()
    bid = str(raw_block.get("id") or "")
    # 默认值
    short_name = "页面功能块"
    short_desc = "页面上的一个功能区域。"

    lower_sel = sel.lower()

    # 携程酒店搜索模块相关
    if "#kakxi" in sel or "hs_list-search-container" in lower_sel or "hotelsearchv1" in lower_sel:
        short_name = "酒店搜索模块"
        short_desc = "包含目的地、入住/退房日期、房间及住客、酒店级别、关键词和搜索按钮等字段。"
    # 推荐/列表类区域
    elif "pas_hotel-container" in lower_sel or "hotel" in lower_sel and "list" in lower_sel:
        short_name = "酒店推荐模块"
        short_desc = "展示酒店推荐卡片或搜索结果列表。"
    # 顶部导航
    elif "nav" in lower_sel or "header" in lower_sel:
        short_name = "导航栏模块"
        short_desc = "包含站点导航入口，如首页、订单、客服等。"
    # 底部区域
    elif "footer" in lower_sel:
        short_name = "底部区域模块"
        short_desc = "页面底部区域，包含版权信息与辅助链接。"

    return BlockSummary(
        id=bid or "",
        selector=sel,
        short_name=short_name,
        short_desc=short_desc,
    )


def build_page_summary(run_dir: str, *, verbose: bool = True) -> Dict[str, Any]:
    """读取 run_dir 下 detect 产物，生成 page_summary.json 并返回结构。"""
    if verbose:
        print(f"[env_summary] run_dir={run_dir}")
    blocks_path = os.path.join(run_dir, "blocks.json")
    controls_path = os.path.join(run_dir, "controls_tree.json")
    ax_path = os.path.join(run_dir, "ax.json")
    meta_path = os.path.join(run_dir, "meta.json")

    blocks_doc = _read_json(blocks_path)
    controls_doc = _read_json(controls_path)
    ax_doc = _read_json(ax_path)
    meta_doc = _read_json(meta_path)

    raw_blocks = blocks_doc.get("blocks") or []
    block_summaries: List[BlockSummary] = []

    # 若存在 blocks.json，则优先使用其中的 blocks
    if isinstance(raw_blocks, list) and raw_blocks:
        for b in raw_blocks:
            if not isinstance(b, dict):
                continue
            selector = (b.get("selector") or "").strip()
            # 若 blocks.json 中已有 name 字段，可直接复用
            if b.get("name"):
                short_name = str(b.get("name"))
                short_desc = str(b.get("description") or "")
                block_summaries.append(
                    BlockSummary(
                        id=str(b.get("id") or ""),
                        selector=selector,
                        short_name=short_name,
                        short_desc=short_desc or "页面上的一个功能区域。",
                    )
                )
            else:
                block_summaries.append(_infer_block_name(selector, b))
    else:
        # fallback：从控件树 roots 中构造几个粗粒度块（非常粗糙，仅用于无 blocks.json 时）
        nodes = [n for n in (controls_doc.get("nodes") or []) if isinstance(n, dict)]
        by_id: Dict[str, Dict[str, Any]] = {str(n.get("id")): n for n in nodes}
        roots = [str(r) for r in (controls_doc.get("roots") or [])]
        for rid in roots:
            node = by_id.get(rid) or {}
            sel = (node.get("selector") or "").strip()
            block_summaries.append(_infer_block_name(sel, {"id": rid}))

    # 选择 main_block_id：优先 selector 含 #kakxi 或名字中含“酒店搜索”
    main_block_id: Optional[str] = None
    for blk in block_summaries:
        if "#kakxi" in blk.selector:
            main_block_id = blk.id
            break
    if main_block_id is None:
        for blk in block_summaries:
            if "酒店搜索" in blk.short_name:
                main_block_id = blk.id
                break
    if main_block_id is None and block_summaries:
        main_block_id = block_summaries[0].id

    page_summary = {
        "meta": {
            "url": meta_doc.get("url"),
            "domain": meta_doc.get("domain") or meta_doc.get("domain_sanitized"),
            "title": meta_doc.get("title"),
        },
        "blocks": [asdict(b) for b in block_summaries],
        "main_block_id": main_block_id,
    }

    out_path = os.path.join(run_dir, "page_summary.json")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(page_summary, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"[env_summary] wrote {out_path}")
    except Exception as e:
        if verbose:
            print(f"[env_summary] write error: {e}")
    return page_summary


def _cli() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Build minimal page summary from detect artifacts")
    p.add_argument("--run-dir", required=True, help="Detect run dir, e.g. workspace/data/ctrip_com/20251116032614")
    p.add_argument("--no-verbose", dest="verbose", action="store_false")
    args = p.parse_args()
    build_page_summary(args.run_dir, verbose=getattr(args, "verbose", True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

