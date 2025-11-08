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

from .errors import CollectError
from .utils import (
    sanitize_domain,
    timestamp_yyyymmddhhmmss,
    ensure_unique_dir,
    write_json,
    validate_url,
    parse_viewport,
)
from .constants import DEFAULT_VIEWPORT, DETECT_SPEC_VERSION, ARTIFACTS
from .context_utils import make_context_args
from .scrolling import auto_scroll_full_page as _auto_scroll_full_page

JS_HELPERS_FILE = os.path.join(os.path.dirname(__file__), "collect_playwright.js")


# 以上工具与异常等已抽离到独立模块，减少与采集主流程的耦合。


def collect(
    url: str,
    out_root: str = "data",
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
                    dom_summary = page.evaluate("(limit, opts) => window.DetectHelpers.getDomSummaryAdvanced(limit, opts)", 20000, {"occlusionStep": 8})
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
                        page.wait_for_load_state("networkidle", timeout=1500)
                    except PlaywrightTimeoutError:
                        pass
                except Exception as se:
                    warnings.append({"code": "AUTOSCROLL_ERROR", "stage": "autosupport", "error": str(se)})

            # Re-measure document metrics after scrolling
            try:
                doc_after = page.evaluate("() => window.DetectHelpers.getDocMetrics()") if injected_helpers else {"scrollHeight": None, "clientHeight": None}
            except Exception:
                doc_after = {"scrollHeight": None, "clientHeight": None}

            # Optionally capture bottom viewport (tail) to emphasize scrolled-in content
            try:
                page.screenshot(path=os.path.join(out_dir, ARTIFACTS["screenshot_scrolled_tail"]), full_page=False)
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
                    dom_summary_scrolled = page.evaluate("(limit, opts) => window.DetectHelpers.getDomSummaryAdvanced(limit, opts)", 20000, {"occlusionStep": 8})
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
                write_json(os.path.join(out_dir, ARTIFACTS["scroll_info"]), {
                    "before": doc_before,
                    "after": doc_after,
                    "auto_scroll_reached_bottom": autos_reached_bottom,
                    "achieved_networkidle": achieved_networkidle,
                    "new_elements_count": new_count,
                })
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
    p.add_argument("--out-root", default="data", help="Output root directory (default: data)")
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
