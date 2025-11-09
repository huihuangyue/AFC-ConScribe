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
        load_json_config,
    )
    from .constants import DEFAULT_VIEWPORT, DETECT_SPEC_VERSION, ARTIFACTS  # type: ignore
    from .context_utils import make_context_args  # type: ignore
    from .scrolling import auto_scroll_full_page as _auto_scroll_full_page  # type: ignore
    from .scrolling import scroll_by_distance as _scroll_by_distance  # type: ignore
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
        load_json_config,
    )
    from constants import DEFAULT_VIEWPORT, DETECT_SPEC_VERSION, ARTIFACTS  # type: ignore
    from context_utils import make_context_args  # type: ignore
    from scrolling import auto_scroll_full_page as _auto_scroll_full_page  # type: ignore
    from scrolling import scroll_by_distance as _scroll_by_distance  # type: ignore
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
    # 预热滚动：在任何采集动作前，先滚动若干步以触发懒加载
    prewarm_scroll: bool = True,
    prewarm_max_steps: int = 3,
    prewarm_delay_ms: int = 1500,
    prewarm_wait_before_ms: int = 1200,
    prewarm_wait_after_ms: int = 1200,
    prewarm_scroll_ratio: float | None = None,
    prewarm_scroll_pixels: int | None = None,
    prewarm_step_px: int = 200,
    auto_scroll_before_loaded_shot: bool = True,
    autoscroll_max_steps: int = 3,
    autoscroll_delay_ms: int = 1200,
    nav_wait_until: str = "domcontentloaded",
    networkidle_timeout_ms: int = 5000,
    after_nav_wait_ms: int = 2000,
    ready_selector: str | None = None,
    ready_selector_timeout_ms: int = 10000,
    ensure_images_loaded: bool = True,
    images_wait_timeout_ms: int = 30000,
    images_max_count: int = 256,
    ensure_backgrounds_loaded: bool = True,
    stabilize_frames: int = 2,
    stabilize_wait_ms: int = 200,
    device: str | None = None,
    viewport: str | tuple[int, int] | None = None,
    dpr: float | None = None,
    return_info: bool = False,
    container_selector: str | None = None,
    container_step_wait_ms: int = 600,
    step_wait_selector: str | None = None,
    enable_container_stitch: bool = False,
    max_stitch_segments: int = 30,
    max_stitch_seconds: int = 10,
    enable_overlay: bool = True,
    max_stitch_pixels: int = 40000000,
    reset_to_top_before_loaded_shot: bool = True,
    crop_trailing_blank: bool = True,
    crop_margin_px: int = 200,
    crop_max_screens: int | None = 4,
    prefetch_positions: int = 5,
    headless: bool = True,
    human_verify: bool = False,
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
    prewarm_reached_bottom = None
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
                    "prewarm_scroll": prewarm_scroll,
                    "prewarm_max_steps": prewarm_max_steps,
                    "prewarm_delay_ms": prewarm_delay_ms,
                    "prewarm_wait_before_ms": prewarm_wait_before_ms,
                    "prewarm_wait_after_ms": prewarm_wait_after_ms,
                    "prewarm_scroll_ratio": prewarm_scroll_ratio,
                    "prewarm_scroll_pixels": prewarm_scroll_pixels,
                    "prewarm_step_px": prewarm_step_px,
                    "auto_scroll_before_loaded_shot": auto_scroll_before_loaded_shot,
                    "autoscroll_max_steps": autoscroll_max_steps,
                    "autoscroll_delay_ms": autoscroll_delay_ms,
                    "nav_wait_until": nav_wait_until,
                    "networkidle_timeout_ms": networkidle_timeout_ms,
                    "after_nav_wait_ms": after_nav_wait_ms,
                "ready_selector": ready_selector,
                "ready_selector_timeout_ms": ready_selector_timeout_ms,
                "ensure_images_loaded": ensure_images_loaded,
                "images_wait_timeout_ms": images_wait_timeout_ms,
                "images_max_count": images_max_count,
                "ensure_backgrounds_loaded": ensure_backgrounds_loaded,
                "device": device,
                "viewport": v_tuple,
                "dpr": dpr,
                "headless": headless,
                "human_verify": human_verify,
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
                browser = pw.chromium.launch(headless=headless)
                context = browser.new_context(**context_args)
                page = context.new_page()
            except Exception as se:
                error_code = "LAUNCH_ERROR"
                error_stage = "launch"
                raise

            # Navigate and wait for initial state
            try:
                _wu = nav_wait_until if nav_wait_until in ("domcontentloaded", "load", "networkidle", "commit") else "domcontentloaded"
                page.goto(url, timeout=timeout_ms, wait_until=_wu)
            except PlaywrightTimeoutError as te:
                error_code = "NAV_TIMEOUT"
                error_stage = "navigate"
                raise
            except Exception as ne:
                # 若为代理相关错误，尝试无代理重试一次（常见于系统/环境变量配置了不可用代理）。
                msg = str(ne)
                if any(k in msg.upper() for k in ("PROXY", "ERR_TUNNEL", "ERR_NO_SUPPORTED_PROXIES")) and not os.environ.get("AFC_DISABLE_PROXY_FALLBACK"):
                    try:
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
                        # 记录当前环境代理设置，便于排查
                        proxy_env = {k: os.environ.get(k) for k in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy") if os.environ.get(k)}
                        if proxy_env:
                            warnings.append({"code": "PROXY_ENV_DETECTED", "stage": "navigate", "env": proxy_env})
                        warnings.append({"code": "PROXY_FALLBACK", "stage": "navigate", "info": "retry without proxy"})
                        # 以禁用代理方式重启浏览器并重试导航
                        browser = pw.chromium.launch(headless=headless, args=["--no-proxy-server", "--proxy-bypass-list=*"])
                        context = browser.new_context(**context_args)
                        page = context.new_page()
                        _wu = nav_wait_until if nav_wait_until in ("domcontentloaded", "load", "networkidle", "commit") else "domcontentloaded"
                        page.goto(url, timeout=timeout_ms, wait_until=_wu)
                    except Exception:
                        error_code = "NAV_ERROR"
                        error_stage = "navigate"
                        raise
                    else:
                        # 重试成功则继续后续流程
                        pass
                else:
                    error_code = "NAV_ERROR"
                    error_stage = "navigate"
                    raise

            # 可选：等待就绪选择器（元素可见），增强页面稳定性
            if ready_selector:
                try:
                    page.wait_for_selector(ready_selector, state="visible", timeout=max(1, int(ready_selector_timeout_ms)))
                except Exception as _rse:
                    warnings.append({"code": "READY_SELECTOR_TIMEOUT", "stage": "navigate", "selector": ready_selector, "error": str(_rse)})

            # 若启用人工验证，暂停以便手动完成验证码（建议与 --no-headless 搭配）
            if human_verify:
                try:
                    print("[INFO] human_verify: 请在浏览器窗口完成站点验证，完成后回到终端按回车继续……", flush=True)
                    try:
                        input()
                    except EOFError:
                        page.wait_for_timeout(10000)
                except Exception:
                    pass

            # 预热滚动（在任何采集动作前）：先等待 → 缓慢滚动一段距离 → 再等待
            if prewarm_scroll:
                try:
                    if prewarm_wait_before_ms and prewarm_wait_before_ms > 0:
                        page.wait_for_timeout(max(0, int(prewarm_wait_before_ms)))
                    # 计算本次预热滚动的目标距离（优先像素，其次视口比例）
                    total_px = None
                    try:
                        if prewarm_scroll_pixels is not None:
                            total_px = int(prewarm_scroll_pixels)
                        else:
                            vh = page.evaluate(
                                "() => Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0)"
                            ) or 0
                            r = float(prewarm_scroll_ratio or 0.0)
                            if r > 0:
                                total_px = int(max(1, vh * min(1.0, max(0.05, r))))
                    except Exception:
                        total_px = None
                    if total_px is None:
                        # 回退：按步数滚动整页的较小步（避免一次翻页过大）
                        _reached = _auto_scroll_full_page(
                            page,
                            max_steps=int(prewarm_max_steps),
                            delay_ms=int(prewarm_delay_ms),
                        )
                    else:
                        # 缓慢滚动指定距离（分步）
                        steps_cap = max(1, int(prewarm_max_steps))
                        # 每步像素，避免过大
                        step_px = max(10, int(prewarm_step_px))
                        # 按总距离裁剪步数
                        planned_steps = (total_px + step_px - 1) // step_px
                        if planned_steps > steps_cap:
                            step_px = max(1, total_px // steps_cap)
                        _reached = _scroll_by_distance(
                            page,
                            total_px=total_px,
                            step_px=step_px,
                            delay_ms=int(prewarm_delay_ms),
                        )
                    prewarm_reached_bottom = bool(_reached)
                    if prewarm_wait_after_ms and prewarm_wait_after_ms > 0:
                        page.wait_for_timeout(max(0, int(prewarm_wait_after_ms)))
                except Exception as _pse:
                    warnings.append({"code": "PREWARM_SCROLL_ERROR", "stage": "prewarm", "error": str(_pse)})

            # screenshot_initial.png — taken after initial wait/prewarm (+轻量稳定)
            try:
                try:
                    # 稳定两帧 + 额外等待，缓解抖动/过渡影响
                    if stabilize_frames and stabilize_frames > 0:
                        page.evaluate(
                            "(n)=>new Promise(r=>{let i=0; const step=()=>{i++; if(i>=Math.max(1,Number(n)||1)) return r(true); requestAnimationFrame(step);}; requestAnimationFrame(step);})",
                            int(max(1, int(stabilize_frames))),
                        )
                    if stabilize_wait_ms and stabilize_wait_ms > 0:
                        page.wait_for_timeout(max(0, int(stabilize_wait_ms)))
                except Exception:
                    pass
                page.screenshot(path=os.path.join(out_dir, "screenshot_initial.png"), full_page=True)
            except Exception as ee:
                warnings.append({"code": "SCREENSHOT_INITIAL_ERROR", "stage": "screenshot_initial", "error": str(ee)})

            # Inject helper JS (functions in collect_playwright.js)
            # 优先使用 add_init_script，确保在后续任何导航/重载后仍可用；退化为 add_script_tag。
            injected_helpers = False
            try:
                if os.path.exists(JS_HELPERS_FILE):
                    try:
                        with open(JS_HELPERS_FILE, "r", encoding="utf-8") as jf:
                            _js_code = jf.read()
                        page.add_init_script(script=_js_code)
                        injected_helpers = True
                        # 轻量校验：若当前上下文能取到对象则认为可用；若失败，退化为一次性 script_tag
                        try:
                            ok = page.evaluate("() => !!window.DetectHelpers")
                            if not ok:
                                page.add_script_tag(path=JS_HELPERS_FILE)
                        except Exception:
                            try:
                                page.add_script_tag(path=JS_HELPERS_FILE)
                            except Exception:
                                pass
                    except Exception as _is_e:
                        # 文件读取或 init_script 失败
                        try:
                            page.add_script_tag(path=JS_HELPERS_FILE)
                            injected_helpers = True
                        except Exception as _tag_e:
                            warnings.append({"code": "INJECT_JS_ERROR", "stage": "inject_js", "error": f"init/tag failed: {_is_e} / {_tag_e}"})
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
                    page.wait_for_load_state("networkidle", timeout=max(1, int(networkidle_timeout_ms)))
                    achieved_networkidle = True
                except PlaywrightTimeoutError:
                    achieved_networkidle = False
            except Exception as le:
                warnings.append({"code": "LOAD_STATE_ERROR", "stage": "load_state", "error": str(le)})

            # 可选：额外静默等待，给懒加载或过渡动画收尾
            if after_nav_wait_ms and after_nav_wait_ms > 0:
                try:
                    page.wait_for_timeout(max(0, int(after_nav_wait_ms)))
                except Exception:
                    pass

            # 可选：确保首屏图片加载完成（仅视口内前 N 张）
            if ensure_images_loaded:
                try:
                    page.wait_for_function(
                        "(maxCount) => {\n"
                        "  const imgs = Array.from(document.images).filter(img => {\n"
                        "    const r = img.getBoundingClientRect();\n"
                        "    const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0);\n"
                        "    const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0);\n"
                        "    return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<vh && r.left<vw;\n"
                        "  }).slice(0, Math.max(1, Number(maxCount)||32));\n"
                        "  return imgs.length === 0 || imgs.every(img => img.complete && img.naturalWidth>0 && img.naturalHeight>0);\n"
                        "}",
                        arg=images_max_count,
                        timeout=max(1, int(images_wait_timeout_ms)),
                    )
                except Exception as _iwe:
                    warnings.append({"code": "IMAGES_WAIT_TIMEOUT", "stage": "load_state", "error": str(_iwe), "maxCount": int(images_max_count)})
            # 可选：确保视口内的 CSS 背景图加载完成（最佳努力）
            if ensure_backgrounds_loaded and injected_helpers:
                try:
                    page.evaluate(
                        "async (p) => await window.DetectHelpers.waitViewportBackgrounds(p.limit, p.timeout)",
                        {"limit": max(1, int(images_max_count)), "timeout": max(1, int(images_wait_timeout_ms))},
                    )
                except Exception as _bwe:
                    warnings.append({"code": "BG_IMAGES_WAIT_ERROR", "stage": "load_state", "error": str(_bwe)})

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
                    # Optional: once at bottom, ensure viewport-visible images are loaded (for tail area)
                    if ensure_images_loaded:
                        try:
                            page.wait_for_function(
                                "(maxCount) => {\n"
                                "  const imgs = Array.from(document.images).filter(img => {\n"
                                "    const r = img.getBoundingClientRect();\n"
                                "    const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0);\n"
                                "    const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0);\n"
                                "    return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<vh && r.left<vw;\n"
                                "  }).slice(0, Math.max(1, Number(maxCount)||32));\n"
                                "  return imgs.length === 0 || imgs.every(img => img.complete && img.naturalWidth>0 && img.naturalHeight>0);\n"
                                "}",
                                arg=images_max_count,
                                timeout=max(1, int(images_wait_timeout_ms)),
                            )
                        except Exception:
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
            # 分段元信息：将记录每段在容器内容坐标中的范围与在拼接画布中的 y 偏移
            seg_meta: list[dict[str, int]] = []
            try:
                # 限额常量（可按需微调）
                MAX_SEGMENTS = int(max_stitch_segments or 20)
                MAX_SECONDS = int(max_stitch_seconds or 8)
                # 使用可配置像素上限（参数 max_stitch_pixels），无则回退 25MP
                PIXEL_CAP = int(max_stitch_pixels) if (max_stitch_pixels and int(max_stitch_pixels) > 0) else 25_000_000

                if injected_helpers:
                    if container_selector:
                        # 优先使用用户指定容器
                        container_info = page.evaluate(
                            "(s)=>{ const e=document.querySelector(s); if(!e) return null; const cs=getComputedStyle(e); return {selector:s, scrollHeight:e.scrollHeight||0, clientHeight:e.clientHeight||0, overflowY:(cs&&cs.overflowY)||''}; }",
                            container_selector,
                        )
                        if not container_info:
                            warnings.append({"code": "CONTAINER_SELECTOR_NOT_FOUND", "stage": "container_capture", "selector": container_selector})
                    if not container_info:
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
                            last_vis_h = ch  # 记录上一帧的实际可见高度，用于更准确的重叠裁剪
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
                                            "(p)=>{const e=document.querySelector(p.s); return e && Math.abs((e.scrollTop||0)-p.t) < 2}",
                                            arg={"s": sel, "t": desired},
                                            timeout=1000,
                                        )
                                    except Exception:
                                        pass
                                    # 额外等待渲染（放慢分段拍摄节奏）
                                    try:
                                        page.wait_for_timeout(max(0, int(container_step_wait_ms)))
                                    except Exception:
                                        pass
                                    # 可选：等待内容选择器出现（例如骨架屏替换为真实图片/卡片）
                                    if step_wait_selector:
                                        try:
                                            page.wait_for_selector(step_wait_selector, timeout=max(1, int(container_step_wait_ms)))
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
                                # 计算与上一段的重叠，并裁掉重叠的上半部分
                                # 注意：上一帧的实际可见高度可能小于 clientHeight（如被固定头/工具条遮挡），
                                # 若用 ch 计算会造成缝隙或覆盖，改用上一帧截图的真实高度。
                                overlap = max(0, (last_top + last_vis_h) - top) if last_top >= 0 else 0
                                use_h = im.height - overlap
                                if use_h <= 0:
                                    break
                                cropped = im.crop((0, overlap, im.width, overlap + use_h))
                                segs.append(cropped)
                                # 记录该段在容器内容坐标中的起点与高度（content_top = top + overlap）
                                seg_meta.append({
                                    "scrollTop": int(top),
                                    "clientHeight": int(ch),
                                    "overlap": int(overlap),
                                    "content_top": int(top + overlap),
                                    "content_height": int(cropped.height),
                                    "width": int(cropped.width),
                                })
                                max_width = max(max_width, im.width)
                                last_top = top
                                last_vis_h = im.height
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
                                for idx, s in enumerate(segs):
                                    stitched.paste(s, (0, y))
                                    # 回填画布 y 偏移
                                    if idx < len(seg_meta):
                                        seg_meta[idx]["y"] = int(y)
                                    y += s.height
                                stitched_path = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"])
                                stitched.save(stitched_path, format="PNG", optimize=True)
                                # 写出 segments/index.json（容器 bbox 与 scrollTop→y 映射）
                                try:
                                    # 以当前（最终）状态读取容器 bbox 与 scrollTop
                                    met_final = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", sel) or {}
                                    cb = page.evaluate(
                                        "(s)=>{const e=document.querySelector(s); if(!e) return null; const r=e.getBoundingClientRect(); return {x:Math.round(r.x), y:Math.round(r.y), width:Math.round(r.width), height:Math.round(r.height)};}",
                                        sel,
                                    ) or {"x": 0, "y": 0, "width": max_width, "height": ch}
                                    seg_dir = os.path.join(out_dir, ARTIFACTS.get("segments_dir", "segments"))
                                    os.makedirs(seg_dir, exist_ok=True)
                                    seg_index_path = os.path.join(out_dir, ARTIFACTS.get("segments_meta", "segments/index.json"))
                                    seg_doc = {
                                        "container": {
                                            "selector": sel,
                                            "scrollHeight": int(sh),
                                            "clientHeight": int(ch),
                                            "bbox_viewport_final": [int(cb.get("x", 0)), int(cb.get("y", 0)), int(cb.get("width", max_width)), int(cb.get("height", ch))],
                                            "scrollTop_final": int(met_final.get("scrollTop", 0)),
                                        },
                                        "stitched": {
                                            "image": os.path.relpath(stitched_path, out_dir),
                                            "width": int(max_width),
                                            "height": int(total_h),
                                            "segments": seg_meta,
                                        },
                                    }
                                    write_json(seg_index_path, seg_doc)
                                except Exception as se_meta:
                                    warnings.append({"code": "SEGMENTS_META_WRITE_ERROR", "stage": "container_capture", "error": str(se_meta)})
                                stitched_ok = True
            except Exception as ce:
                warnings.append({"code": "CONTAINER_STITCH_ERROR", "stage": "container_capture", "error": str(ce)})

            if not stitched_ok:
                try:
                    # 若无容器拼接，回退整页截图。但为避免顶部灰底（CSS 背景/图片懒加载），
                    # 在 fullPage 前做“顶-中-底”三段预取（仅当启用 ensure_* 时）。
                    if ensure_images_loaded or ensure_backgrounds_loaded:
                        try:
                            # 获取文档高度与视口高度
                            met = page.evaluate("() => ({ sh: Math.max(document.body?.scrollHeight||0, document.documentElement?.scrollHeight||0), ch: Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0) })") or {}
                            sh = int(met.get("sh", 0))
                            ch = int(met.get("ch", 0))
                            if crop_max_screens and crop_max_screens > 0:
                                try:
                                    cap = int(crop_max_screens) * max(1, ch)
                                    sh = min(sh, cap)
                                except Exception:
                                    pass
                            if sh > 0 and ch > 0:
                                # 在 scrollHeight 上均匀取 prefetch_positions 个锚点进行资源预取
                                N = max(3, int(prefetch_positions))
                                last = max(0, sh - ch)
                                positions = [max(0, min(last, int(round(i * last / (N - 1) if N > 1 else 0)))) for i in range(N)]
                                for pos in positions:
                                    try:
                                        page.evaluate("(y)=>window.scrollTo(0,y)", int(pos))
                                        page.wait_for_timeout(200)
                                        if ensure_images_loaded:
                                            try:
                                                page.wait_for_function(
                                                    "() => { const imgs = Array.from(document.images).filter(img => { const r = img.getBoundingClientRect(); const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0); const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0); return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<vh && r.left<vw; }).slice(0,256); return imgs.length===0 || imgs.every(img => img.complete && img.naturalWidth>0 && img.naturalHeight>0); }",
                                                    timeout=max(1, int(images_wait_timeout_ms)),
                                                )
                                            except Exception:
                                                pass
                                        if ensure_backgrounds_loaded and injected_helpers:
                                            try:
                                                page.evaluate(
                                                    "async () => { return await window.DetectHelpers.waitViewportBackgrounds(256, 5000); }"
                                                )
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                    # 最终整页截图
                    page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"]), full_page=True)
                except Exception as ee:
                    warnings.append({"code": "SCREENSHOT_TAIL_ERROR", "stage": "screenshot_tail", "error": str(ee)})

            # 在生成尾部截图后、回到顶部之前，优先落地一次“滚动后的 DOM 简表”
            # 这样可以捕获仅在滚动到可视区域时才被真实挂载/可见的元素（避免回顶后被虚拟化卸载）。
            new_count = None
            dom_scrolled_done = False
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
                # 计算相对初始 DOM 的新增元素数量（用于 scroll_info 汇总）
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
                dom_scrolled_done = True
            except Exception as se:
                warnings.append({"code": "DOM_SUMMARY_SCROLLED_ERROR", "stage": "dom_summary_scrolled", "error": str(se)})

            # screenshot_loaded.png — after load (+ networkidle if achieved) (+轻量稳定+资源就绪)
            try:
                # 先回到顶部，避免由于滚到页尾导致顶部区域处于“收起/置换”状态
                if reset_to_top_before_loaded_shot:
                    try:
                        page.evaluate("() => window.scrollTo(0,0)")
                        page.wait_for_timeout(200)
                    except Exception:
                        pass
                try:
                    if stabilize_frames and stabilize_frames > 0:
                        page.evaluate(
                            "(n)=>new Promise(r=>{let i=0; const step=()=>{i++; if(i>=Math.max(1,Number(n)||1)) return r(true); requestAnimationFrame(step);}; requestAnimationFrame(step);})",
                            int(max(1, int(stabilize_frames))),
                        )
                    if stabilize_wait_ms and stabilize_wait_ms > 0:
                        page.wait_for_timeout(max(0, int(stabilize_wait_ms)))
                except Exception:
                    pass
                # 再次确保视口资源就绪（顶部背景/图片）
                try:
                    if ensure_images_loaded:
                        page.wait_for_function(
                            "(maxCount) => {\n"
                            "  const imgs = Array.from(document.images).filter(img => {\n"
                            "    const r = img.getBoundingClientRect();\n"
                            "    const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0);\n"
                            "    const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0);\n"
                            "    return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<vh && r.left<vw;\n"
                            "  }).slice(0, Math.max(1, Number(maxCount)||32));\n"
                            "  return imgs.length === 0 || imgs.every(img => img.complete && img.naturalWidth>0 && img.naturalHeight>0);\n"
                            "}",
                            arg=images_max_count,
                            timeout=max(1, int(images_wait_timeout_ms)),
                        )
                    if ensure_backgrounds_loaded and injected_helpers:
                        page.evaluate(
                            "async (p) => await window.DetectHelpers.waitViewportBackgrounds(p.limit, p.timeout)",
                            {"limit": max(1, int(images_max_count)), "timeout": max(1, int(images_wait_timeout_ms))},
                        )
                except Exception:
                    pass
                page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_loaded"]), full_page=True)
            except Exception as ee:
                warnings.append({"code": "SCREENSHOT_LOADED_ERROR", "stage": "screenshot_loaded", "error": str(ee)})

            # timings.json — Navigation Timing via helper；若失败则回退原生 performance API
            try:
                nav_timing = None
                if injected_helpers:
                    try:
                        nav_timing = page.evaluate("() => window.DetectHelpers.getNavigationTiming()")
                    except Exception:
                        nav_timing = None
                if nav_timing is None:
                    nav_timing = page.evaluate("() => (performance.getEntriesByType('navigation')[0]?.toJSON?.() || performance.getEntriesByType('navigation')[0] || performance.timing || {})")
            except Exception as te:
                nav_timing = {}
                warnings.append({"code": "TIMINGS_ERROR", "stage": "timings", "error": str(te)})
            try:
                write_json(os.path.join(out_dir, ARTIFACTS["timings"]), nav_timing or {})
            except Exception as we:
                warnings.append({"code": "TIMINGS_WRITE_ERROR", "stage": "timings", "error": str(we)})

            # 如前面已在“底部”抓取成功，这里避免覆盖（虚拟列表回顶后会卸载元素）。
            if not dom_scrolled_done:
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

            # 在滚动后 DOM 简表落地后，回到顶部并重拍一次 loaded 全页图，确保包含最新懒加载内容（再做一次资源就绪）
            try:
                if reset_to_top_before_loaded_shot:
                    try:
                        page.evaluate("() => window.scrollTo(0,0)")
                        page.wait_for_timeout(200)
                    except Exception:
                        pass
                try:
                    if stabilize_frames and stabilize_frames > 0:
                        page.evaluate(
                            "(n)=>new Promise(r=>{let i=0; const step=()=>{i++; if(i>=Math.max(1,Number(n)||1)) return r(true); requestAnimationFrame(step);}; requestAnimationFrame(step);})",
                            int(max(1, int(stabilize_frames))),
                        )
                    if stabilize_wait_ms and stabilize_wait_ms > 0:
                        page.wait_for_timeout(max(0, int(stabilize_wait_ms)))
                except Exception:
                    pass
                try:
                    if ensure_images_loaded:
                        page.wait_for_function(
                            "(maxCount) => {\n"
                            "  const imgs = Array.from(document.images).filter(img => {\n"
                            "    const r = img.getBoundingClientRect();\n"
                            "    const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0);\n"
                            "    const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0);\n"
                            "    return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<vh && r.left<vw;\n"
                            "  }).slice(0, Math.max(1, Number(maxCount)||32));\n"
                            "  return imgs.length === 0 || imgs.every(img => img.complete && img.naturalWidth>0 && img.naturalHeight>0);\n"
                            "}",
                            arg=images_max_count,
                            timeout=max(1, int(images_wait_timeout_ms)),
                        )
                    if ensure_backgrounds_loaded and injected_helpers:
                        page.evaluate(
                            "async (p) => await window.DetectHelpers.waitViewportBackgrounds(p.limit, p.timeout)",
                            {"limit": max(1, int(images_max_count)), "timeout": max(1, int(images_wait_timeout_ms))},
                        )
                except Exception:
                    pass
                page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_loaded"]), full_page=True)
            except Exception as ee:
                warnings.append({"code": "SCREENSHOT_LOADED_RETRY_ERROR", "stage": "screenshot_loaded", "error": str(ee)})

            # Persist scroll info summary
            try:
                info_obj = {
                    "before": doc_before,
                    "after": doc_after,
                    "auto_scroll_reached_bottom": autos_reached_bottom,
                    "prewarm_scroll_reached_bottom": prewarm_reached_bottom,
                    "achieved_networkidle": achieved_networkidle,
                    "new_elements_count": new_count,
                }
                if container_info:
                    info_obj["container"] = container_info
                    try:
                        met_final = page.evaluate("(s)=>window.DetectHelpers.getContainerMetrics(s)", container_info.get("selector")) or {}
                        info_obj["container_final_scrollTop"] = int(met_final.get("scrollTop", 0))
                    except Exception:
                        pass
                write_json(os.path.join(out_dir, ARTIFACTS["scroll_info"]), info_obj)
            except Exception as we:
                warnings.append({"code": "SCROLL_INFO_WRITE_ERROR", "stage": "scroll_info", "error": str(we)})

            # meta.json — URL, domain, viewport, UA, tz, status, versions
            try:
                ua = ""
                if injected_helpers:
                    try:
                        ua = page.evaluate("() => window.DetectHelpers.getUserAgent()") or ""
                    except Exception:
                        ua = ""
                if not ua:
                    try:
                        ua = page.evaluate("() => navigator.userAgent") or ""
                    except Exception:
                        ua = ""
                if not ua:
                    try:
                        # 作为兜底，尝试从上下文配置获取
                        ua = str((context._options or {}).get("userAgent") or "")  # type: ignore[attr-defined]
                    except Exception:
                        ua = ua or ""
                if not ua:
                    warnings.append({"code": "UA_ERROR", "stage": "meta", "error": "userAgent unavailable after fallbacks"})
            except Exception as _ue:
                ua = ""
                warnings.append({"code": "UA_ERROR", "stage": "meta", "error": f"ua_fetch_error: {_ue}"})
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
                # 合并初始与滚动后的 DOM 简表，避免滚动后顶部元素丢失（去重）
                merged: list[dict] | None = None
                try:
                    parts: list[list[dict]] = []
                    p_scrolled = os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"])
                    if os.path.exists(p_scrolled):
                        with open(p_scrolled, "r", encoding="utf-8") as f:
                            doc = json.load(f)
                            if isinstance(doc.get("elements"), list):
                                parts.append(doc.get("elements"))
                    p_base = os.path.join(out_dir, ARTIFACTS["dom_summary"])
                    if os.path.exists(p_base):
                        with open(p_base, "r", encoding="utf-8") as f:
                            doc = json.load(f)
                            if isinstance(doc.get("elements"), list):
                                parts.append(doc.get("elements"))
                    if parts:
                        def _fp(e: dict) -> str:
                            def s(v):
                                return "" if v is None else str(v)
                            bb = e.get("bbox") or [0,0,0,0]
                            return "|".join([
                                s(e.get("tag")), s(e.get("id")), s(e.get("class")), s(e.get("role")), s(e.get("name")),
                                (e.get("text") or "")[:80], f"{bb[0]}-{bb[1]}-{bb[2]}-{bb[3]}"
                            ])
                        seen = set()
                        merged = []
                        for lst in parts:
                            for e in lst:
                                key = _fp(e)
                                if key in seen:
                                    continue
                                seen.add(key)
                                merged.append(e)
                except Exception:
                    merged = None
                if merged is not None:
                    elements_for_tree = merged
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
                        # 可选：按控件/内容的最大 bottom 裁掉尾部空白，并输出一张裁剪版
                        if crop_trailing_blank:
                            try:
                                with open(tree_in, "r", encoding="utf-8") as f:
                                    _tree = json.load(f)
                                _nodes = _tree.get("nodes") or []
                                _max_bottom = 0
                                for n in _nodes:
                                    bb = (n.get("geom") or {}).get("bbox") or [0,0,0,0]
                                    try:
                                        btm = int(bb[1] or 0) + int(bb[3] or 0)
                                    except Exception:
                                        btm = 0
                                    if btm > _max_bottom:
                                        _max_bottom = btm
                                from PIL import Image as _Img, ImageStat as _Stat
                                _im = _Img.open(img_in).convert("RGB")
                                _h = _im.height
                                target_h = min(_h, max(_max_bottom + int(crop_margin_px), 1))
                                # 应用“最多 N 屏高度”的限制
                                try:
                                    _vh = int((context_args.get("viewport", {}) or {}).get("height", 800))
                                    if crop_max_screens and crop_max_screens > 0:
                                        cap_h = int(crop_max_screens) * max(1, _vh)
                                        target_h = min(target_h, cap_h)
                                except Exception:
                                    pass
                                # 进一步：基于图像底部方差从下往上扫描，剪掉大面积“几乎空白/占位”的区域
                                try:
                                    _gs = _im.resize((_im.width, max(1, _im.height)), _Img.BILINEAR).convert("L")
                                    window = 16  # 垂直窗口高度
                                    step = 8
                                    std_thresh = 6.0
                                    last_content_y = _h - 1
                                    y = _h - window
                                    while y > max(0, _h - 2000):
                                        box = (0, max(0, y), _im.width, min(_h, y + window))
                                        crop = _gs.crop(box)
                                        st = _Stat(crop)
                                        var = st.var[0] if st.var else 0.0
                                        if var > std_thresh:
                                            last_content_y = y + window
                                            break
                                        y -= step
                                    target_h = min(target_h, last_content_y + int(crop_margin_px))
                                except Exception:
                                    pass
                                if target_h < _h:
                                    _cropped = _im.crop((0, 0, _im.width, target_h))
                                    _cropped_path = os.path.join(out_dir, ARTIFACTS["screenshot_loaded_cropped"])
                                    _cropped.save(_cropped_path)
                                    # 生成裁剪版 overlay
                                    _cropped_overlay = os.path.join(out_dir, ARTIFACTS["screenshot_loaded_cropped_overlay"])
                                    draw_overlay(_cropped_path, tree_in, _cropped_overlay, min_thickness=1, max_thickness=6, alpha=0, label=False)
                            except Exception as _ce:
                                warnings.append({"code": "CROP_TRAILING_ERROR", "stage": "overlay", "error": str(_ce)})
                        # 追加一张滚动后整页的 Overlay（后一张）
                        try:
                            img_in2 = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"])
                            img_out2 = os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail_overlay"])
                            if os.path.exists(img_in2):
                                draw_overlay(img_in2, tree_in, img_out2, min_thickness=1, max_thickness=6, alpha=0, label=False)
                        except Exception as _e2:
                            warnings.append({"code": "OVERLAY_TAIL_ERROR", "stage": "overlay_tail", "error": str(_e2)})
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
                    "prewarm_scroll": prewarm_scroll,
                    "prewarm_max_steps": prewarm_max_steps,
                    "prewarm_delay_ms": prewarm_delay_ms,
                    "prewarm_wait_before_ms": prewarm_wait_before_ms,
                    "prewarm_wait_after_ms": prewarm_wait_after_ms,
                    "prewarm_scroll_ratio": prewarm_scroll_ratio,
                    "prewarm_scroll_pixels": prewarm_scroll_pixels,
                    "prewarm_step_px": prewarm_step_px,
                    "auto_scroll_before_loaded_shot": auto_scroll_before_loaded_shot,
                    "autoscroll_max_steps": autoscroll_max_steps,
                    "autoscroll_delay_ms": autoscroll_delay_ms,
                    "nav_wait_until": nav_wait_until,
                    "networkidle_timeout_ms": networkidle_timeout_ms,
                    "after_nav_wait_ms": after_nav_wait_ms,
                    "ready_selector": ready_selector,
                    "ready_selector_timeout_ms": ready_selector_timeout_ms,
                "ensure_images_loaded": ensure_images_loaded,
                "images_wait_timeout_ms": images_wait_timeout_ms,
                "images_max_count": images_max_count,
                "ensure_backgrounds_loaded": ensure_backgrounds_loaded,
                "device": device,
                "viewport": v_tuple,
                "dpr": dpr,
                "headless": headless,
                "human_verify": human_verify,
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
                "prewarm_scroll": prewarm_scroll,
                "prewarm_max_steps": prewarm_max_steps,
                "prewarm_delay_ms": prewarm_delay_ms,
                "prewarm_wait_before_ms": prewarm_wait_before_ms,
                "prewarm_wait_after_ms": prewarm_wait_after_ms,
                "prewarm_scroll_ratio": prewarm_scroll_ratio,
                "prewarm_scroll_pixels": prewarm_scroll_pixels,
                "prewarm_step_px": prewarm_step_px,
                "auto_scroll_before_loaded_shot": auto_scroll_before_loaded_shot,
                "autoscroll_max_steps": autoscroll_max_steps,
                "autoscroll_delay_ms": autoscroll_delay_ms,
                "nav_wait_until": nav_wait_until,
                "networkidle_timeout_ms": networkidle_timeout_ms,
                "after_nav_wait_ms": after_nav_wait_ms,
                "ready_selector": ready_selector,
                "ready_selector_timeout_ms": ready_selector_timeout_ms,
                "ensure_images_loaded": ensure_images_loaded,
                "images_wait_timeout_ms": images_wait_timeout_ms,
                "images_max_count": images_max_count,
                "ensure_backgrounds_loaded": ensure_backgrounds_loaded,
                "device": device,
                "viewport": v_tuple,
                "dpr": dpr,
                "headless": headless,
                "human_verify": human_verify,
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
    # 预热滚动相关（在任何采集前）
    p.add_argument("--no-prewarm", dest="prewarm_scroll", action="store_false", help="Disable prewarm scroll before any collection")
    p.add_argument("--prewarm-max-steps", type=int, default=5, help="Max steps for prewarm scroll (default: 3)")
    p.add_argument("--prewarm-delay-ms", type=int, default=1500, help="Delay between prewarm scroll steps in ms (default: 1500)")
    p.add_argument("--prewarm-wait-before-ms", type=int, default=1200, help="Wait before prewarm scroll (default: 1200)")
    p.add_argument("--prewarm-wait-after-ms", type=int, default=1200, help="Wait after prewarm scroll (default: 1200)")
    p.add_argument("--prewarm-scroll-ratio", type=float, default=None, help="Scroll by this ratio of viewport height before collection (default: disabled; use step-based)")
    p.add_argument("--prewarm-scroll-pixels", type=int, default=None, help="Override prewarm scroll distance in pixels (takes precedence over ratio)")
    p.add_argument("--prewarm-step-px", type=int, default=200, help="Prewarm scroll step size in pixels (default: 200)")
    p.add_argument("--no-auto-scroll", dest="auto_scroll", action="store_false", help="Disable auto scroll before loaded screenshot")
    p.add_argument("--autoscroll-max-steps", type=int, default=5, help="Max steps for auto scroll before loaded shot (default: 3)")
    p.add_argument("--autoscroll-delay-ms", type=int, default=1200, help="Delay between auto scroll steps in ms (default: 1200)")
    p.add_argument("--wait-until", type=str, default="domcontentloaded", choices=["domcontentloaded", "load", "networkidle", "commit"], help="WaitUntil for page.goto (default: domcontentloaded)")
    p.add_argument("--config", type=str, default=None, help="Path to JSON config file to override defaults")
    p.add_argument("--networkidle-timeout-ms", type=int, default=5000, help="Timeout for waiting networkidle state (default: 5000)")
    p.add_argument("--after-nav-wait-ms", type=int, default=2000, help="Extra silent wait after load/networkidle (default: 2000)")
    p.add_argument("--ready-selector", type=str, default=None, help="Wait for this selector visible before capture (e.g., main content)")
    p.add_argument("--ready-selector-timeout-ms", type=int, default=10000, help="Timeout for ready selector (default: 10000)")
    # 等待资源：默认开启；提供关闭开关
    p.add_argument("--no-ensure-images", dest="ensure_images", action="store_false", help="Disable waiting for viewport images")
    p.add_argument("--images-wait-timeout-ms", type=int, default=30000, help="Timeout for images/bg ready wait (default: 30000)")
    p.add_argument("--images-max-count", type=int, default=256, help="Only check first N visible images (default: 256)")
    p.add_argument("--no-ensure-backgrounds", dest="ensure_backgrounds", action="store_false", help="Disable waiting for CSS background images in viewport")
    # 默认开启 ensure_*
    p.set_defaults(ensure_images=True, ensure_backgrounds=True)
    p.add_argument("--stabilize-frames", type=int, default=2, help="Wait this many rAF frames before screenshots (default: 2)")
    p.add_argument("--stabilize-wait-ms", type=int, default=200, help="Extra wait before screenshots in ms (default: 200)")
    p.add_argument("--no-reset-top", dest="reset_top", action="store_false", help="Do not scroll back to top before shooting loaded screenshots")
    p.add_argument("--device", type=str, default=None, help="Playwright built-in device name (e.g., 'iPhone 12 Pro')")
    p.add_argument("--viewport", type=str, default=None, help="Custom viewport as 'WIDTHxHEIGHT' (e.g., 1280x800)")
    p.add_argument("--dpr", type=float, default=None, help="Device scale factor (device pixel ratio)")
    p.add_argument("--return-info", action="store_true", help="Return a JSON object with locating info instead of just path")
    p.add_argument("--container-selector", type=str, default=None, help="CSS selector of the main scrollable container (overrides auto-detect)")
    p.add_argument("--container-stitch", dest="enable_container_stitch", action="store_true", help="Enable container-aware stitch (default: off; fullPage fallback by default)")
    p.set_defaults(enable_container_stitch=False)
    p.add_argument("--container-step-wait-ms", type=int, default=600, help="Extra wait after each container scroll step (default: 600ms)")
    p.add_argument("--step-wait-selector", type=str, default=None, help="Optional selector to wait for after each scroll step (e.g. real content img)")
    p.add_argument("--max-stitch-segments", type=int, default=30, help="Max segments to capture for container stitch (default: 30)")
    p.add_argument("--max-stitch-seconds", type=int, default=10, help="Time budget in seconds for container stitch (default: 10)")
    p.add_argument("--max-stitch-pixels", type=int, default=40000000, help="Pixel cap (W*H) for stitched image; clips if exceeded (default: 40MP)")
    # 运行模式
    p.add_argument("--no-headless", dest="headless", action="store_false", help="Run browser in headed mode (default: headless)")
    p.set_defaults(headless=True)
    p.add_argument("--human-verify", action="store_true", help="Pause after navigation to allow manual verification (slider/CAPTCHA)")
    args = p.parse_args()
    # 仅在提供 --config 时载入用户配置；不再自动加载默认配置文件
    cfg = load_json_config(args.config)
    allowed = {
        "prewarm_scroll", "prewarm_max_steps", "prewarm_delay_ms",
        "prewarm_wait_before_ms", "prewarm_wait_after_ms",
        "prewarm_scroll_ratio", "prewarm_scroll_pixels", "prewarm_step_px",
        "auto_scroll_before_loaded_shot", "autoscroll_max_steps", "autoscroll_delay_ms",
        "nav_wait_until", "networkidle_timeout_ms", "after_nav_wait_ms",
        "ready_selector", "ready_selector_timeout_ms",
        "ensure_images_loaded", "images_wait_timeout_ms", "images_max_count",
        "ensure_backgrounds_loaded", "stabilize_frames", "stabilize_wait_ms",
        "reset_to_top_before_loaded_shot", "device", "viewport", "dpr",
        "container_selector", "enable_container_stitch", "container_step_wait_ms",
        "step_wait_selector", "max_stitch_segments", "max_stitch_seconds", "max_stitch_pixels",
        "prefetch_positions", "crop_trailing_blank", "crop_margin_px", "crop_max_screens",
        "headless", "human_verify",
    }
    def cfg_get(name, default):
        return cfg.get(name, default) if isinstance(cfg, dict) and name in allowed else default
    result = collect(
        args.url,
        args.out_root,
        args.timeout_ms,
        raise_on_error=args.raise_on_error,
        prewarm_scroll=cfg_get("prewarm_scroll", args.prewarm_scroll),
        prewarm_max_steps=cfg_get("prewarm_max_steps", args.prewarm_max_steps),
        prewarm_delay_ms=cfg_get("prewarm_delay_ms", args.prewarm_delay_ms),
        prewarm_wait_before_ms=cfg_get("prewarm_wait_before_ms", args.prewarm_wait_before_ms),
        prewarm_wait_after_ms=cfg_get("prewarm_wait_after_ms", args.prewarm_wait_after_ms),
        prewarm_scroll_ratio=cfg_get("prewarm_scroll_ratio", args.prewarm_scroll_ratio),
        prewarm_scroll_pixels=cfg_get("prewarm_scroll_pixels", args.prewarm_scroll_pixels),
        prewarm_step_px=cfg_get("prewarm_step_px", args.prewarm_step_px),
        auto_scroll_before_loaded_shot=cfg_get("auto_scroll_before_loaded_shot", args.auto_scroll),
        autoscroll_max_steps=cfg_get("autoscroll_max_steps", args.autoscroll_max_steps),
        autoscroll_delay_ms=cfg_get("autoscroll_delay_ms", args.autoscroll_delay_ms),
        nav_wait_until=cfg_get("nav_wait_until", args.wait_until),
        networkidle_timeout_ms=cfg_get("networkidle_timeout_ms", args.networkidle_timeout_ms),
        after_nav_wait_ms=cfg_get("after_nav_wait_ms", args.after_nav_wait_ms),
        ready_selector=cfg_get("ready_selector", args.ready_selector),
        ready_selector_timeout_ms=cfg_get("ready_selector_timeout_ms", args.ready_selector_timeout_ms),
        ensure_images_loaded=cfg_get("ensure_images_loaded", args.ensure_images),
        images_wait_timeout_ms=cfg_get("images_wait_timeout_ms", args.images_wait_timeout_ms),
        images_max_count=cfg_get("images_max_count", args.images_max_count),
        ensure_backgrounds_loaded=cfg_get("ensure_backgrounds_loaded", args.ensure_backgrounds),
        stabilize_frames=cfg_get("stabilize_frames", args.stabilize_frames),
        stabilize_wait_ms=cfg_get("stabilize_wait_ms", args.stabilize_wait_ms),
        reset_to_top_before_loaded_shot=cfg_get("reset_to_top_before_loaded_shot", args.reset_top),
        device=cfg_get("device", args.device),
        viewport=cfg_get("viewport", args.viewport),
        dpr=cfg_get("dpr", args.dpr),
        return_info=args.return_info,
        container_selector=cfg_get("container_selector", args.container_selector),
        container_step_wait_ms=cfg_get("container_step_wait_ms", args.container_step_wait_ms),
        step_wait_selector=cfg_get("step_wait_selector", args.step_wait_selector),
        enable_container_stitch=cfg_get("enable_container_stitch", args.enable_container_stitch),
        max_stitch_segments=cfg_get("max_stitch_segments", args.max_stitch_segments),
        max_stitch_seconds=cfg_get("max_stitch_seconds", args.max_stitch_seconds),
        max_stitch_pixels=cfg_get("max_stitch_pixels", args.max_stitch_pixels),
        prefetch_positions=cfg_get("prefetch_positions", 5),
        crop_trailing_blank=cfg_get("crop_trailing_blank", True),
        crop_margin_px=cfg_get("crop_margin_px", 200),
        crop_max_screens=cfg_get("crop_max_screens", 10),
        headless=cfg_get("headless", args.headless),
        human_verify=cfg_get("human_verify", args.human_verify),
    )
    if args.return_info:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
