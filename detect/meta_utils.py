from __future__ import annotations

"""
meta_utils

抽取 meta.json 的采集与更新：
 - get_user_agent: 从 helper / navigator / context 选项多级回退获取 UA。
 - write_meta: 组装并写入 meta.json（容错）。
 - update_meta_artifacts: 运行结束后刷新 warnings 与关键产物存在性。

本模块不抛异常，调用方只需在必要时记录 warnings。
"""

import json
import os
import time
from typing import Any, Dict, Optional

try:  # 优先包内相对导入
    from .constants import ARTIFACTS, DEFAULT_VIEWPORT, DETECT_SPEC_VERSION  # type: ignore
    from .utils import write_json  # type: ignore
except Exception:  # 兼容脚本直接运行
    from constants import ARTIFACTS, DEFAULT_VIEWPORT, DETECT_SPEC_VERSION  # type: ignore
    from utils import write_json  # type: ignore
from urllib.parse import urlparse


def get_user_agent(page, context) -> str:
    """多级回退获取 UA（不抛异常）。"""
    try:
        try:
            ua = page.evaluate("() => window.DetectHelpers && window.DetectHelpers.getUserAgent && window.DetectHelpers.getUserAgent()") or ""
        except Exception:
            ua = ""
        if not ua:
            try:
                ua = page.evaluate("() => navigator.userAgent") or ""
            except Exception:
                ua = ""
        if not ua:
            try:
                ua = str((context._options or {}).get("userAgent") or "")  # type: ignore[attr-defined]
            except Exception:
                ua = ua or ""
        return ua or ""
    except Exception:
        return ""


def write_meta(
    out_dir: str,
    *,
    url: str,
    title: str,
    domain_key: str,
    ts: str,
    ua: str,
    viewport: Optional[Dict[str, Any]] = None,
    status: str = "ok",
    achieved_networkidle: bool = False,
    warnings: Optional[list] = None,
    device_name: Optional[str] = None,
    dpr: Optional[float] = None,
    started_epoch: Optional[float] = None,
) -> None:
    """写出 meta.json；容错，不抛异常。"""
    try:
        tz_offset_min = -time.timezone // 60 if (time.localtime().tm_isdst == 0) else -time.altzone // 60
        meta = {
            "url": url,
            "title": title or "",
            "domain": urlparse(url).netloc,
            "domain_sanitized": domain_key,
            "timestamp": ts,
            "tz_offset_minutes": tz_offset_min,
            "user_agent": ua or "",
            "viewport": viewport or DEFAULT_VIEWPORT,
            "detect_spec_version": DETECT_SPEC_VERSION,
            "tool": "playwright-python",
            "status": status,
            "achieved_networkidle": bool(achieved_networkidle),
            "warnings": warnings or [],
            "device_name": device_name,
            "device_scale_factor": dpr,
            "started_epoch": started_epoch,
            "finished_epoch": time.time(),
        }
        write_json(os.path.join(out_dir, ARTIFACTS["meta"]), meta)
    except Exception:
        # 最小容错：直接尝试写最简 JSON
        try:
            with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"url": url, "timestamp": ts, "status": status}, f)
        except Exception:
            pass


def update_meta_artifacts(out_dir: str, *, warnings: Optional[list] = None) -> None:
    """更新 meta：写回 warnings 与关键产物存在性（容错）。"""
    try:
        meta_path = os.path.join(out_dir, ARTIFACTS["meta"])
        meta_now = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_now = json.load(f) or {}
            except Exception:
                meta_now = {}
        meta_now.update({
            "warnings": warnings or [],
            "finished_epoch": time.time(),
            "artifacts_present": {
                "controls_tree": os.path.exists(os.path.join(out_dir, ARTIFACTS["controls_tree"])),
                "screenshot_loaded": os.path.exists(os.path.join(out_dir, ARTIFACTS["screenshot_loaded"])),
                "screenshot_loaded_overlay": os.path.exists(os.path.join(out_dir, ARTIFACTS["screenshot_loaded_overlay"])),
                "dom_summary": os.path.exists(os.path.join(out_dir, ARTIFACTS["dom_summary"])),
                "dom_summary_scrolled": os.path.exists(os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"])),
            },
        })
        write_json(meta_path, meta_now)
    except Exception:
        pass
