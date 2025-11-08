"""
Detect | Python + Playwright 采集脚本（含中文注释）

接口：
    collect(url: str, out_root: str = "data", timeout_ms: int = 45000, ...) -> str | dict

产物目录：data/<domain_sanitized>/<YYYYMMDDHHMMSS>/
    - screenshot_initial.png  （DOMContentLoaded 后，全页）
    - screenshot_loaded.png   （load(+networkidle) 后，全页）
    - screenshot_scrolled_tail.png （滚到底部时视口截图）
    - dom.html                （documentElement.outerHTML）
    - dom_summary.json        （滚动前 DOM 简表）
    - dom_summary_scrolled.json（滚动后 DOM 简表）
    - dom_scrolled_new.json   （滚动后新增元素近似集合）
    - ax.json                 （可访问性树快照）
    - meta.json               （元信息：URL/UA/viewport/状态/版本等）
    - timings.json            （Navigation Timing v2/legacy）

说明：
    - 该脚本演示实现，已尽可能解耦：错误类型/工具函数/上下文参数构造/滚动逻辑
      分别封装于 detect/errors.py, detect/utils.py, detect/context_utils.py, detect/scrolling.py。
    - 需要浏览器内核：`python -m playwright install chromium`。
"""

from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any, Dict
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    from .errors import CollectError  # type: ignore
    from .utils import (  # type: ignore
        sanitize_domain,
        timestamp_yyyymmddhhmmss,
        ensure_unique_dir,
        write_json,
        validate_url,
        parse_viewport,
    )
    from .constants import DEFAULT_VIEWPORT, DETECT_SPEC_VERSION, ARTIFACTS  # type: ignore
    from .context_utils import make_context_args  # type: ignore
    from .scrolling import auto_scroll_full_page as _auto_scroll_full_page  # type: ignore
    from .controls_tree import write_controls_tree  # type: ignore
    from .overlay import draw_overlay  # type: ignore
    from .icon_patches import generate_icon_patches  # type: ignore
except Exception:
    from errors import CollectError  # type: ignore
    from utils import (  # type: ignore
        sanitize_domain,
        timestamp_yyyymmddhhmmss,
        ensure_unique_dir,
        write_json,
        validate_url,
        parse_viewport,
    )
    from constants import DEFAULT_VIEWPORT, DETECT_SPEC_VERSION, ARTIFACTS  # type: ignore
    from context_utils import make_context_args  # type: ignore
    from scrolling import auto_scroll_full_page as _auto_scroll_full_page  # type: ignore
    from controls_tree import write_controls_tree  # type: ignore
    from overlay import draw_overlay  # type: ignore
    from icon_patches import generate_icon_patches  # type: ignore

JS_HELPERS_FILE = os.path.join(os.path.dirname(__file__), "collect_playwright.js")


# 以上工具与异常等已抽离到独立模块，减少与采集主流程的耦合。


def collect(
    url: str,
    out_root: str = "workspace/data",
    timeout_ms: int = 45000,
    *,
    raise_on_error: bool = False,
    auto_scroll_before_loaded_shot: bool = True,
    autoscroll_max_steps: int = 50,
    autoscroll_delay_ms: int = 200,
    device: str | None = None,
    viewport: str | tuple[int, int] | None = None,
    dpr: float | None = None,
    return_info: bool = False,
    enable_container_stitch: bool = True,
    max_stitch_segments: int = 30,
    max_stitch_seconds: int = 10,
    enable_overlay: bool = True,
    max_stitch_pixels: int = 40000000,
) -> str | Dict[str, Any]:
    """
    Collect page artifacts using Playwright and save under data/<domain>/<timestamp>/.

    Returns the final directory path.
    """
    # 基本输出目录与时间戳
    started_epoch = time.time()
    ts = timestamp_yyyymmddhhmmss()
    domain_key = sanitize_domain(url)
    base_dir = os.path.join(out_root, domain_key, ts)
    out_dir = ensure_unique_dir(base_dir)

    status = "ok"
    achieved_networkidle = False
    autos_reached_bottom = None
    warnings: list[dict[str, Any]] = []
    error_code = None
    error_stage = None

    # Stash device/viewport args on function for context creation
    v_tuple = parse_viewport(viewport)
    
    # 提前校验 URL
    try:
        validate_url(url)
    except CollectError as ce:
        # Write minimal meta then rethrow or return
        os.makedirs(out_dir, exist_ok=True)
        write_json(os.path.join(out_dir, "meta.json"), {
            "url": url,
            "domain": urlparse(url).netloc,
            "domain_sanitized": domain_key,
            "timestamp": ts,
            "detect_spec_version": DETECT_SPEC_VERSION,
            "tool": "playwright-python",
            "status": "failed",
            "error_code": ce.code,
            "error_stage": ce.stage,
            "error": ce.message,
            "started_epoch": started_epoch,
            "finished_epoch": time.time(),
        })
        if raise_on_error:
            raise CollectError(ce.code, ce.stage, ce.message, out_dir) from None
        if return_info:
            return {
                "url": url,
                "domain": urlparse(url).netloc,
                "domain_sanitized": domain_key,
                "timestamp": ts,
                "out_dir": out_dir,
                "status": "failed",
                "error_code": ce.code,
                "error_stage": ce.stage,
                "params": {
                    "out_root": out_root,
                    "timeout_ms": timeout_ms,
                    "auto_scroll_before_loaded_shot": auto_scroll_before_loaded_shot,
                    "autoscroll_max_steps": autoscroll_max_steps,
                    "autoscroll_delay_ms": autoscroll_delay_ms,
                    "device": device,
                    "viewport": v_tuple,
                    "dpr": dpr,
                },
                "artifacts": ARTIFACTS,
            }
        return out_dir

    browser = None
    context = None
    page = None
    try:
        with sync_playwright() as pw:
            try:
                # 由工具函数生成上下文参数，降低与 Playwright 设备描述的耦合
                context_args = make_context_args(
                    pw,
                    device_name=device,
                    viewport_tuple=v_tuple,
                    dpr=dpr,
                    default_viewport=DEFAULT_VIEWPORT,
                    warnings=warnings,
                )
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(**context_args)
                page = context.new_page()
            except Exception as se:
                error_code = "LAUNCH_ERROR"
                error_stage = "launch"
                raise

            # Navigate and wait for DOMContentLoaded
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as te:
                error_code = "NAV_TIMEOUT"
                error_stage = "navigate"
                raise
            except Exception as ne:
                error_code = "NAV_ERROR"
                error_stage = "navigate"
                raise

            # screenshot_initial.png — taken right after DOMContentLoaded
            try:
                page.screenshot(path=os.path.join(out_dir, "screenshot_initial.png"), full_page=True)
            except Exception as ee:
                warnings.append({"code": "SCREENSHOT_INITIAL_ERROR", "stage": "screenshot_initial", "error": str(ee)})

            # Inject helper JS (functions in collect_playwright.js)
            injected_helpers = False
            try:
                if os.path.exists(JS_HELPERS_FILE):
                    page.add_script_tag(path=JS_HELPERS_FILE)
                    injected_helpers = True
                else:
                    warnings.append({"code": "INJECT_JS_MISSING", "stage": "inject_js", "path": JS_HELPERS_FILE})
            except Exception as je:
                warnings.append({"code": "INJECT_JS_ERROR", "stage": "inject_js", "error": str(je)})

            # dom.html — full page HTML (outerHTML)
            try:
                html = page.content()
            except Exception as he:
                html = ""
                warnings.append({"code": "DOM_HTML_ERROR", "stage": "dom", "error": str(he)})
            try:
                with open(os.path.join(out_dir, ARTIFACTS["dom_html"]), "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception as we:
                warnings.append({"code": "DOM_HTML_WRITE_ERROR", "stage": "dom", "error": str(we)})

            # ax.json — full accessibility snapshot (interesting_only=False)
            try:
                ax = page.accessibility.snapshot(interesting_only=False)
            except Exception as ae:
                ax = {}
                warnings.append({"code": "AX_SNAPSHOT_ERROR", "stage": "ax", "error": str(ae)})
            try:
                write_json(os.path.join(out_dir, ARTIFACTS["ax"]), ax or {})
            except Exception as we:
                warnings.append({"code": "AX_WRITE_ERROR", "stage": "ax", "error": str(we)})

            # dom_summary.json — lightweight DOM table via helper JS (advanced)
            try:
                if injected_helpers:
                    dom_summary = page.evaluate(
                        "(p) => window.DetectHelpers.getDomSummaryAdvanced(p.limit, p.opts)",
                        {"limit": 20000, "opts": {"occlusionStep": 8}},
                    )
                else:
                    dom_summary = page.evaluate("Array.from(document.querySelectorAll('*')).slice(0, 500).map((e,i)=>({index:i,tag:(e.tagName||'').toLowerCase(),id:e.id||null}))")
            except Exception as de:
                dom_summary = []
                warnings.append({"code": "DOM_SUMMARY_ERROR", "stage": "dom_summary", "error": str(de)})
            try:
                write_json(os.path.join(out_dir, ARTIFACTS["dom_summary"]), {
                    "count": len(dom_summary) if isinstance(dom_summary, list) else 0,
                    "viewport": DEFAULT_VIEWPORT,
                    "elements": dom_summary,
                })
            except Exception as we:
                warnings.append({"code": "DOM_SUMMARY_WRITE_ERROR", "stage": "dom_summary", "error": str(we)})

            # Wait for load + optionally networkidle for a "fully loaded" state
            try:
                page.wait_for_load_state("load", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                    achieved_networkidle = True
                except PlaywrightTimeoutError:
                    achieved_networkidle = False
            except Exception as le:
                warnings.append({"code": "LOAD_STATE_ERROR", "stage": "load_state", "error": str(le)})

            # Measure document metrics before scrolling
            try:
                doc_before = page.evaluate("() => window.DetectHelpers.getDocMetrics()") if injected_helpers else {"scrollHeight": None, "clientHeight": None}
            except Exception:
                doc_before = {"scrollHeight": None, "clientHeight": None}

            # Auto-scroll to bottom to trigger lazy-loaded content before the 'loaded' screenshot
            if auto_scroll_before_loaded_shot:
                try:
                    reached_bottom = _auto_scroll_full_page(
                        page,
                        max_steps=autoscroll_max_steps,
                        delay_ms=autoscroll_delay_ms,
                    )
                    autos_reached_bottom = bool(reached_bottom)
                    if not reached_bottom:
                        warnings.append({"code": "AUTOSCROLL_CAP_REACHED", "stage": "autosupport", "info": {"max_steps": autoscroll_max_steps}})
                    # After scrolling, a short idle wait can help late resources settle
                    try:
                        page.wait_for_load_state("networkidle", timeout=3000)
                    except PlaywrightTimeoutError:
                        pass
                except Exception as se:
                    warnings.append({"code": "AUTOSCROLL_ERROR", "stage": "autosupport", "error": str(se)})

            # Re-measure document metrics after scrolling
            try:
                doc_after = page.evaluate("() => window.DetectHelpers.getDocMetrics()") if injected_helpers else {"scrollHeight": None, "clientHeight": None}
            except Exception:
                doc_after = {"scrollHeight": None, "clientHeight": None}

            # 容器感知 + 限额：优先拼接容器整图，否则退回 fullPage；并输出若干局部片段
            container_info = None
            stitched_ok = False
            seg_meta: list[dict[str, int]] = []
            try:
                # 限额常量（可按需微调）
                MAX_SEGMENTS = 20
                MAX_SECONDS = 8
                PIXEL_CAP = 25_000_000  # 约 25MP

                if injected_helpers:
                    container_info = page.evaluate("() => window.DetectHelpers.findMainScrollContainer()")
                if container_info and container_info.get("selector") and (container_info.get("scrollHeight", 0) > container_info.get("clientHeight", 0) + 50):
                    sel = container_info["selector"]
                    el = page.query_selector(sel)
                    if el:
                        # 回到顶部
                        try:
                            page.evaluate("(s)=>window.DetectHelpers.scrollContainerTo(s,0)", sel)
                        except Exception:
                            pass
                        from io import BytesIO
                        from PIL import Image
                        import time as _t
                        segs = []
                        max_width = 0
                        step_px = int(min(max(200, (context_args.get("viewport", {}).get("height", 800) * 0.9)), 1600))
                        metrics = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", sel) or {}
                        sh = int(metrics.get("scrollHeight") or container_info.get("scrollHeight") or 0)
                        ch = int(metrics.get("clientHeight") or container_info.get("clientHeight") or 0)
                        if sh > 0 and ch > 0:
                            last_top = -1
                            t0 = _t.monotonic()
                            for i in range(MAX_SEGMENTS):
                                if _t.monotonic() - t0 > MAX_SECONDS:
                                    warnings.append({"code": "CONTAINER_STITCH_LIMIT", "stage": "container_capture", "info": {"reason": "time_limit", "seconds": MAX_SECONDS}})
                                    break
                                try:
                                    # 目标位置：基于上次 top 累进，避免跳跃过大
                                    met_prev = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", sel) or {}
                                    prev_top = int(met_prev.get("scrollTop", 0))
                                    desired = min(max(0, sh - ch), prev_top + step_px)
                                    page.evaluate("(s,t)=>window.DetectHelpers.scrollContainerTo(s,t)", sel, desired)
                                    # 等待滚动完成或超时（提升准确度）
                                    try:
                                        page.wait_for_function(
                                            "(s,t)=>{const e=document.querySelector(s); return e && Math.abs((e.scrollTop||0)-t) < 2}",
                                            sel,
                                            desired,
                                            timeout=1000,
                                        )
                                    except Exception:
                                        pass
                                    # 兜底：若未推进，尝试鼠标滚轮与键盘 PageDown
                                    try:
                                        met_chk = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", sel) or {}
                                        chk_top = int(met_chk.get("scrollTop", 0))
                                        if abs(chk_top - prev_top) < 2 and chk_top < sh - ch - 2:
                                            # hover 到容器中心
                                            try:
                                                bbox = el.bounding_box()
                                                if bbox:
                                                    page.mouse.move(bbox["x"] + bbox["width"]/2, bbox["y"] + 10)
                                            except Exception:
                                                pass
                                            # 鼠标滚轮
                                            try:
                                                page.mouse.wheel(0, step_px)
                                                page.wait_for_timeout(200)
                                            except Exception:
                                                pass
                                            met_w = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", sel) or {}
                                            w_top = int(met_w.get("scrollTop", 0))
                                            if abs(w_top - prev_top) < 2 and w_top < sh - ch - 2:
                                                # 键盘 PageDown
                                                try:
                                                    page.keyboard.press("PageDown")
                                                    page.wait_for_timeout(200)
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                                buf = el.screenshot(type="png")
                                im = Image.open(BytesIO(buf)).convert("RGB")
                                met = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", sel) or {}
                                top = int(met.get("scrollTop", 0))
                                overlap = max(0, (last_top + ch) - top) if last_top >= 0 else 0
                                use_h = im.height - overlap
                                if use_h <= 0:
                                    break
                                cropped = im.crop((0, overlap, im.width, overlap + use_h))
                                segs.append(cropped)
                                seg_meta.append({"scrollTop": top, "height": int(cropped.height), "width": int(cropped.width)})
                                max_width = max(max_width, im.width)
                                last_top = top
                                if top + ch >= sh - 2:
                                    break
                            if segs:
                                total_h = sum(s.height for s in segs)
                                if PIXEL_CAP and max_width * total_h > PIXEL_CAP:
                                    cap_h = max(1, PIXEL_CAP // max(1, max_width))
                                    warnings.append({"code": "CONTAINER_STITCH_LIMIT", "stage": "container_capture", "info": {"reason": "pixel_limit", "cap_height": cap_h}})
                                    acc = 0
                                    clipped = []
                                    for s in segs:
                                        if acc >= cap_h:
                                            break
                                        take = min(s.height, cap_h - acc)
                                        clipped.append(s.crop((0, 0, s.width, take)))
                                        acc += take
                                    segs = clipped
                                    total_h = acc
                                from PIL import Image as _Img
                                stitched = _Img.new("RGB", (max_width, total_h), (255, 255, 255))
                                y = 0
                                for s in segs:
                                    stitched.paste(s, (0, y))
                                    y += s.height
                                stitched.save(os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"]), format="PNG", optimize=True)
                                stitched_ok = True
                                # 保存若干局部图（最多 6 张）：顶部、中部、底部 + 额外均匀采样
                                try:
                                    seg_dir = os.path.join(out_dir, ARTIFACTS["segments_dir"])  # segments/
                                    os.makedirs(seg_dir, exist_ok=True)
                                    picks = []
                                    n = len(segs)
                                    if n:
                                        picks.append(0)
                                        if n > 2:
                                            picks.append(n//2)
                                        if n > 1:
                                            picks.append(n-1)
                                        # 均匀补充至最多 6 张
                                        i = 1
                                        while len(picks) < min(6, n) and i < n-1:
                                            if i not in picks:
                                                picks.append(i)
                                            i += max(1, n//6)
                                        picks = sorted(set(picks))
                                        meta_list = []
                                        for idx in picks:
                                            fn = f"seg_{idx:03d}.png"
                                            seg_path = os.path.join(seg_dir, fn)
                                            segs[idx].save(seg_path, format="PNG", optimize=True)
                                            m = seg_meta[idx] if idx < len(seg_meta) else {"scrollTop": 0, "height": int(segs[idx].height), "width": int(segs[idx].width)}
                                            m.update({"file": os.path.join(ARTIFACTS["segments_dir"], fn)})
                                            meta_list.append(m)
                                        # 若不足 3 张，改用整页图均匀切成 3 份补齐
                                        if len(meta_list) < 3:
                                            try:
                                                from PIL import Image as _Img
                                                stitched_fp = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"])
                                                im_all = _Img.open(stitched_fp).convert("RGB")
                                                meta_list = []
                                                thirds = [0, im_all.height//2, max(0, im_all.height-1)]
                                                names = ["seg_u_000.png", "seg_u_001.png", "seg_u_002.png"]
                                                for j, y in enumerate(thirds):
                                                    h = max(1, im_all.height//3)
                                                    patch = im_all.crop((0, y, im_all.width, min(im_all.height, y+h)))
                                                    fn = names[j]
                                                    patch.save(os.path.join(seg_dir, fn), format="PNG", optimize=True)
                                                    meta_list.append({"file": os.path.join(ARTIFACTS["segments_dir"], fn), "scrollTop": int(y), "height": int(patch.height), "width": int(patch.width)})
                                            except Exception:
                                                pass
                                        # 写索引
                                        from .utils import write_json as _wjson  # type: ignore
                                        _wjson(os.path.join(out_dir, ARTIFACTS["segments_meta"]), {"segments": meta_list})
                                except Exception as se_save:
                                    warnings.append({"code": "SEGMENTS_SAVE_ERROR", "stage": "segments", "error": str(se_save)})
            except Exception as ce:
                warnings.append({"code": "CONTAINER_STITCH_ERROR", "stage": "container_capture", "error": str(ce)})

            if not stitched_ok:
                try:
                    page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"]), full_page=True)
                    # fallback 情况下也生成若干均匀切片
                    try:
                        from PIL import Image as _Img
                        fp = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"])
                        im = _Img.open(fp).convert("RGB")
                        vw = context_args.get("viewport", {}).get("height", 800) or 800
                        seg_h = max(200, int(vw))
                        seg_dir = os.path.join(out_dir, ARTIFACTS["segments_dir"])  # segments/
                        os.makedirs(seg_dir, exist_ok=True)
                        meta_list = []
                        y = 0
                        idx = 0
                        while y < im.height and idx < 6:
                            h = min(seg_h, im.height - y)
                            patch = im.crop((0, y, im.width, y + h))
                            fn = f"seg_{idx:03d}.png"
                            patch.save(os.path.join(seg_dir, fn), format="PNG", optimize=True)
                            meta_list.append({"file": os.path.join(ARTIFACTS["segments_dir"], fn), "scrollTop": y, "height": int(h), "width": int(im.width)})
                            y += h
                            idx += 1
                        # 若不足 3 张，改为均匀切三份
                        if len(meta_list) < 3:
                            meta_list = []
                            thirds = [0, im.height//2, max(0, im.height-1)]
                            names = ["seg_u_000.png", "seg_u_001.png", "seg_u_002.png"]
                            for j, y in enumerate(thirds):
                                h = max(1, im.height//3)
                                patch = im.crop((0, y, im.width, min(im.height, y+h)))
                                fn = names[j]
                                patch.save(os.path.join(seg_dir, fn), format="PNG", optimize=True)
                                meta_list.append({"file": os.path.join(ARTIFACTS["segments_dir"], fn), "scrollTop": int(y), "height": int(patch.height), "width": int(patch.width)})
                        from .utils import write_json as _wjson  # type: ignore
                        _wjson(os.path.join(out_dir, ARTIFACTS["segments_meta"]), {"segments": meta_list})
                    except Exception as se2:
                        warnings.append({"code": "SEGMENTS_FALLBACK_ERROR", "stage": "segments", "error": str(se2)})
                except Exception as ee:
                    warnings.append({"code": "SCREENSHOT_TAIL_ERROR", "stage": "screenshot_tail", "error": str(ee)})

            # screenshot_loaded.png — after load (+ networkidle if achieved)
            try:
                page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_loaded"]), full_page=True)
            except Exception as ee:
                warnings.append({"code": "SCREENSHOT_LOADED_ERROR", "stage": "screenshot_loaded", "error": str(ee)})

            # timings.json — Navigation Timing via helper
            try:
                if injected_helpers:
                    nav_timing = page.evaluate("() => window.DetectHelpers.getNavigationTiming()")
                else:
                    nav_timing = page.evaluate("() => (performance.getEntriesByType('navigation')[0]?.toJSON?.() || performance.getEntriesByType('navigation')[0] || performance.timing || {})")
            except Exception as te:
                nav_timing = {}
                warnings.append({"code": "TIMINGS_ERROR", "stage": "timings", "error": str(te)})
            try:
                write_json(os.path.join(out_dir, ARTIFACTS["timings"]), nav_timing or {})
            except Exception as we:
                warnings.append({"code": "TIMINGS_WRITE_ERROR", "stage": "timings", "error": str(we)})

            # Recompute DOM summary after scroll to capture newly loaded elements (advanced)
            new_count = None
            try:
                if injected_helpers:
                    dom_summary_scrolled = page.evaluate(
                        "(p) => window.DetectHelpers.getDomSummaryAdvanced(p.limit, p.opts)",
                        {"limit": 20000, "opts": {"occlusionStep": 8}},
                    )
                else:
                    dom_summary_scrolled = []
                write_json(os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"]), {
                    "count": len(dom_summary_scrolled) if isinstance(dom_summary_scrolled, list) else 0,
                    "viewport": DEFAULT_VIEWPORT,
                    "elements": dom_summary_scrolled,
                })
                # Compute new elements compared to initial dom_summary
                try:
                    def fp(e: Dict[str, Any]) -> str:
                        def s(v):
                            return "" if v is None else str(v)
                        bb = e.get("bbox") or [0,0,0,0]
                        return "|".join([
                            s(e.get("tag")), s(e.get("id")), s(e.get("class")), s(e.get("role")), s(e.get("name")),
                            (e.get("text") or "")[:80], f"{bb[0]}-{bb[1]}-{bb[2]}-{bb[3]}"
                        ])
                    base_set = set(fp(x) for x in (dom_summary or []))
                    scrolled_set = set(fp(x) for x in (dom_summary_scrolled or []))
                    only_scrolled = [e for e in (dom_summary_scrolled or []) if fp(e) not in base_set]
                    new_count = len(only_scrolled)
                    write_json(os.path.join(out_dir, ARTIFACTS["dom_scrolled_new"]), {
                        "initial_count": len(dom_summary or []),
                        "scrolled_count": len(dom_summary_scrolled or []),
                        "new_count": new_count,
                        "new_elements": only_scrolled,
                    })
                except Exception as de:
                    warnings.append({"code": "DOM_SCROLL_DIFF_ERROR", "stage": "dom_diff", "error": str(de)})
            except Exception as se:
                warnings.append({"code": "DOM_SUMMARY_SCROLLED_ERROR", "stage": "dom_summary_scrolled", "error": str(se)})

            # Persist scroll info summary
            try:
                info_obj = {
                    "before": doc_before,
                    "after": doc_after,
                    "auto_scroll_reached_bottom": autos_reached_bottom,
                    "achieved_networkidle": achieved_networkidle,
                    "new_elements_count": new_count,
                }
                if container_info:
                    info_obj["container"] = container_info
                write_json(os.path.join(out_dir, ARTIFACTS["scroll_info"]), info_obj)
            except Exception as we:
                warnings.append({"code": "SCROLL_INFO_WRITE_ERROR", "stage": "scroll_info", "error": str(we)})

            # meta.json — URL, domain, viewport, UA, tz, status, versions
            try:
                ua = (page.evaluate("() => window.DetectHelpers.getUserAgent()") if injected_helpers else page.evaluate("() => navigator.userAgent")) or ""
            except Exception:
                ua = ""
                warnings.append({"code": "UA_ERROR", "stage": "meta", "error": "navigator.userAgent unavailable"})
            try:
                title = page.title() or ""
            except Exception:
                title = ""
            tz_offset_min = -time.timezone // 60 if (time.localtime().tm_isdst == 0) else -time.altzone // 60
            meta = {
                "url": url,
                "title": title,
                "domain": urlparse(url).netloc,
                "domain_sanitized": domain_key,
                "timestamp": ts,
                "tz_offset_minutes": tz_offset_min,
                "user_agent": ua,
                "viewport": context_args.get("viewport", DEFAULT_VIEWPORT),
                "detect_spec_version": DETECT_SPEC_VERSION,
                "tool": "playwright-python",
                "status": "ok",
                "achieved_networkidle": achieved_networkidle,
                "warnings": warnings,
                "device_name": device,
                "device_scale_factor": dpr,
                "started_epoch": started_epoch,
                "finished_epoch": time.time(),
            }
            write_json(os.path.join(out_dir, ARTIFACTS["meta"]), meta)

            # controls_tree.json — 极简控件树（默认启用）
            try:
                controls_out = os.path.join(out_dir, ARTIFACTS["controls_tree"])
                elements_for_tree = None
                # 优先从落地文件读取（解耦内存变量）
                try:
                    p_scrolled = os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"])
                    if os.path.exists(p_scrolled):
                        with open(p_scrolled, "r", encoding="utf-8") as f:
                            doc = json.load(f)
                            elements_for_tree = doc.get("elements")
                except Exception:
                    elements_for_tree = None
                if not elements_for_tree:
                    try:
                        p_base = os.path.join(out_dir, ARTIFACTS["dom_summary"])
                        if os.path.exists(p_base):
                            with open(p_base, "r", encoding="utf-8") as f:
                                doc = json.load(f)
                                elements_for_tree = doc.get("elements")
                    except Exception:
                        elements_for_tree = None
                # 仍不可得则回退到内存变量
                if not elements_for_tree:
                    elements_for_tree = dom_summary_scrolled if (isinstance(locals().get("dom_summary_scrolled"), list) and locals().get("dom_summary_scrolled")) else dom_summary
                if not elements_for_tree:
                    raise RuntimeError("no elements available for controls tree")
                write_controls_tree(elements_for_tree, controls_out)
            except Exception as ce:
                warnings.append({"code": "CONTROLS_TREE_ERROR", "stage": "controls_tree", "error": str(ce)})

            # icons/ — 基于控件 bbox 的贴图裁剪（启发式）
            try:
                # 以滚动后的整页图为基图（覆盖更广）
                generate_icon_patches(
                    out_dir,
                    tree_file=ARTIFACTS["controls_tree"],
                    screenshot_file=ARTIFACTS["screenshot_scrolled_tail"],
                    icons_subdir="icons",
                )
                # 可选：并行生成一套基于 loaded 的贴图，便于对比（不影响主目录）
                try:
                    generate_icon_patches(
                        out_dir,
                        tree_file=ARTIFACTS["controls_tree"],
                        screenshot_file=ARTIFACTS["screenshot_loaded"],
                        icons_subdir="icons_loaded",
                    )
                except Exception:
                    pass
            except Exception as ie:
                warnings.append({"code": "ICON_PATCH_ERROR", "stage": "icons", "error": str(ie)})

            # 自动生成 Overlay 截图（loaded）
            try:
                if enable_overlay:
                    img_in = os.path.join(out_dir, ARTIFACTS["screenshot_loaded"])
                    tree_in = os.path.join(out_dir, ARTIFACTS["controls_tree"])
                    img_out = os.path.join(out_dir, ARTIFACTS["screenshot_loaded_overlay"])
                    if os.path.exists(img_in) and os.path.exists(tree_in):
                        draw_overlay(img_in, tree_in, img_out, min_thickness=1, max_thickness=6, alpha=0, label=False)
                    else:
                        missing = []
                        if not os.path.exists(img_in):
                            missing.append("screenshot_loaded.png")
                        if not os.path.exists(tree_in):
                            missing.append("controls_tree.json")
                        raise RuntimeError(f"missing inputs: {', '.join(missing)}")
            except Exception as oe:
                warnings.append({"code": "OVERLAY_ERROR", "stage": "overlay", "error": str(oe)})

    except Exception as e:
        # Fatal error occurred (e.g., launch/navigation); write failure meta.
        status = "failed"
        msg = f"{type(e).__name__}: {e}"
        if error_code is None:
            # Fallback generic code
            error_code = "UNEXPECTED_ERROR"
            error_stage = error_stage or "unknown"
        os.makedirs(out_dir, exist_ok=True)
        write_json(os.path.join(out_dir, ARTIFACTS["meta"]), {
            "url": url,
            "domain": urlparse(url).netloc,
            "domain_sanitized": domain_key,
            "timestamp": ts,
            "detect_spec_version": DETECT_SPEC_VERSION,
            "tool": "playwright-python",
            "status": status,
            "error_code": error_code,
            "error_stage": error_stage,
            "error": msg,
            "traceback": traceback.format_exc(),
            "started_epoch": started_epoch,
            "finished_epoch": time.time(),
        })
        if raise_on_error:
            raise CollectError(error_code, error_stage or "unknown", msg, out_dir, e)
        if return_info:
            return {
                "url": url,
                "domain": urlparse(url).netloc,
                "domain_sanitized": domain_key,
                "timestamp": ts,
                "out_dir": out_dir,
                "status": status,
                "error_code": error_code,
                "error_stage": error_stage,
                "params": {
                    "out_root": out_root,
                    "timeout_ms": timeout_ms,
                    "auto_scroll_before_loaded_shot": auto_scroll_before_loaded_shot,
                    "autoscroll_max_steps": autoscroll_max_steps,
                    "autoscroll_delay_ms": autoscroll_delay_ms,
                    "device": device,
                    "viewport": v_tuple,
                    "dpr": dpr,
                },
                "artifacts": ARTIFACTS,
            }
    finally:
        try:
            if page is not None:
                page.close()
        except Exception:
            pass
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
    if return_info:
        return {
            "url": url,
            "domain": urlparse(url).netloc,
            "domain_sanitized": domain_key,
            "timestamp": ts,
            "out_dir": out_dir,
            "status": "ok",
            "achieved_networkidle": achieved_networkidle,
            "auto_scroll_reached_bottom": autos_reached_bottom,
            "params": {
                "out_root": out_root,
                "timeout_ms": timeout_ms,
                "auto_scroll_before_loaded_shot": auto_scroll_before_loaded_shot,
                "autoscroll_max_steps": autoscroll_max_steps,
                "autoscroll_delay_ms": autoscroll_delay_ms,
                "device": device,
                "viewport": v_tuple,
                "dpr": dpr,
            },
            "artifacts": ARTIFACTS,
        }
    return out_dir


def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Collect page artifacts using Playwright.")
    p.add_argument("url", help="Target URL, e.g. https://www.baidu.com")
    p.add_argument("--out-root", default="workspace/data", help="Output root directory (default: workspace/data)")
    p.add_argument("--timeout-ms", type=int, default=45000, help="Navigation/collection timeout in ms")
    p.add_argument("--raise-on-error", action="store_true", help="Raise CollectError on fatal errors")
    p.add_argument("--no-auto-scroll", dest="auto_scroll", action="store_false", help="Disable auto scroll before loaded screenshot")
    p.add_argument("--autoscroll-max-steps", type=int, default=50, help="Max steps for auto scroll (default: 50)")
    p.add_argument("--autoscroll-delay-ms", type=int, default=200, help="Delay between scroll steps in ms (default: 200)")
    p.add_argument("--device", type=str, default=None, help="Playwright built-in device name (e.g., 'iPhone 12 Pro')")
    p.add_argument("--viewport", type=str, default=None, help="Custom viewport as 'WIDTHxHEIGHT' (e.g., 1280x800)")
    p.add_argument("--dpr", type=float, default=None, help="Device scale factor (device pixel ratio)")
    p.add_argument("--return-info", action="store_true", help="Return a JSON object with locating info instead of just path")
    args = p.parse_args()
    result = collect(
        args.url,
        args.out_root,
        args.timeout_ms,
        raise_on_error=args.raise_on_error,
        auto_scroll_before_loaded_shot=args.auto_scroll,
        autoscroll_max_steps=args.autoscroll_max_steps,
        autoscroll_delay_ms=args.autoscroll_delay_ms,
        device=args.device,
        viewport=args.viewport,
        dpr=args.dpr,
        return_info=args.return_info,
    )
    if args.return_info:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
