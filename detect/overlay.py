"""
detect.overlay
在截图上根据控件树（controls_tree.json）打框，可按“深度”染上不同颜色与线宽。

用法（命令行）：
    python detect/overlay.py --dir data/<domain>/<ts> \
        --image screenshot_loaded.png \
        --out   screenshot_loaded_overlay.png \
        --min-thickness 1 --max-thickness 6 --alpha 0 --label

说明：
    - 默认读取目录下的 controls_tree.json 与指定截图文件；
    - 盒子框包含组件 bbox，颜色与线宽随深度变化；
    - 可选绘制 id 标签（左上角小字）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont


def _load_tree(tree_path: str) -> Dict[str, Any]:
    with open(tree_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _compute_depths(nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    # 多根：parent=None 的为根，深度 0；其余按 parent+1 迭代，防御环
    by_id = {n["id"]: n for n in nodes}
    depth: Dict[str, int] = {}

    # 初始化根
    for n in nodes:
        if not n.get("parent"):
            depth[n["id"]] = 0

    # 迭代传播深度（最多 N 次，防环）
    for _ in range(len(nodes)):
        changed = False
        for n in nodes:
            pid = n.get("parent")
            nid = n["id"]
            if pid and pid in depth:
                cand = depth[pid] + 1
                if nid not in depth or cand < depth[nid]:
                    depth[nid] = cand
                    changed = True
            elif nid not in depth and not pid:
                depth[nid] = 0
                changed = True
        if not changed:
            break
    # 未赋值的节点（环或孤立），给个默认 0
    for n in nodes:
        depth.setdefault(n["id"], 0)
    return depth


def _palette(depth: int) -> Tuple[int, int, int]:
    # 9 色循环（RGB），更偏高饱和以在网页上清晰可见
    colors = [
        (240,  64,  64), (255, 140,   0), (255, 210,  60),
        ( 64, 200,  80), ( 60, 200, 200), ( 60, 140, 255),
        ( 64,  64, 255), (160,  80, 255), (230,  60, 230),
    ]
    return colors[depth % len(colors)]


def _map_thickness(depth: int, max_depth: int, min_t: int, max_t: int) -> int:
    if max_depth <= 0:
        return max_t
    span = max(1, max_t - min_t)
    # 越浅的深度（靠近根）线越粗
    t = min_t + round(span * (max_depth - depth) / max_depth)
    return max(min_t, min(max_t, t))


def draw_overlay(
    image_path: str,
    tree_path: str,
    out_path: str,
    *,
    min_thickness: int = 1,
    max_thickness: int = 6,
    alpha: int = 0,
    label: bool = False,
) -> None:
    """在 image_path 上绘制控件框，输出至 out_path。

    alpha: 0 表示不填充，仅描边；>0 可在轮廓内叠加半透明色块（0~128 推荐）。
    label: 是否在左上角绘制 id 文本。
    """
    tree = _load_tree(tree_path)
    nodes = tree.get("nodes") or []
    if not nodes:
        raise RuntimeError("controls_tree.json 中 nodes 为空")

    depth_map = _compute_depths(nodes)
    max_depth = max(depth_map.values()) if depth_map else 0

    img = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()

    for n in nodes:
        nid = n.get("id")
        bbox = n.get("geom", {}).get("bbox") or [0, 0, 0, 0]
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            continue
        d = depth_map.get(nid, 0)
        color = _palette(d)
        width = _map_thickness(d, max_depth, min_thickness, max_thickness)
        # 轮廓
        draw.rectangle([x, y, x + w, y + h], outline=color + (255,), width=width)
        # 半透明填充（可选）
        if alpha > 0:
            fill = color + (max(0, min(255, alpha)),)
            # 内缩 1px 以免盖住轮廓
            draw.rectangle([x + 1, y + 1, x + w - 1, y + h - 1], fill=fill)
        # 标签（可选）
        if label and nid:
            tx, ty = x + 2, max(0, y - 10)
            # 简易描边文字：先画深色底，再画浅色字
            draw.text((tx + 1, ty + 1), nid, font=font, fill=(0, 0, 0, 255))
            draw.text((tx, ty), nid, font=font, fill=(255, 255, 255, 255))

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(out_path)


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="在截图上根据控件树打框")
    p.add_argument("--dir", required=True, help="数据目录 data/<domain>/<ts>")
    p.add_argument("--image", default="screenshot_loaded.png", help="输入截图文件名")
    p.add_argument("--tree", default="controls_tree.json", help="控件树文件名")
    p.add_argument("--out", default=None, help="输出文件名（默认在输入名后加 _overlay.png）")
    p.add_argument("--min-thickness", type=int, default=1)
    p.add_argument("--max-thickness", type=int, default=6)
    p.add_argument("--alpha", type=int, default=0, help="填充透明度 0~255，建议 0~128")
    p.add_argument("--label", action="store_true", help="是否绘制节点 id 标签")
    args = p.parse_args()

    image_path = os.path.join(args.dir, args.image)
    tree_path = os.path.join(args.dir, args.tree)
    out_path = args.out or os.path.splitext(image_path)[0] + "_overlay.png"

    draw_overlay(
        image_path,
        tree_path,
        out_path,
        min_thickness=args.min_thickness,
        max_thickness=args.max_thickness,
        alpha=args.alpha,
        label=args.label,
    )
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
