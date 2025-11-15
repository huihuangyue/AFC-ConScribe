#!/usr/bin/env python3
"""
MVFN‑lite | 离线元素分类脚本

输入一个采集数据目录（如 workspace/data/<domain>/<ts>/），基于 dom 摘要与控件树：
  - 标注 static（初始就存在且未明显变化）
  - 标注 dynamic（滚动后新增或显著变化）
  - 标注 db_likely（启发式：列表/卡片/新闻/广告等信息流项，动态中挑选）

输出：
  1) <dir>/element_classification.json — 分类结果与每个节点的标签
  2) <dir>/element_classified_overlay.png — 在 screenshot_loaded.png 上彩色框标

说明：
  - "接入数据库"无法从离线文件直接证明，本脚本的 db_likely 仅为启发式提示。
  - 若需要更可靠的“API 驱动”判断，应在采集阶段记录网络请求与 DOM 变更时序并做关联。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set

from PIL import Image, ImageDraw


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def valid(self) -> bool:
        return self.w > 0 and self.h > 0


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _to_box(v: List[int] | Tuple[int, int, int, int] | None) -> Box:
    if not v or len(v) < 4:
        return Box(0, 0, 0, 0)
    try:
        x, y, w, h = int(v[0] or 0), int(v[1] or 0), int(v[2] or 0), int(v[3] or 0)
    except Exception:
        x = y = w = h = 0
    return Box(x, y, w, h)


def _load_elements(path: str) -> List[dict]:
    doc = _load_json(path)
    els = doc.get("elements")
    return els if isinstance(els, list) else []


def _id_from_index(idx: int | None) -> Optional[str]:
    try:
        if idx is None:
            return None
        return f"d{int(idx)}"
    except Exception:
        return None


def _build_lookup(elements: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for e in elements:
        try:
            idx = int(e.get("index"))
        except Exception:
            continue
        out[f"d{idx}"] = e
    return out


def _bbox_changed(a: Box, b: Box, *, tol_ratio: float = 0.2, tol_px: int = 8) -> bool:
    if not (a.valid and b.valid):
        return False
    dx = abs(a.x - b.x)
    dy = abs(a.y - b.y)
    dw = abs(a.w - b.w)
    dh = abs(a.h - b.h)
    if dx > tol_px or dy > tol_px or dw > tol_px or dh > tol_px:
        return True
    try:
        area_a = max(1, a.w * a.h)
        area_b = max(1, b.w * b.h)
        ratio = abs(area_a - area_b) / max(area_a, area_b)
        return ratio > tol_ratio
    except Exception:
        return False


def _text_changed(a: str, b: str, *, tol: float = 0.5) -> bool:
    if not a and not b:
        return False
    if (a or "") == (b or ""):
        return False
    la, lb = len(a or ""), len(b or "")
    if la == 0 or lb == 0:
        return True
    r = abs(la - lb) / max(la, lb)
    return r >= tol


def classify(dir_path: str) -> Dict[str, Set[str]]:
    p_init = os.path.join(dir_path, "dom_summary.json")
    p_scroll = os.path.join(dir_path, "dom_summary_scrolled.json")
    p_new = os.path.join(dir_path, "dom_scrolled_new.json")
    p_tree = os.path.join(dir_path, "controls_tree.json")

    init_els = _load_elements(p_init)
    scroll_els = _load_elements(p_scroll)
    init_map = _build_lookup(init_els)
    scroll_map = _build_lookup(scroll_els)

    def _visible_ok(e: dict) -> bool:
        bb = _to_box(e.get("bbox"))
        vis = e.get("visible_adv") if e.get("visible_adv") is not None else e.get("visible")
        return bb.valid and bool(vis)

    initial_ids: Set[str] = set()
    for e in init_els:
        nid = _id_from_index(e.get("index"))
        if nid and _visible_ok(e):
            initial_ids.add(nid)

    scrolled_ids: Set[str] = set()
    for e in scroll_els:
        nid = _id_from_index(e.get("index"))
        if nid and _visible_ok(e):
            scrolled_ids.add(nid)

    new_ids: Set[str] = set()
    doc_new = _load_json(p_new)
    if isinstance(doc_new.get("new_elements"), list):
        for e in (doc_new.get("new_elements") or []):
            nid = _id_from_index(e.get("index"))
            if nid and _visible_ok(e):
                new_ids.add(nid)
    else:
        new_ids = {i for i in scrolled_ids if i not in initial_ids}

    changed_ids: Set[str] = set()
    common = initial_ids & scrolled_ids
    for nid in common:
        a = init_map.get(nid) or {}
        b = scroll_map.get(nid) or {}
        if not a or not b:
            continue
        if _bbox_changed(_to_box(a.get("bbox")), _to_box(b.get("bbox"))) or _text_changed(a.get("text") or "", b.get("text") or ""):
            changed_ids.add(nid)

    dynamic: Set[str] = set(new_ids) | set(changed_ids)

    tree = _load_json(p_tree) or {}
    nodes = tree.get("nodes") or []
    node_ids = {n.get("id") for n in nodes if n.get("id")}
    dynamic &= node_ids
    static = node_ids - dynamic

    # db_likely 启发式
    keywords = (
        "card", "tile", "feed", "list", "item", "result", "story", "news",
        "post", "sponsor", "sponsored", "ad", "ads", "recommend",
    )
    db_likely: Set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if nid not in dynamic:
            continue
        sel = (n.get("selector") or "").lower()
        ntype = (n.get("type") or "").lower()
        g = n.get("geom") or {}
        bb = _to_box(g.get("bbox"))
        text = ""
        src = scroll_map.get(nid) or init_map.get(nid) or {}
        try:
            text = (src.get("text") or "").lower()
        except Exception:
            text = ""
        if ntype == "content" and bb.w * bb.h >= 20000 and any(k in sel or k in text for k in keywords):
            db_likely.add(nid)

    return {"static": static, "dynamic": dynamic, "db_likely": db_likely}


def _node_box(n: dict) -> Box:
    g = n.get("geom") or {}
    pbb = _to_box(g.get("page_bbox"))
    if pbb.valid:
        return pbb
    return _to_box(g.get("bbox"))


def draw_overlay(dir_path: str, image_name: str, out_name: str, classes: Dict[str, Set[str]], *, alpha: int = 0, thickness: int = 3) -> str:
    img_path = os.path.join(dir_path, image_name)
    out_path = out_name
    img = Image.open(img_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def color_of(nid: str) -> Tuple[int, int, int, int]:
        if nid in classes.get("db_likely", set()):
            return (160, 80, 255, 255)
        if nid in classes.get("dynamic", set()):
            return (255, 140, 0, 255)
        if nid in classes.get("static", set()):
            return (64, 200, 80, 255)
        return (160, 160, 160, 255)

    tree = _load_json(os.path.join(dir_path, "controls_tree.json")) or {}
    for n in (tree.get("nodes") or []):
        nid = n.get("id")
        if not nid:
            continue
        b = _node_box(n)
        if not b.valid:
            continue
        col = color_of(nid)
        draw.rectangle([b.x, b.y, b.x + b.w, b.y + b.h], outline=col, width=max(1, thickness))
        if alpha > 0:
            fill = (col[0], col[1], col[2], max(0, min(255, alpha)))
            draw.rectangle([b.x + 1, b.y + 1, b.x + b.w - 1, b.y + b.h - 1], fill=fill)

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(out_path)
    return out_path


def write_report(dir_path: str, out_json: str, classes: Dict[str, Set[str]]) -> str:
    tree = _load_json(os.path.join(dir_path, "controls_tree.json")) or {}
    nodes = tree.get("nodes") or []
    by_node: Dict[str, dict] = {}
    for n in nodes:
        nid = n.get("id")
        if not nid:
            continue
        label = "static"
        if nid in classes.get("dynamic", set()):
            label = "dynamic"
        if nid in classes.get("db_likely", set()):
            label = "db_likely"
        g = n.get("geom") or {}
        by_node[nid] = {
            "label": label,
            "selector": n.get("selector"),
            "type": n.get("type"),
            "bbox": g.get("bbox"),
            "page_bbox": g.get("page_bbox"),
        }

    summary = {k: len(v) for k, v in classes.items()}
    payload = {
        "summary": summary,
        "by_node": by_node,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_json


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="离线分类 static/dynamic/db_likely 并生成叠加标注与 JSON 报告")
    p.add_argument("--dir", required=True, help="数据目录 data/<domain>/<ts>")
    p.add_argument("--image", default="screenshot_loaded_cropped.png", help="作为底图的截图文件名（默认 screenshot_loaded_cropped.png；不存在时自动回退）")
    p.add_argument("--overlay", default=None, help="输出叠加图文件名（默认 element_classified_overlay.png）")
    p.add_argument("--out-json", default=None, help="输出 JSON 报告文件名（默认 element_classification.json）")
    p.add_argument("--alpha", type=int, default=0, help="填充透明度 0~255（默认 0）")
    p.add_argument("--thickness", type=int, default=3, help="描边线宽（默认 3）")
    args = p.parse_args()

    dir_path = args.dir
    overlay_path = args.overlay or os.path.join(dir_path, "element_classified_overlay.png")
    out_json = args.out_json or os.path.join(dir_path, "element_classification.json")

    # 选择基图：优先用户指定；若为默认名且不存在，则自动回退
    image_name = args.image
    candidate_fallbacks = [image_name, "screenshot_loaded.png", "screenshot_scrolled_tail.png"]
    chosen = None
    for cand in candidate_fallbacks:
        if os.path.exists(os.path.join(dir_path, cand)):
            chosen = cand
            break
    if not chosen:
        raise SystemExit(f"No base image found under {dir_path}. Tried: {', '.join(candidate_fallbacks)}")

    classes = classify(dir_path)
    write_report(dir_path, out_json, classes)
    draw_overlay(dir_path, chosen, overlay_path, classes, alpha=max(0, min(255, args.alpha)), thickness=max(1, int(args.thickness)))

    print(f"[SUMMARY] static={len(classes['static'])} dynamic={len(classes['dynamic'])} db_likely={len(classes['db_likely'])}")
    print(f"[OUTPUT] json={out_json}")
    print(f"[OUTPUT] overlay={overlay_path}")
    print(f"[BASE] image={os.path.join(dir_path, chosen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
