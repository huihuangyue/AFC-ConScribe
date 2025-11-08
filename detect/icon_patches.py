"""
detect.icon_patches
为控件树中的每个节点，基于 geom.bbox/shape 从截图裁剪出“图标贴图”。

规则（启发式，仅使用 bbox/shape，不依赖文本）：
- 若控件尺寸较小（min(w,h) <= 48）或形状为 round/pill 且尺寸不大：
  - 认为控件本身即为图标，裁剪整个 bbox。
- 否则：
  - 在控件内左侧裁剪一个近似正方形图标区域（高度 80% 以内，宽度不超过一半，最大 32px），并垂直居中。

输出：
- 在数据目录下创建 `icons/` 子目录，保存为 `icons/<node_id>.png`。
- 在 controls_tree.json 中为相应节点追加字段：
  - icon: { path: "icons/<node_id>.png", roi: [x,y,w,h] }
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from PIL import Image


def _clip_rect(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    x = max(0, min(x, img_w))
    y = max(0, min(y, img_h))
    w = max(0, min(w, img_w - x))
    h = max(0, min(h, img_h - y))
    return x, y, w, h


def _icon_roi(node: Dict[str, Any], img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    g = node.get("geom") or {}
    bbox = g.get("bbox") or [0, 0, 0, 0]
    x, y, w, h = [int(v or 0) for v in bbox]
    shape = (g.get("shape") or "").lower()

    # 小控件或圆/胶囊且尺寸不大：直接取整个 bbox
    if min(w, h) <= 48 or (shape in {"round", "pill"} and max(w, h) <= 56):
        return _clip_rect(x, y, w, h, img_w, img_h)

    # 大控件：在左内侧取一个近似正方形的小贴图
    # 目标边长：不超过 32px，且不超过控件高度的 80% 与一半宽度
    side = min(32, int(h * 0.8), max(8, int(w * 0.5)))
    # 左侧留出 4px 内边距，垂直居中
    px = x + 4
    py = y + max(0, (h - side) // 2)
    roi = _clip_rect(px, py, side, side, img_w, img_h)
    if roi[2] <= 4 or roi[3] <= 4:
        # 回退为整个 bbox（极端情况）
        return _clip_rect(x, y, w, h, img_w, img_h)
    return roi


def generate_icon_patches(data_dir: str, *,
                          tree_file: str = "controls_tree.json",
                          screenshot_file: str = "screenshot_loaded.png",
                          icons_subdir: str = "icons") -> str:
    """根据 controls_tree 与截图，生成图标贴图并回写 tree 中的 icon 字段。

    返回更新后的 controls_tree.json 路径。
    """
    tree_path = os.path.join(data_dir, tree_file)
    img_path = os.path.join(data_dir, screenshot_file)
    out_dir = os.path.join(data_dir, icons_subdir)
    os.makedirs(out_dir, exist_ok=True)

    with open(tree_path, "r", encoding="utf-8") as f:
        tree = json.load(f)
    nodes = tree.get("nodes") or []

    img = Image.open(img_path).convert("RGB")
    iw, ih = img.size

    for n in nodes:
        nid = n.get("id")
        if not nid:
            continue
        rx, ry, rw, rh = _icon_roi(n, iw, ih)
        if rw <= 0 or rh <= 0:
            continue
        patch = img.crop((rx, ry, rx + rw, ry + rh))
        out_path = os.path.join(out_dir, f"{nid}.png")
        patch.save(out_path)
        # 回写节点 icon 信息
        n["icon"] = {
            "path": os.path.join(icons_subdir, f"{nid}.png"),
            "roi": [rx, ry, rw, rh],
        }

    with open(tree_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)
    return tree_path


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="从控件树与截图生成图标贴图")
    p.add_argument("--dir", required=True, help="数据目录 data/<domain>/<timestamp>")
    p.add_argument("--tree", default="controls_tree.json")
    p.add_argument("--image", default="screenshot_loaded.png")
    p.add_argument("--icons", default="icons")
    args = p.parse_args()
    path = generate_icon_patches(args.dir, tree_file=args.tree, screenshot_file=args.image, icons_subdir=args.icons)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

