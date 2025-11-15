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


def _try_load_segments_map(base_dir: str, img_w: int, img_h: int) -> Dict[str, Any] | None:
    """尝试加载容器拼接映射（segments/index.json）。

    仅当映射中的 stitched 尺寸与当前 image 尺寸一致时返回映射，否则返回 None。
    """
    seg_path = os.path.join(base_dir, "segments", "index.json")
    if not os.path.exists(seg_path):
        return None
    try:
        with open(seg_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        st = m.get("stitched") or {}
        if int(st.get("width", -1)) == int(img_w) and int(st.get("height", -1)) == int(img_h):
            return m
    except Exception:
        return None
    return None


def _project_bbox_to_stitched(bbox: List[int], mapping: Dict[str, Any], *, stitched_w: int, stitched_h: int) -> List[List[int]]:
    """将视口坐标下的 bbox 映射到容器拼接画布坐标。

    返回可能被切分成多段的矩形列表 [x,y,w,h]，已裁剪到画布范围内。
    要求 mapping 中包含：
      - container.bbox_viewport_final: [x,y,w,h]
      - container.scrollTop_final: number
      - stitched.segments: [{content_top, content_height, y}, ...]
    """
    try:
        cx, cy, cw, ch = mapping.get("container", {}).get("bbox_viewport_final", [0, 0, 0, 0])
        s_final = int(mapping.get("container", {}).get("scrollTop_final", 0))
        segs = mapping.get("stitched", {}).get("segments", [])
        x, y, w, h = [int(v or 0) for v in (bbox or [0, 0, 0, 0])]
        if w <= 0 or h <= 0:
            return []
        # 转为容器内容坐标
        lx0 = x - int(cx)
        ly0 = y - int(cy) + s_final
        lx1 = lx0 + w
        ly1 = ly0 + h
        # 限定到容器横向宽度
        lx0 = max(0, min(lx0, cw))
        lx1 = max(0, min(lx1, cw))
        if lx1 <= lx0:
            return []
        res: List[List[int]] = []
        for seg in segs:
            top = int(seg.get("content_top", seg.get("scrollTop", 0)))
            sh = int(seg.get("content_height", seg.get("height", 0)))
            yy = int(seg.get("y", 0))
            if sh <= 0:
                continue
            sy0 = top
            sy1 = top + sh
            # 垂直相交范围（容器内容坐标）
            iy0 = max(ly0, sy0)
            iy1 = min(ly1, sy1)
            if iy1 <= iy0:
                continue
            # 映射到画布坐标
            px = max(0, min(lx0, stitched_w))
            py = max(0, min(yy + (iy0 - sy0), stitched_h))
            pw = max(0, min(lx1 - lx0, stitched_w - px))
            ph = max(0, min(iy1 - iy0, stitched_h - py))
            if pw > 0 and ph > 0:
                res.append([int(px), int(py), int(pw), int(ph)])
        return res
    except Exception:
        return []


def _load_summary_lookup(base_dir: str) -> Dict[str, Dict[str, Any]]:
    """读取 dom_summary_scrolled.json 或 dom_summary.json，构建 id→部分字段映射。

    返回形如:
      { 'd123': { 'bbox': [...], 'page_bbox': [...], 'visible': bool,
                  'visible_adv': bool|None, 'in_viewport': bool|None,
                  'occlusion_ratio': float|None } }
    读取失败或缺失时返回空映射。
    """
    import json as _json
    paths = [
        os.path.join(base_dir, 'dom_summary_scrolled.json'),
        os.path.join(base_dir, 'dom_summary.json'),
    ]
    for p in paths:
        try:
            if not os.path.exists(p):
                continue
            with open(p, 'r', encoding='utf-8') as f:
                doc = _json.load(f)
            els = doc.get('elements') or []
            out: Dict[str, Dict[str, Any]] = {}
            for e in els:
                try:
                    idx = int(e.get('index'))
                except Exception:
                    continue
                nid = f'd{idx}'
                bb = e.get('bbox') or [0, 0, 0, 0]
                pbb = e.get('page_bbox') or [0, 0, 0, 0]
                out[nid] = {
                    'bbox': bb,
                    'page_bbox': pbb,
                    'visible': bool(e.get('visible')) if e.get('visible') is not None else None,
                    'visible_adv': e.get('visible_adv'),
                    'in_viewport': e.get('in_viewport'),
                    'occlusion_ratio': e.get('occlusion_ratio'),
                }
            if out:
                return out
        except Exception:
            continue
    return {}


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


def _read_viewport_height(base_dir: str, fallback: int = 800) -> int:
    """读取 meta.json 中的 viewport.height，失败则回退 fallback。"""
    try:
        import json as _json
        p = os.path.join(base_dir, 'meta.json')
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                meta = _json.load(f)
            vh = int(((meta.get('viewport') or {}).get('height')) or fallback)
            return max(1, vh)
    except Exception:
        pass
    return max(1, int(fallback))


def draw_overlay(
    image_path: str,
    tree_path: str,
    out_path: str,
    *,
    min_thickness: int = 1,
    max_thickness: int = 6,
    alpha: int = 0,
    label: bool = False,
    mode: str = "viewport",  # viewport: 仅按 bbox 且限制在视口高度；page: 使用容器映射/页面绝对坐标
    only_visible: bool = False,
    filter_occluded: bool = True,
    occlusion_threshold: float = 0.98,
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

    # 运行模式：
    # - viewport：忽略容器映射与 page_bbox，仅用 bbox，并限制在视口高度内；
    # - page：保留原逻辑（容器映射优先，其次 page_bbox 回退）。
    base_dir = os.path.dirname(image_path)
    use_page_mode = str(mode or "viewport").lower() == "page"
    seg_map = _try_load_segments_map(base_dir, *img.size) if use_page_mode else None
    summary_map = _load_summary_lookup(base_dir) if (use_page_mode or (not use_page_mode)) else {}
    viewport_h = _read_viewport_height(base_dir, fallback=img.size[1]) if not use_page_mode else None
    viewport_h_page = _read_viewport_height(base_dir, fallback=img.size[1]) if use_page_mode else None

    for n in nodes:
        nid = n.get("id")
        g = n.get("geom", {})
        bbox = g.get("bbox") or [0, 0, 0, 0]
        page_bbox = g.get("page_bbox") or None
        # 可见性筛选：若开启，按 visible/visible_adv 过滤
        if only_visible and summary_map:
            sm = summary_map.get(str(nid)) or {}
            vis = sm.get('visible_adv') if sm.get('visible_adv') is not None else sm.get('visible')
            if vis is False:
                continue
        # 遮挡筛选：若开启，按 occlusion_ratio 过滤（高度遮挡的元素不绘制）
        if filter_occluded and summary_map:
            sm2 = summary_map.get(str(nid)) or {}
            try:
                occ = float(sm2.get('occlusion_ratio') or 0.0)
                if occ >= float(occlusion_threshold):
                    continue
            except Exception:
                pass
        rects: List[List[int]]
        if use_page_mode:
            if seg_map:
                rects = _project_bbox_to_stitched(bbox, seg_map, stitched_w=img.size[0], stitched_h=img.size[1])
            else:
                # 普通整页：优先使用 page_bbox（绝对坐标），若其无效则退回视口坐标/summary 回填
                x, y, w, h = bbox
                if page_bbox and all(isinstance(v, (int, float)) for v in page_bbox):
                    px, py, pw, ph = [int(v) for v in page_bbox]
                    if pw > 0 and ph > 0:
                        x, y, w, h = px, py, pw, ph
                if (w <= 0 or h <= 0) and summary_map:
                    sid = str(nid)
                    if sid in summary_map:
                        pbb = summary_map[sid].get('page_bbox') or [0, 0, 0, 0]
                        pbw, pbh = int(pbb[2] or 0), int(pbb[3] or 0)
                        if pbw > 0 and pbh > 0:
                            x, y, w, h = int(pbb[0] or 0), int(pbb[1] or 0), pbw, pbh
                        else:
                            bb2 = summary_map[sid].get('bbox') or [0, 0, 0, 0]
                            if int(bb2[2] or 0) > 0 and int(bb2[3] or 0) > 0:
                                x, y, w, h = int(bb2[0] or 0), int(bb2[1] or 0), int(bb2[2] or 0), int(bb2[3] or 0)
                # 额外兜底：fixed/sticky 元素在滚动时的 page_bbox 可能被叠加 scrollY，导致 y 过大。
                # 若其视口内的 bbox.y 很小（靠近顶部）而 page_bbox.y>>viewport 高度，则将 y 调整为 bbox.y。
                try:
                    if viewport_h_page and y > int(viewport_h_page * 1.5):
                        by = int(bbox[1] or 0)
                        if 0 <= by <= int(viewport_h_page * 0.25) and h <= int(viewport_h_page * 0.8):
                            y = by
                except Exception:
                    pass
                # 裁剪到画布范围
                x = max(0, min(int(x), img.size[0]))
                y = max(0, min(int(y), img.size[1]))
                w = max(0, min(int(w), img.size[0] - x))
                h = max(0, min(int(h), img.size[1] - y))
                rects = [[x, y, w, h]] if (w > 0 and h > 0) else []
        else:
            # 视口模式：仅用 bbox，并限制在视口高度内（避免全页坐标错位）
            x, y, w, h = [int(v or 0) for v in (bbox or [0, 0, 0, 0])]
            if viewport_h is not None and (y >= viewport_h or y + h <= 0):
                rects = []
            else:
                rects = [[x, y, w, h]] if (w > 0 and h > 0) else []

        if not rects:
            continue
        d = depth_map.get(nid, 0)
        color = _palette(d)
        width = _map_thickness(d, max_depth, min_thickness, max_thickness)

        first_rect = True
        for (x, y, w, h) in rects:
            if w <= 0 or h <= 0:
                continue
            # 轮廓
            draw.rectangle([x, y, x + w, y + h], outline=color + (255,), width=width)
            # 半透明填充（可选）
            if alpha > 0:
                fill = color + (max(0, min(255, alpha)),)
                draw.rectangle([x + 1, y + 1, x + w - 1, y + h - 1], fill=fill)
            # 标签（仅在第一段画一次）
            if first_rect and label and nid:
                tx, ty = x + 2, max(0, y - 10)
                draw.text((tx + 1, ty + 1), nid, font=font, fill=(0, 0, 0, 255))
                draw.text((tx, ty), nid, font=font, fill=(255, 255, 255, 255))
                first_rect = False

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(out_path)


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="在截图上根据控件树打框")
    p.add_argument("--dir", required=True, help="数据目录 data/<domain>/<ts>")
    p.add_argument("--image", default="screenshot_loaded.png", help="输入截图文件名")
    p.add_argument("--tree", default="controls_tree.json", help="控件树文件名")
    p.add_argument("--out", default=None, help="输出文件名（默认在输入名后加 _overlay.png）")
    p.add_argument("--mode", choices=["viewport", "page"], default="viewport", help="叠加模式：viewport=仅按视口 bbox 且限制视口高度；page=使用容器映射/页面坐标（可能轻微错位）")
    p.add_argument("--min-thickness", type=int, default=1)
    p.add_argument("--max-thickness", type=int, default=6)
    p.add_argument("--alpha", type=int, default=0, help="填充透明度 0~255，建议 0~128")
    p.add_argument("--label", action="store_true", help="是否绘制节点 id 标签")
    p.add_argument("--no-only-visible", dest="only_visible", action="store_false", help="关闭可见性筛选（默认关闭，仅按遮挡过滤）")
    p.add_argument("--no-filter-occluded", dest="filter_occluded", action="store_false", help="关闭遮挡筛选（默认关闭）")
    p.add_argument("--filter-occluded", dest="filter_occluded", action="store_true", help="开启遮挡筛选")
    p.add_argument("--occ-threshold", type=float, default=0.98, help="遮挡阈值，>=该值视为被挡住（启用遮挡筛选时生效）")
    p.set_defaults(only_visible=False, filter_occluded=False)
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
        mode=args.mode,
        only_visible=args.only_visible,
        filter_occluded=args.filter_occluded,
        occlusion_threshold=float(args.occ_threshold),
    )
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
