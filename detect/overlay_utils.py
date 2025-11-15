from __future__ import annotations

"""
overlay_utils

将 collect_playwright.py 中与 Overlay 生成相关的流程抽取为可复用函数：
 - generate_overlays: 统一生成 loaded/cropped/tail 三类 Overlay（容错，不抛异常）。
 - draw_loaded_overlay/draw_tail_overlay: 细粒度封装，直接调用 detect.overlay.draw_overlay。
 - write_cropped_by_tree: 基于控件树尾部内容高度 + 图像方差对 screenshot_loaded.png 进行裁剪，并生成裁剪图与 Overlay。

本模块只返回摘要信息，不抛异常，便于在主流程中简单调用并收集 warnings。
"""

import json
import os
from typing import Any, Dict, Optional

try:  # 优先包内相对导入
    from .constants import ARTIFACTS  # type: ignore
except Exception:  # 兼容脚本直接运行
    from constants import ARTIFACTS  # type: ignore

try:
    # 优先相对导入（包内）
    from .overlay import draw_overlay  # type: ignore
except Exception:  # pragma: no cover - 作为脚本运行时的退化导入
    from overlay import draw_overlay  # type: ignore


def draw_loaded_overlay(image_path: str, tree_path: str, out_path: str, *, mode: str = "page") -> bool:
    """为 loaded 截图生成 Overlay。

    返回是否生成成功（输入文件存在且未报错）。
    """
    try:
        if not (os.path.exists(image_path) and os.path.exists(tree_path)):
            return False
        _mode = mode if mode in ("viewport", "page") else "page"
        draw_overlay(
            image_path,
            tree_path,
            out_path,
            min_thickness=1,
            max_thickness=6,
            alpha=0,
            label=False,
            mode=_mode,
            only_visible=False,
            filter_occluded=False,
        )
        return os.path.exists(out_path)
    except Exception:
        return False


def draw_tail_overlay(image_path: str, tree_path: str, out_path: str, *, mode: str = "page") -> bool:
    """为 scrolled_tail 截图生成 Overlay（容错）。"""
    try:
        if not (os.path.exists(image_path) and os.path.exists(tree_path)):
            return False
        _mode = mode if mode in ("viewport", "page") else "page"
        draw_overlay(
            image_path,
            tree_path,
            out_path,
            min_thickness=1,
            max_thickness=6,
            alpha=0,
            label=False,
            mode=_mode,
            only_visible=False,
            filter_occluded=False,
        )
        return os.path.exists(out_path)
    except Exception:
        return False


def write_cropped_by_tree(
    out_dir: str,
    *,
    crop_margin_px: int = 200,
    crop_max_screens: Optional[int] = None,
    viewport_height: Optional[int] = None,
    mode: str = "page",
) -> Dict[str, Any]:
    """根据控件树最大 bottom + 图像底部方差剪去尾部空白。

    输入和输出文件位于 out_dir：
      - 输入：screenshot_loaded.png, controls_tree.json
      - 输出：screenshot_loaded_cropped.png, screenshot_loaded_cropped_overlay.png

    返回 { ok, cropped_path, overlay_path, target_height }（容错，不抛异常）。
    """
    from PIL import Image as _Img, ImageStat as _Stat  # 延迟导入，避免无 PIL 时影响其它路径

    img_in = os.path.join(out_dir, ARTIFACTS["screenshot_loaded"])
    tree_in = os.path.join(out_dir, ARTIFACTS["controls_tree"])
    cropped_path = os.path.join(out_dir, ARTIFACTS["screenshot_loaded_cropped"])
    cropped_overlay_path = os.path.join(out_dir, ARTIFACTS["screenshot_loaded_cropped_overlay"])

    summary: Dict[str, Any] = {"ok": False, "cropped_path": None, "overlay_path": None, "target_height": None}
    try:
        if not (os.path.exists(img_in) and os.path.exists(tree_in)):
            return summary
        with open(tree_in, "r", encoding="utf-8") as f:
            tree = json.load(f) or {}
        nodes = tree.get("nodes") or []
        max_bottom = 0
        for n in nodes:
            bb = (n.get("geom") or {}).get("bbox") or [0, 0, 0, 0]
            try:
                btm = int(bb[1] or 0) + int(bb[3] or 0)
            except Exception:
                btm = 0
            if btm > max_bottom:
                max_bottom = btm
        im = _Img.open(img_in).convert("RGB")
        h = im.height
        # 1) 基于节点 bottom 推导裁剪高度
        target_h = min(h, max(max_bottom + int(crop_margin_px), 1))
        # 2) 限制为最多 N 屏
        try:
            if crop_max_screens and crop_max_screens > 0 and viewport_height and viewport_height > 0:
                cap_h = int(crop_max_screens) * int(viewport_height)
                target_h = min(target_h, cap_h)
        except Exception:
            pass
        # 3) 底部方差启发式：从底部向上扫描，遇到有内容的区域即止
        try:
            gs = im.resize((im.width, max(1, im.height)), _Img.BILINEAR).convert("L")
            window = 16
            step = 8
            std_thresh = 6.0
            last_content_y = h - 1
            y = h - window
            while y > max(0, h - 2000):
                box = (0, max(0, y), im.width, min(h, y + window))
                crop = gs.crop(box)
                st = _Stat(crop)
                var = st.var[0] if st.var else 0.0
                if var > std_thresh:
                    last_content_y = y + window
                    break
                y -= step
            target_h = min(target_h, last_content_y + int(crop_margin_px))
        except Exception:
            pass
        if target_h < h:
            cropped = im.crop((0, 0, im.width, target_h))
            cropped.save(cropped_path)
            # 生成裁剪版 overlay
            _mode = mode if mode in ("viewport", "page") else "page"
            draw_overlay(cropped_path, tree_in, cropped_overlay_path, min_thickness=1, max_thickness=6, alpha=0, label=False, mode=_mode, only_visible=False, filter_occluded=False)
            summary.update({
                "ok": True,
                "cropped_path": cropped_path,
                "overlay_path": cropped_overlay_path,
                "target_height": int(target_h),
            })
            return summary
        else:
            # 无需裁剪
            summary.update({"ok": False, "target_height": int(target_h)})
            return summary
    except Exception:
        return summary


def generate_overlays(
    out_dir: str,
    *,
    overlay_mode_loaded: str = "auto",
    overlay_mode_tail: str = "auto",
    crop_trailing_blank: bool = True,
    crop_margin_px: int = 200,
    crop_max_screens: Optional[int] = None,
    viewport_height: Optional[int] = None,
) -> Dict[str, Any]:
    """统一生成 Overlay（容错）并返回摘要：
    {
      "loaded": true/false,
      "tail": true/false,
      "cropped": { ok, cropped_path, overlay_path, target_height } | None
    }
    """
    try:
        img_in = os.path.join(out_dir, ARTIFACTS["screenshot_loaded"])
        tree_in = os.path.join(out_dir, ARTIFACTS["controls_tree"])
        img_out = os.path.join(out_dir, ARTIFACTS["screenshot_loaded_overlay"])
        mode_loaded = overlay_mode_loaded if overlay_mode_loaded in ("viewport", "page") else "page"
        mode_tail = overlay_mode_tail if overlay_mode_tail in ("viewport", "page") else "page"
        loaded_ok = draw_loaded_overlay(img_in, tree_in, img_out, mode=mode_loaded)

        cropped_summary: Optional[Dict[str, Any]] = None
        if loaded_ok and crop_trailing_blank:
            cropped_summary = write_cropped_by_tree(
                out_dir,
                crop_margin_px=crop_margin_px,
                crop_max_screens=crop_max_screens,
                viewport_height=viewport_height,
                mode=mode_loaded,
            )

        tail_in = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"])
        tail_out = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail_overlay"])
        tail_ok = draw_tail_overlay(tail_in, tree_in, tail_out, mode=mode_tail) if os.path.exists(tail_in) else False
        return {"loaded": bool(loaded_ok), "tail": bool(tail_ok), "cropped": cropped_summary}
    except Exception:
        return {"loaded": False, "tail": False, "cropped": None}
