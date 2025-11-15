#!/usr/bin/env python3
"""
叠加可视化：在截图上绘制 top-N 置信候选（final_confidence > 阈值）。

优先读取 <dir>/AFC/candidates_scored.json；若不存在，则从
 candidates.json + evidence_text.json + dom_summary.json 动态计算置信度：
   final_conf = 0.6*candidate.score + 0.3*text_quality + 0.1*(1-occlusion)

输出：<dir>/afc_top_candidates_overlay.png（默认名，可自定义）。
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

from .utils_io import read_json


def _choose_image(dir_path: str, pref: str | None) -> str:
    if pref:
        p = os.path.join(dir_path, pref)
        if os.path.exists(p):
            return p
    for name in ("screenshot_loaded_cropped.png", "screenshot_loaded.png", "screenshot_scrolled_tail.png"):
        p = os.path.join(dir_path, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError("no base image found under dir")


def _to_box(v: List[int] | None) -> Tuple[int, int, int, int]:
    if not v or len(v) < 4:
        return (0, 0, 0, 0)
    try:
        x, y, w, h = int(v[0] or 0), int(v[1] or 0), int(v[2] or 0), int(v[3] or 0)
        return (x, y, w, h)
    except Exception:
        return (0, 0, 0, 0)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _compute_conf(c: Dict[str, Any], evid_extras: Dict[str, Any], dom_map: Dict[str, Any]) -> float:
    text_quality = float((evid_extras or {}).get("text_quality") or 0.0)
    c_score = float(c.get("score") or 0.0)
    occ = c.get("occlusion_ratio")
    if occ is None:
        occ = (dom_map.get(c.get("id")) or {}).get("occlusion_ratio")
    occ = float(occ) if isinstance(occ, (int, float)) else 0.0
    return _clamp01(0.6 * c_score + 0.3 * text_quality + 0.1 * (1.0 - float(occ)))


from .utils_io import ensure_dir


def overlay_top(dir_path: str, *, image_name: str | None = None, out_name: str | None = None,
                topn: int = 50, min_conf: float = 0.3, thickness: int = 3, alpha: int = 0,
                label: bool = True) -> str:
    base_img = _choose_image(dir_path, image_name)
    afc_dir = ensure_dir(os.path.join(dir_path, "AFC"))
    out_path = out_name or os.path.join(afc_dir, "afc_top_candidates_overlay.png")

    afc_dir = os.path.join(dir_path, "AFC")
    scored = read_json(os.path.join(afc_dir, "candidates_scored.json"))
    cands = scored.get("candidates") if isinstance(scored, dict) else None
    if not cands:
        cand_doc = read_json(os.path.join(afc_dir, "candidates.json"))
        evid_doc = read_json(os.path.join(afc_dir, "evidence_text.json"))
        dom_doc = read_json(os.path.join(dir_path, "dom_summary.json"))
        cands = cand_doc.get("candidates") or []
        extras = {e.get("id"): e for e in (evid_doc.get("extras") or [])}
        dom_map = {}
        for e in dom_doc.get("elements", []) or []:
            try:
                idx = int(e.get("index"))
                dom_map[f"d{idx}"] = e
            except Exception:
                continue
        # 动态计算 final_confidence
        for c in cands:
            e = extras.get(c.get("id")) or {}
            c["final_confidence"] = _compute_conf(c, e, dom_map)

    # 过滤/排序
    good = [c for c in (cands or []) if float(c.get("final_confidence") or 0.0) >= float(min_conf)]
    good.sort(key=lambda x: float(x.get("final_confidence") or 0.0), reverse=True)
    if topn and topn > 0:
        good = good[:topn]

    # 画图
    img = Image.open(base_img).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    def color(v: float) -> Tuple[int, int, int, int]:
        if v >= 0.9:
            return (64, 200, 80, 255)
        if v >= 0.75:
            return (255, 210, 60, 255)
        return (255, 140, 0, 255)

    for i, c in enumerate(good, 1):
        bb = _to_box(c.get("page_bbox"))
        if bb[2] <= 0 or bb[3] <= 0:
            bb = _to_box(c.get("bbox"))
        if bb[2] <= 0 or bb[3] <= 0:
            continue
        conf = float(c.get("final_confidence") or 0.0)
        col = color(conf)
        x, y, w, h = bb
        draw.rectangle([x, y, x + w, y + h], outline=col, width=max(1, int(thickness)))
        if alpha > 0:
            fill = (col[0], col[1], col[2], max(0, min(255, alpha)))
            draw.rectangle([x + 1, y + 1, x + w - 1, y + h - 1], fill=fill)
        if label and font:
            label_txt = f"{i}. {c.get('id')} {conf:.2f}"
            tx, ty = x + 2, max(0, y - 10)
            draw.text((tx + 1, ty + 1), label_txt, font=font, fill=(0, 0, 0, 255))
            draw.text((tx, ty), label_txt, font=font, fill=(255, 255, 255, 255))

    out = Image.alpha_composite(img, overlay).convert("RGB")
    out.save(out_path)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="在截图上绘制 top-N 置信候选")
    ap.add_argument("--dir", required=True, help="采集目录 data/<domain>/<ts>")
    ap.add_argument("--image", default=None, help="基图文件名（默认自动：loaded_cropped→loaded→scrolled_tail）")
    ap.add_argument("--out", default=None, help="输出文件名（默认 afc_top_candidates_overlay.png）")
    ap.add_argument("--topn", type=int, default=50)
    ap.add_argument("--min-conf", type=float, default=0.3)
    ap.add_argument("--thickness", type=int, default=3)
    ap.add_argument("--alpha", type=int, default=0)
    ap.add_argument("--no-label", dest="label", action="store_false")
    args = ap.parse_args()

    out = overlay_top(args.dir, image_name=args.image, out_name=args.out, topn=args.topn,
                      min_conf=args.min_conf, thickness=args.thickness, alpha=args.alpha,
                      label=args.label)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
