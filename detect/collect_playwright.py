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
    from .overlay_utils import generate_overlays  # type: ignore
    from .meta_utils import get_user_agent as _get_ua, write_meta as _write_meta, update_meta_artifacts as _update_meta  # type: ignore
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
    from overlay_utils import generate_overlays  # type: ignore
    from meta_utils import get_user_agent as _get_ua, write_meta as _write_meta, update_meta_artifacts as _update_meta  # type: ignore
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
    headless: bool = False,
    human_verify: bool = False,
    overlay_mode_loaded: str = "auto",
    overlay_mode_tail: str = "auto",
    # 自动关闭弹层/遮罩（登录框/验证/营销弹窗等）
    auto_close_overlays: bool = True,
    overlay_close_selectors: str | None = None,
    overlay_hide_fixed_mask: bool = True,
    overlay_wait_after_ms: int = 300,
    # 是否提取第一层控件的 DOM 片段（outerHTML）并分类存储
    extract_snippets: bool = True,
    # 头显高亮控件：在页面中为检测到的控件添加可视化描边
    live_outline_controls: bool = False,
    live_outline_color: str = "rgba(255,0,0,0.9)",
    live_outline_width_px: int = 2,
    # 控件框调优：扩大到容器/膨胀像素
    expand_to_container: bool = False,
    bbox_inflate_px: int = 0,
    # 导出 tips：为每个控件/节点生成一段 HTML 片段
    export_tips: bool = True,
    # 基于片段包含关系优化父子关系，并输出根节点列表
    refine_parent_by_snippet: bool = True,
    # 在页面内为候选控件打标（写入 __actiontype/__selectorid 属性，影响后续 dom.html/tips 片段）
    annotate_controls: bool = True,
    # 交互式展开（默认开启）：对安全控件做有限点击/输入以显性展开面板，再进行后续采集
    interactive_reveal: bool = True,
    reveal_max_actions: int = 8,
    reveal_total_budget_ms: int = 15000,
    reveal_wait_ms: int = 800,
    # 注入标注微探针（默认开启，减少 none）
    annotate_probe: bool = True,
    annotate_probe_max: int = 30,
    annotate_probe_wait_ms: int = 200,
    annotate_no_none: bool = False,
    # 容器自动/强制包含策略（来自 CLI 或配置），可为逗号分隔字符串或列表
    force_include_ids: str | list[str] | None = None,
    force_include_selectors: str | list[str] | None = None,
    include_roles: str | list[str] | None = "search,form",
    include_class_kw: str | list[str] | None = "search,wrap,container,box",
    include_min_controls: int | None = 3,
    # 低频与单实例控制（缓解触发）：
    disable_proxy: bool = False,
    single_instance_lock: str | None = None,
    min_interval_seconds: int = 0,
    jitter_seconds: int = 0,
    rate_state_file: str | None = None,
    sleep_after_seconds: int = 0,
    # 日志
    verbose: bool = True,
    # 主控件块分割（可选；严格规则默认开启并要求命中内层类词）
    ai_blocks: bool = False,
    ai_blocks_max: int = 8,
    blocks_strict: bool = True,
    blocks_strict_require_inner: bool = True,
    # 控件树尺寸过滤（默认开启）
    filter_tree_by_size: bool = True,
    filter_min_w: int = 96,
    filter_min_h: int = 80,
    filter_min_area: int = 20000,
    filter_max_area_ratio: float = 0.6,
    filter_cap_small_per_parent: int = 12,
    filter_keep_important: bool = True,
    # 交互图（默认开启）：针对主控件块探索交互关系并写 graphs/graph_*.json
    explore_graph: bool = True,
    explore_graph_max_ops_per_block: int = 20,
    explore_graph_wait_ms: int = 500,
    # 是否导出浏览器上下文 cookies 至 cookies.json（包含潜在敏感信息，默认关闭）
    export_cookies: bool = False,
) -> str | Dict[str, Any]:
    """
    Collect page artifacts using Playwright and save under data/<domain>/<timestamp>/.

    Returns the final directory path.
    """
    # verbose logger (define before any usage)
    def _v(msg: str) -> None:
        if verbose:
            print(f"[detect] {msg}")
    # 基本输出目录与时间戳
    started_epoch = time.time()
    ts = timestamp_yyyymmddhhmmss()
    domain_key = sanitize_domain(url)
    base_dir = os.path.join(out_root, domain_key, ts)
    out_dir = ensure_unique_dir(base_dir)
    _v(f"out_dir={out_dir}")

    status = "ok"
    achieved_networkidle = False
    autos_reached_bottom = None
    prewarm_reached_bottom = None
    warnings: list[dict[str, Any]] = []
    error_code = None
    error_stage = None

    # 单实例锁与全局节流（可选）
    lock_path = None
    lock_fd = None
    try:
        if single_instance_lock:
            lock_path = single_instance_lock
            try:
                # 尝试独占创建
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(lock_fd, str(os.getpid()).encode("utf-8"))
            except FileExistsError:
                warnings.append({"code": "SINGLE_INSTANCE_LOCKED", "stage": "init", "lock": lock_path})
                # 等待一小会儿再失败，避免并发
                time.sleep(1.0)
                raise RuntimeError(f"single instance lock held: {lock_path}")

        # 节流：使用全局状态文件记录最近一次运行时间
        rsf = rate_state_file or os.path.join(out_root, ".detect_rate.json")
        if min_interval_seconds and min_interval_seconds > 0:
            try:
                last_epoch = None
                if os.path.exists(rsf):
                    with open(rsf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        last_epoch = float(data.get("last_epoch") or 0)
                now = time.time()
                if last_epoch and now - last_epoch < float(min_interval_seconds):
                    remain = float(min_interval_seconds) - (now - last_epoch)
                    # 抖动
                    jitter = max(0, float(jitter_seconds or 0))
                    delay = max(0.0, remain) + (0.0 if jitter == 0 else min(jitter, __import__("random").random() * jitter))
                    warnings.append({"code": "RATE_LIMIT_SLEEP", "stage": "init", "sleep_seconds": round(delay, 2)})
                    time.sleep(delay)
                # 更新状态
                try:
                    with open(rsf, "w", encoding="utf-8") as f:
                        json.dump({"last_epoch": time.time()}, f)
                except Exception:
                    pass
            except Exception as _rse:
                warnings.append({"code": "RATE_LIMIT_ERROR", "stage": "init", "error": str(_rse)})
    except Exception as _le:
        # 锁/节流异常不致命
        warnings.append({"code": "INIT_THROTTLE_ERROR", "stage": "init", "error": str(_le)})

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
                "overlay_mode_loaded": overlay_mode_loaded,
                "overlay_mode_tail": overlay_mode_tail,
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
                launch_args = []
                if disable_proxy:
                    launch_args += ["--no-proxy-server", "--proxy-bypass-list=*"]
                    # 记录并提示当前环境代理变量（不修改全局环境，仅作为信息）
                    proxy_env = {k: os.environ.get(k) for k in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy") if os.environ.get(k)}
                    if proxy_env:
                        warnings.append({"code": "PROXY_ENV_DETECTED", "stage": "launch", "env": proxy_env})
                    warnings.append({"code": "PROXY_DISABLED", "stage": "launch"})
                browser = pw.chromium.launch(headless=headless, args=launch_args)
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

            # 自动尝试关闭常见弹层/遮罩
            if auto_close_overlays:
                try:
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                    if overlay_wait_after_ms and overlay_wait_after_ms > 0:
                        page.wait_for_timeout(max(0, int(overlay_wait_after_ms)))
                    # 关闭按钮选择器列表（可通过参数覆盖，逗号分隔）
                    if overlay_close_selectors:
                        sels = [s.strip() for s in str(overlay_close_selectors).split(',') if s.strip()]
                    else:
                        sels = [
                            ".ui-dialog-close", ".modal .close", ".popup .close", ".dialog .close",
                            "[aria-label='关闭']", ".layui-layer-close", ".ant-modal-close", ".ant-drawer-close",
                            ".close-btn", ".btn-close", ".JDJRV-close", ".J-close", ".J_ModalClose",
                        ]
                    closed = 0
                    for _ in range(3):
                        changed = 0
                        for s in sels:
                            try:
                                loc_first = page.locator(s).first
                                loc = loc_first() if callable(loc_first) else loc_first
                                if loc and loc.count() > 0 and loc.is_visible():
                                    loc.click()
                                    changed += 1
                                    closed += 1
                                    if overlay_wait_after_ms and overlay_wait_after_ms > 0:
                                        page.wait_for_timeout(max(0, int(overlay_wait_after_ms)))
                            except Exception:
                                continue
                        if changed == 0:
                            break
                    if overlay_hide_fixed_mask:
                        try:
                            hidden = page.evaluate(
                                """
                                () => { 
                                  const vw = Math.max(document.documentElement?.clientWidth||0, window.innerWidth||0);
                                  const vh = Math.max(document.documentElement?.clientHeight||0, window.innerHeight||0);
                                  const els = Array.from(document.querySelectorAll('*'));
                                  const hidden = [];
                                  for (const el of els){
                                    const cs = getComputedStyle(el);
                                    if (!cs) continue;
                                    const pos = cs.position||'';
                                    if (pos !== 'fixed' && pos !== 'sticky') continue;
                                    const zi = Number(cs.zIndex||0);
                                    const r = el.getBoundingClientRect();
                                    const area = Math.max(0, r.width*r.height);
                                    if (zi >= 10 && area > vw*vh*0.4){
                                      try { el.setAttribute('data-afc-hidden','1'); el.style.setProperty('display','none','important'); } catch(e){}
                                      hidden.push((el.tagName||'') + '#' + (el.id||'') + '.' + (el.className||'').toString().slice(0,40));
                                    }
                                  }
                                  return hidden.slice(0,10);
                                }
                                """
                            )
                            if hidden:
                                warnings.append({"code": "OVERLAY_AUTO_HIDE", "stage": "navigate", "hidden": hidden})
                        except Exception:
                            pass
                    if closed > 0:
                        warnings.append({"code": "OVERLAY_AUTO_CLICK", "stage": "navigate", "closed": int(closed)})
                except Exception as _ace:
                    warnings.append({"code": "OVERLAY_AUTO_ERROR", "stage": "navigate", "error": str(_ace)})

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

            # 可选：在 DOM 上为潜在控件节点打标，写入 __actiontype / __selectorid 属性（优先 JS；失败回退 Python 版）
            try:
                if annotate_controls:
                    marked_res = None
                    try:
                        marked_res = page.evaluate(
                            "opts => window.DetectHelpers && window.DetectHelpers.annotateControls && window.DetectHelpers.annotateControls(opts)",
                            {
                                "setDomIdAttr": True,
                                "enableProbe": bool(annotate_probe),
                                "probeMax": int(max(0, int(annotate_probe_max or 0))),
                                "probeWaitMs": int(max(0, int(annotate_probe_wait_ms or 0))),
                                "noNone": bool(annotate_no_none),
                            },
                        )
                    except Exception:
                        marked_res = None
                    if not (isinstance(marked_res, dict) and marked_res.get("ok")):
                        # fallback: Python 内联版本
                        marked = page.evaluate(
                            """
                            (()=>{ let c=0; const all=document.querySelectorAll('*');
                              const isC=(el)=>{const t=(el.tagName||'').toLowerCase(); const r=(el.getAttribute('role')||'').toLowerCase();
                                if(['button','input','select','textarea','a'].includes(t)) return true; if(['button','link','textbox','checkbox','radio','combobox'].includes(r)) return true;
                                const tb=Number(el.getAttribute('tabindex')); if(!Number.isNaN(tb)&&tb>=0) return true; if(el.isContentEditable) return true; const cls=(el.className||'').toLowerCase(); if(cls&&cls.includes('btn')) return true; return false; };
                              const act=(el)=>{const t=(el.tagName||'').toLowerCase(); const r=(el.getAttribute('role')||'').toLowerCase(); if(t==='input'){const it=(el.getAttribute('type')||'').toLowerCase();
                                if(['checkbox','radio','switch','toggle'].includes(it)) return 'toggle'; if(['submit'].includes(it)) return 'submit'; if(['button','image','reset'].includes(it)) return 'click'; return 'type'; }
                                if(t==='textarea') return 'type'; if(t==='select') return 'select'; if(t==='a'||r==='link') return 'navigate'; if(r==='button') return 'click'; return 'none'; };
                              for(let i=0;i<all.length;i++){ const el=all[i]; try{ if(!isC(el)) continue; const a=act(el); const did=el.getAttribute('id')||''; el.setAttribute('__selectorid','d'+i); if(did) el.setAttribute('__domid',did); el.setAttribute('__actiontype',a); c++; }catch(_){}}
                              return c; })()
                            """
                        )
                        if isinstance(marked, int):
                            _v(f"annotated controls with __actiontype/__selectorid: {marked}")
                    else:
                        _v(f"annotated controls (js) count={marked_res.get('count')}")
            except Exception as _ann_e:
                warnings.append({"code": "ANNOTATE_CONTROLS_ERROR", "stage": "annotate", "error": str(_ann_e)})

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

            # Phase-B：交互式展开（安全白名单动作），默认开启
            try:
                summary = None
                if interactive_reveal:
                    _v("interactive reveal phase …")
                    url_before_reveal = page.url
                    if injected_helpers:
                        try:
                            summary = page.evaluate("opts => window.DetectHelpers && window.DetectHelpers.revealInteractively && window.DetectHelpers.revealInteractively(opts)", {
                                "maxActions": int(max(0, reveal_max_actions)),
                                "totalBudgetMs": int(max(0, reveal_total_budget_ms)),
                                "waitMs": int(max(0, reveal_wait_ms)),
                            })
                            # 若发生跳页，回退并重新标注，避免状态丢失
                            try:
                                if page.url != url_before_reveal or (isinstance(summary, dict) and summary.get("navigated")):
                                    try:
                                        page.go_back(wait_until='domcontentloaded', timeout=3000)
                                    except Exception:
                                        pass
                                    # 重新标注（JS）
                                    try:
                                        page.evaluate("opts => window.DetectHelpers && window.DetectHelpers.annotateControls && window.DetectHelpers.annotateControls(opts)", {"setDomIdAttr": True})
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        except Exception:
                            summary = None
                    else:
                        # 若未注入 JS，维持原始不作为（保守）
                        pass
                # 持久化试探式交互记录（即使关闭，也写出空结构，便于审计）
                try:
                    from .utils import write_json as _wj  # type: ignore
                except Exception:
                    from utils import write_json as _wj  # type: ignore
                try:
                    _wj(os.path.join(out_dir, ARTIFACTS["reveal_log"]), {
                        "ok": bool(isinstance(summary, dict) and summary.get("ok")),
                        "actions": int((summary or {}).get("actions") or 0) if isinstance(summary, dict) else 0,
                        "navigated": bool((summary or {}).get("navigated")) if isinstance(summary, dict) else False,
                        "steps": (summary or {}).get("steps") if isinstance(summary, dict) else [],
                    })
                except Exception as _wl:
                    warnings.append({"code": "REVEAL_LOG_WRITE_ERROR", "stage": "reveal", "error": str(_wl)})
            except Exception as re:
                warnings.append({"code": "INTERACTIVE_REVEAL_ERROR", "stage": "reveal", "error": str(re)})

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

            # 在生成尾部截图后、回到顶部之前，优先落地一次“滚动后的 DOM 简表”（封装）
            new_count = None
            dom_scrolled_done = False
            try:
                try:
                    from .dom_utils import perform_scrolled_phase  # type: ignore
                except Exception:
                    from dom_utils import perform_scrolled_phase  # type: ignore
                res = perform_scrolled_phase(
                    page,
                    out_dir,
                    ensure_images_loaded=ensure_images_loaded,
                    images_wait_timeout_ms=images_wait_timeout_ms,
                    ensure_backgrounds_loaded=ensure_backgrounds_loaded,
                    autoscroll_max_steps=autoscroll_max_steps,
                    autoscroll_delay_ms=autoscroll_delay_ms,
                    prefetch_positions=prefetch_positions,
                )
                dom_summary_scrolled = res.get("dom_summary_scrolled") or []
                new_count = res.get("new_count")
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

            # meta.json — URL, domain, viewport, UA, tz, status, versions（抽取到 meta_utils）
            try:
                ua = _get_ua(page, context)
                if not ua:
                    warnings.append({"code": "UA_ERROR", "stage": "meta", "error": "userAgent unavailable after fallbacks"})
            except Exception as _ue:
                ua = ""
                warnings.append({"code": "UA_ERROR", "stage": "meta", "error": f"ua_fetch_error: {_ue}"})
            try:
                title = page.title() or ""
            except Exception:
                title = ""
            try:
                _write_meta(
                    out_dir,
                    url=url,
                    title=title,
                    domain_key=domain_key,
                    ts=ts,
                    ua=ua,
                    viewport=context_args.get("viewport", DEFAULT_VIEWPORT),
                    status="ok",
                    achieved_networkidle=achieved_networkidle,
                    warnings=warnings,
                    device_name=device,
                    dpr=dpr,
                    started_epoch=started_epoch,
                )
            except Exception:
                pass

            # controls_tree.json — 极简控件树（默认启用）
            try:
                controls_out = os.path.join(out_dir, ARTIFACTS["controls_tree"])
                _v("building controls_tree: merge dom_summary + dom_summary_scrolled …")
                try:
                    from .dom_utils import merge_elements_for_tree  # type: ignore
                except Exception:
                    from dom_utils import merge_elements_for_tree  # type: ignore
                elements_for_tree = merge_elements_for_tree(
                    out_dir,
                    base_path=os.path.join(out_dir, ARTIFACTS["dom_summary"]),
                    scrolled_path=os.path.join(out_dir, ARTIFACTS["dom_summary_scrolled"]),
                ) or (dom_summary_scrolled if (isinstance(locals().get("dom_summary_scrolled"), list) and locals().get("dom_summary_scrolled")) else dom_summary)
                if not elements_for_tree:
                    raise RuntimeError("no elements available for controls tree")
                # 不做任何可见性/遮挡过滤，保留所有符合 bbox>0 的节点
                # 如果用户指定强制/自动包含策略，解析为列表传入（不依赖 cfg_get）
                def _to_list(v):
                    if v is None:
                        return None
                    if isinstance(v, str):
                        return [x.strip() for x in v.split(',') if x.strip()]
                    if isinstance(v, (list, tuple)):
                        return [str(x).strip() for x in v if str(x).strip()]
                    return None

                force_ids = _to_list(force_include_ids)
                force_sels = _to_list(force_include_selectors)
                auto_roles = _to_list(include_roles)
                auto_kw = _to_list(include_class_kw)
                try:
                    min_ctrls = int(include_min_controls) if include_min_controls is not None else 3
                except Exception:
                    min_ctrls = 3

                _v("write controls_tree.json …")
                write_controls_tree(
                    elements_for_tree,
                    controls_out,
                    only_visible=False,
                    filter_occluded=False,
                    occ_threshold=0.98,
                    expand_to_container=bool(expand_to_container),
                    inflate_px=int(bbox_inflate_px or 0),
                    force_include_ids=force_ids,
                    force_include_selectors=force_sels,
                    auto_include_roles=auto_roles,
                    auto_include_class_keywords=auto_kw,
                    min_controls_in_subtree=min_ctrls,
                )
                _v("controls_tree.json done")
                # 控件树尺寸过滤（默认开启）
                try:
                    if filter_tree_by_size:
                        _v("filter controls_tree by size …")
                        try:
                            from .tree_filter import filter_controls_tree as _filt  # type: ignore
                        except Exception:
                            from tree_filter import filter_controls_tree as _filt  # type: ignore
                        try:
                            _filt(
                                out_dir,
                                min_w=int(filter_min_w),
                                min_h=int(filter_min_h),
                                min_area=int(filter_min_area),
                                max_area_ratio=float(filter_max_area_ratio),
                                cap_small_per_parent=int(filter_cap_small_per_parent),
                                keep_important=bool(filter_keep_important),
                                in_place=True,
                            )
                        except Exception as _fe:
                            warnings.append({"code": "TREE_FILTER_ERROR", "stage": "controls_tree", "error": str(_fe)})
                except Exception:
                    pass
                # 可选：主控件块分割
                try:
                    if ai_blocks:
                        _v("segment main control blocks (heuristic) …")
                        try:
                            from .block_segmenter import segment_main_blocks  # type: ignore
                        except Exception:
                            from block_segmenter import segment_main_blocks  # type: ignore
                        try:
                            seg = segment_main_blocks(page, out_dir, max_blocks=int(ai_blocks_max or 8), use_llm=False)
                            warnings.append({"code": "BLOCKS_SEGMENTED", "stage": "blocks", "count": len(seg.get("blocks") or [])})
                        except Exception as _se:
                            warnings.append({"code": "BLOCKS_ERROR", "stage": "blocks", "error": str(_se)})
                    if blocks_strict:
                        _v("segment main control blocks (strict rules) …")
                        try:
                            from .block_rules import segment_blocks_strict  # type: ignore
                        except Exception:
                            from block_rules import segment_blocks_strict  # type: ignore
                        try:
                            seg2 = segment_blocks_strict(out_dir, require_inner_kw=bool(blocks_strict_require_inner), max_blocks=int(ai_blocks_max or 8))
                            warnings.append({"code": "BLOCKS_STRICT_SEGMENTED", "stage": "blocks", "count": len(seg2.get("blocks") or [])})
                        except Exception as _se2:
                            warnings.append({"code": "BLOCKS_STRICT_ERROR", "stage": "blocks", "error": str(_se2)})
                except Exception:
                    pass
                # 可选：交互图（基于 blocks.json）
                try:
                    if explore_graph:
                        _v("build interaction graphs for blocks …")
                        try:
                            from .interaction_graph import explore_all_blocks as _expl_all  # type: ignore
                        except Exception:
                            from interaction_graph import explore_all_blocks as _expl_all  # type: ignore
                        try:
                            _expl_all(page, out_dir, max_ops_per_block=int(explore_graph_max_ops_per_block or 20), wait_ms=int(explore_graph_wait_ms or 500))
                        except Exception as _ge:
                            warnings.append({"code": "GRAPH_BUILD_ERROR", "stage": "graph", "error": str(_ge)})
                except Exception:
                    pass
                # 额外写出根节点列表（roots.json），便于下游直接消费
                try:
                    with open(controls_out, "r", encoding="utf-8") as tf:
                        _tree = json.load(tf) or {}
                    _roots = _tree.get("roots") or []
                    write_json(os.path.join(out_dir, ARTIFACTS["roots_list"]), {"count": len(_roots), "roots": _roots})
                except Exception as _rw:
                    warnings.append({"code": "ROOTS_WRITE_ERROR", "stage": "controls_tree", "error": str(_rw)})
            except Exception as ce:
                warnings.append({"code": "CONTROLS_TREE_ERROR", "stage": "controls_tree", "error": str(ce)})
                if verbose:
                    print(f"[detect] controls_tree error: {ce}")

            # 头显高亮：基于控件树 selector 在页面为控件添加 outline（仅 headed）
            if live_outline_controls and (not headless):
                _v("live outline controls on page …")
                try:
                    controls_path = os.path.join(out_dir, ARTIFACTS["controls_tree"])
                    selectors: list[str] = []
                    with open(controls_path, "r", encoding="utf-8") as f:
                        tree_doc = json.load(f)
                    nodes = [n for n in (tree_doc.get("nodes") or []) if isinstance(n, dict) and n.get("type") == "control"]
                    seen = set()
                    # 自上而下，取前 200 个 selector 去重
                    def _y(n):
                        try:
                            return int((n.get("geom") or {}).get("bbox", [0,0,0,0])[1] or 0)
                        except Exception:
                            return 0
                    nodes.sort(key=_y)
                    for n in nodes:
                        s = n.get("selector")
                        if not s or s in seen:
                            continue
                        seen.add(s)
                        selectors.append(s)
                        if len(selectors) >= 200:
                            break
                    if selectors:
                        page.evaluate(
                            "(p)=>{ const sels=p.sels||[]; const col=p.color||'rgba(255,0,0,0.9)'; const w=Math.max(1,Number(p.width)||2);\n"
                            "for(const s of sels){ try{ const el=document.querySelector(s); if(!el) continue; el.style.setProperty('outline', w+'px solid '+col, 'important'); el.setAttribute('data-afc-live-outline','1'); }catch(_){} } }",
                            {"sels": selectors, "color": str(live_outline_color), "width": int(live_outline_width_px)},
                        )
                        warnings.append({"code": "LIVE_OUTLINE", "stage": "visual", "count": len(selectors)})
                except Exception as _loe:
                    warnings.append({"code": "LIVE_OUTLINE_ERROR", "stage": "visual", "error": str(_loe)})

            try:
                _v("generate icons …")
                if export_tips:
                    controls_path = os.path.join(out_dir, ARTIFACTS["controls_tree"])
                    if os.path.exists(controls_path):
                        try:
                            from .tips import write_tips as _write_tips  # type: ignore
                        except Exception:
                            from tips import write_tips as _write_tips  # type: ignore
                        try:
                            cnt, idx_path = _write_tips(page, out_dir, controls_path)
                            _v(f"tips exported: {cnt} -> {idx_path}")
                        except Exception as _we:
                            warnings.append({"code": "TIPS_INDEX_WRITE_ERROR", "stage": "tips", "error": str(_we)})
                        # 片段导出完成后：可选按包含关系优化父子列表，并计算根节点
                        try:
                            if refine_parent_by_snippet:
                                from .controls_tree import refine_tree_parent_child_by_snippet  # type: ignore
                                refine_tree_parent_child_by_snippet(
                                    os.path.join(out_dir, ARTIFACTS["controls_tree"]),
                                    os.path.join(out_dir, ARTIFACTS["tips_index"]),
                                    verbose=verbose,
                                )
                                _v("refined parent/children by snippet containment and wrote roots")
                        except Exception as _re2:
                            warnings.append({"code": "REFINE_PARENT_ERROR", "stage": "tips_refine", "error": str(_re2)})
            except Exception as _te2:
                warnings.append({"code": "TIPS_ERROR", "stage": "tips", "error": str(_te2)})

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

            try:
                if extract_snippets:
                    _v("extract snippets (first-layer controls) …")
                    tree_path = os.path.join(out_dir, ARTIFACTS["controls_tree"])
                    if os.path.exists(tree_path):
                        try:
                            from .tips import write_snippets_first_layer as _write_snips  # type: ignore
                        except Exception:
                            from tips import write_snippets_first_layer as _write_snips  # type: ignore
                        try:
                            cnt = _write_snips(page, out_dir, tree_path)
                            _v(f"snippets exported: {cnt}")
                        except Exception as se:
                            warnings.append({"code": "SNIPPETS_ERROR", "stage": "snippets", "error": str(se)})
                        # 写索引
                        try:
                            sn_dir = os.path.join(out_dir, ARTIFACTS["snippets_dir"])
                            os.makedirs(sn_dir, exist_ok=True)
                            write_json(os.path.join(out_dir, ARTIFACTS["snippets_index"]), {
                                "level": "first_layer",
                                "count": len(index),
                                "items": index,
                            })
                        except Exception as we:
                            warnings.append({"code": "SNIPPETS_INDEX_WRITE_ERROR", "stage": "snippets", "error": str(we)})
            except Exception as se:
                warnings.append({"code": "SNIPPETS_ERROR", "stage": "snippets", "error": str(se)})

            # 自动生成 Overlay 截图（loaded/cropped/tail）— 抽取到 overlay_utils
            try:
                if enable_overlay:
                    _v("generate overlays …")
                    ov = generate_overlays(
                        out_dir,
                        overlay_mode_loaded=overlay_mode_loaded,
                        overlay_mode_tail=overlay_mode_tail,
                        crop_trailing_blank=bool(crop_trailing_blank),
                        crop_margin_px=int(crop_margin_px),
                        crop_max_screens=int(crop_max_screens) if (crop_max_screens is not None) else None,
                        viewport_height=int((context_args.get("viewport", {}) or {}).get("height", 800)),
                    )
                    if not ov.get("loaded"):
                        warnings.append({"code": "OVERLAY_MISSING_INPUT", "stage": "overlay", "error": "missing screenshot_loaded.png or controls_tree.json"})
            except Exception as oe:
                warnings.append({"code": "OVERLAY_ERROR", "stage": "overlay", "error": str(oe)})

            # 刷新 meta：warnings 与关键产物存在性（抽取到 meta_utils）
            try:
                _update_meta(out_dir, warnings=warnings)
            except Exception:
                pass

            # 可选：导出 Playwright context cookies（用于 Skill preconditions.cookies.set）
            # 注意：可能包含登录/会话等敏感信息，默认仅在显式开启 export_cookies 时写入。
            try:
                if export_cookies and context is not None:
                    try:
                        ck = context.cookies()
                    except Exception:
                        ck = []
                    if ck:
                        write_json(os.path.join(out_dir, "cookies.json"), {"cookies": ck})
            except Exception as _ce:
                warnings.append({"code": "COOKIES_EXPORT_ERROR", "stage": "cookies", "error": str(_ce)})

    except BaseException as e:
        # Fatal error occurred (e.g., launch/navigation); write failure meta.
        # Gracefully handle KeyboardInterrupt / SystemExit as aborted
        if isinstance(e, KeyboardInterrupt):
            status = "aborted"
            msg = "ABORTED_BY_USER: KeyboardInterrupt"
            error_code = "ABORTED_BY_USER"
            error_stage = error_stage or "interrupt"
        elif isinstance(e, SystemExit):
            status = "aborted"
            msg = f"ABORTED: SystemExit({getattr(e, 'code', None)})"
            error_code = "ABORTED_SYSTEM_EXIT"
            error_stage = error_stage or "interrupt"
        else:
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
        if raise_on_error and not isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise CollectError(error_code, error_stage or "unknown", msg, out_dir, e)
        if isinstance(e, KeyboardInterrupt):
            # Swallow interrupt after writing meta for graceful shutdown
            pass
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
                    "overlay_mode_loaded": overlay_mode_loaded,
                    "overlay_mode_tail": overlay_mode_tail,
                },
                "artifacts": ARTIFACTS,
            }
    finally:
        # 结束前可选延时，降低频率
        if sleep_after_seconds and sleep_after_seconds > 0:
            try:
                time.sleep(max(0, int(sleep_after_seconds)))
            except Exception:
                pass
        # 释放单实例锁
        try:
            if lock_fd is not None:
                os.close(lock_fd)
                if lock_path and os.path.exists(lock_path):
                    os.unlink(lock_path)
        except Exception:
            pass
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
    # cookies 导出（可能包含敏感信息，默认关闭）
    p.add_argument(
        "--export-cookies",
        action="store_true",
        help="Export Playwright context cookies to cookies.json under run_dir (may contain sensitive session data)",
    )
    # 自动关闭弹层相关
    p.add_argument("--no-auto-close-overlays", dest="auto_close_overlays", action="store_false", help="Disable auto-closing overlays/masks")
    p.add_argument("--overlay-close-selectors", type=str, default=None, help="Comma-separated close button selectors to try")
    p.add_argument("--no-overlay-hide-fixed-mask", dest="overlay_hide_fixed_mask", action="store_false", help="Do not hide heavy fixed masks by heuristics")
    p.add_argument("--overlay-wait-after-ms", type=int, default=300, help="Wait after overlay close/hide operations in ms (default: 300)")
    # 控件框调优：扩大到容器/膨胀像素
    p.add_argument("--expand-to-container", action="store_true", help="Expand control bbox to nearest content/container ancestor")
    p.add_argument("--bbox-inflate-px", type=int, default=0, help="Inflate final bbox by N pixels on each side")
    p.add_argument("--force-include-ids", type=str, default=None, help="Comma-separated element IDs to force include as content nodes in controls_tree (e.g., hotelSearchV1)")
    p.add_argument("--force-include-selectors", type=str, default=None, help="Comma-separated simple selectors to force include as content (supports #id, .class, tag.class, [attr=value])")
    # 默认启用对常见容器角色/关键词的自动包含，以便将“搜索表单/大组件”整块纳入控件树
    p.add_argument("--include-roles", type=str, default="search,form", help="Comma-separated roles to auto-include as container content (default: 'search,form')")
    p.add_argument("--include-class-kw", type=str, default="search,wrap,container,box", help="Comma-separated class keywords to auto-include containers (default: 'search,wrap,container,box')")
    p.add_argument("--include-min-controls", type=int, default=3, help="Auto-include containers only if subtree has at least N controls (default 3)")
    # 头显高亮控件
    p.add_argument("--live-outline-controls", action="store_true", help="In headed mode, add CSS outline to detected controls on the page")
    p.add_argument("--live-outline-color", type=str, default="rgba(255,0,0,0.9)", help="CSS color for live outline")
    p.add_argument("--live-outline-width-px", type=int, default=2, help="Outline width in px")
    # tips 导出
    p.add_argument("--no-export-tips", dest="export_tips", action="store_false", help="Do not export per-node HTML snippets under tips/")
    p.add_argument("--no-refine-parent-by-snippet", dest="refine_parent_by_snippet", action="store_false", help="Do not refine parent/children using snippet containment")
    p.add_argument("--no-annotate-controls", dest="annotate_controls", action="store_false", help="Do not annotate DOM elements with __actiontype/__selectorid")
    # 标注微探针开关与参数
    p.add_argument("--no-annotate-probe", dest="annotate_probe", action="store_false", help="Disable micro-probe during annotateControls (default: enabled)")
    p.add_argument("--annotate-probe", dest="annotate_probe", action="store_true", help="Enable micro-probe during annotateControls")
    p.add_argument("--annotate-probe-max", type=int, default=30, help="Max elements to probe in annotateControls (default: 30)")
    p.add_argument("--annotate-probe-wait-ms", type=int, default=200, help="Wait per probe step in ms (default: 200)")
    p.add_argument("--annotate-no-none", dest="annotate_no_none", action="store_true", help="Do not write __actiontype=none entries")
    # 主控件块分割
    p.add_argument("--ai-blocks", action="store_true", help="Segment main control blocks (heuristics; optional LLM later)")
    p.add_argument("--ai-blocks-max", type=int, default=8, help="Max number of blocks to keep (default: 8)")
    p.add_argument("--blocks-strict", action="store_true", help="Segment blocks with strict rules (size veto, submit required, chain compression)")
    p.add_argument("--blocks-strict-require-inner", action="store_true", help="Require inner container class keywords (inner/inner-wrap/list/items) in strict mode")
    # 默认开启严格规则 + 要求命中内层类词
    p.set_defaults(blocks_strict=True, blocks_strict_require_inner=True)
    # 控件树尺寸过滤（默认开启）
    p.add_argument("--no-filter-tree-by-size", dest="filter_tree_by_size", action="store_false", help="Do not filter controls_tree by size")
    p.add_argument("--filter-min-w", type=int, default=96)
    p.add_argument("--filter-min-h", type=int, default=80)
    p.add_argument("--filter-min-area", type=int, default=20000)
    p.add_argument("--filter-max-area-ratio", type=float, default=0.6)
    p.add_argument("--filter-cap-small-per-parent", type=int, default=12)
    p.add_argument("--no-filter-keep-important", dest="filter_keep_important", action="store_false", help="Do not keep important nodes (submit/control) when filtering")
    # 交互图构建（可选）
    p.add_argument("--explore-graph", action="store_true", help="Build interaction graphs for blocks (graphs/graph_*.json)")
    p.add_argument("--explore-graph-max-ops", type=int, default=20, help="Max ops per block for graph exploration (default: 20)")
    p.add_argument("--explore-graph-wait-ms", type=int, default=500, help="Wait per op in ms for graph exploration (default: 500)")
    # 默认开启交互图构建
    p.set_defaults(explore_graph=True)
    # 交互式展开开关与参数（默认开启）
    p.add_argument("--no-interactive-reveal", dest="interactive_reveal", action="store_false", help="Disable interactive reveal phase")
    p.add_argument("--reveal-max-actions", type=int, default=8, help="Max actions in interactive reveal (default: 8)")
    p.add_argument("--reveal-total-budget-ms", type=int, default=15000, help="Total time budget for reveal in ms (default: 15000)")
    p.add_argument("--reveal-wait-ms", type=int, default=800, help="Wait after each reveal action in ms (default: 800)")
    # 频率与单实例控制
    p.add_argument("--disable-proxy", action="store_true", help="Launch browser with --no-proxy-server and bypass list")
    p.add_argument("--single-instance", action="store_true", help="Enable single-instance lock to avoid concurrent runs")
    p.add_argument("--lock-file", type=str, default="workspace/.detect.lock", help="Path to single-instance lock file (default: workspace/.detect.lock)")
    p.add_argument("--min-interval-seconds", type=int, default=0, help="Min interval between runs; if recent run exists, sleep remaining time")
    p.add_argument("--jitter-seconds", type=int, default=0, help="Additional random jitter seconds to add to interval sleep")
    p.add_argument("--rate-state-file", type=str, default=None, help="Path to store last-run timestamp (default: <out_root>/.detect_rate.json)")
    p.add_argument("--sleep-after-seconds", type=int, default=0, help="Sleep seconds after finishing, to slow down subsequent actions")
    # 等待资源：默认开启；提供关闭开关
    p.add_argument("--no-ensure-images", dest="ensure_images", action="store_false", help="Disable waiting for viewport images")
    p.add_argument("--images-wait-timeout-ms", type=int, default=30000, help="Timeout for images/bg ready wait (default: 30000)")
    p.add_argument("--images-max-count", type=int, default=256, help="Only check first N visible images (default: 256)")
    p.add_argument("--no-ensure-backgrounds", dest="ensure_backgrounds", action="store_false", help="Disable waiting for CSS background images in viewport")
    # 默认开启 ensure_* / extract_snippets / auto_close_overlays
    p.set_defaults(ensure_images=True, ensure_backgrounds=True, extract_snippets=True, auto_close_overlays=True, overlay_hide_fixed_mask=True, disable_proxy=False, single_instance=False)
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
    # Overlay 模式：auto/page/viewport（auto 对 loaded/loaded_cropped/tail 采用 page）
    p.add_argument("--overlay-mode-loaded", type=str, choices=["auto", "page", "viewport"], default="auto", help="Overlay mode for loaded/loaded_cropped overlays")
    p.add_argument("--overlay-mode-tail", type=str, choices=["auto", "page", "viewport"], default="auto", help="Overlay mode for scrolled_tail overlay")
    p.add_argument("--no-extract-snippets", dest="extract_snippets", action="store_false", help="Do not extract first-layer control DOM snippets")
    # 运行模式
    p.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Run browser in headed mode (default: headed)",
    )
    # 默认使用有头浏览器，便于调试与观察；如需无头模式可在代码调用时显式传 headless=True
    p.set_defaults(headless=False)
    p.add_argument("--human-verify", action="store_true", help="Pause after navigation to allow manual verification (slider/CAPTCHA)")
    # 日志开关
    p.add_argument("--verbose", action="store_true", help="Enable verbose logging (default: on)")
    p.add_argument("--no-verbose", dest="verbose", action="store_false", help="Disable verbose logging")
    p.set_defaults(verbose=True)
    args = p.parse_args()
    # 仅在提供 --config 时载入用户配置；不再自动加载默认配置文件
    cfg = load_json_config(args.config)
    # 允许通过 JSON 配置与环境变量覆盖部分默认参数；CLI 最终优先。
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
        "headless", "human_verify", "overlay_mode_loaded", "overlay_mode_tail",
        "verbose",
        # include/force selectors/id for controls_tree
        "force_include_ids", "force_include_selectors", "include_roles", "include_class_kw", "include_min_controls",
        # reveal phase params (optional)
        "reveal_max_actions", "reveal_total_budget_ms", "reveal_wait_ms",
        # annotate/probe
        "annotate_probe", "annotate_probe_max", "annotate_probe_wait_ms", "annotate_no_none",
        "annotate_controls", "export_tips", "refine_parent_by_snippet",
        # tree size filter
        "filter_tree_by_size", "filter_min_w", "filter_min_h", "filter_min_area", "filter_max_area_ratio", "filter_cap_small_per_parent", "filter_keep_important",
        # graph/blocks
        "ai_blocks", "ai_blocks_max", "blocks_strict", "blocks_strict_require_inner",
        "explore_graph", "explore_graph_max_ops", "explore_graph_wait_ms",
        # rate-limit / single instance
        "disable_proxy", "single_instance", "lock_file",
        "min_interval_seconds", "jitter_seconds", "rate_state_file", "sleep_after_seconds",
        # visual helpers
        "live_outline_controls", "live_outline_color", "live_outline_width_px",
        # cookies
        "export_cookies",
    }

    def _env_get(name: str):
        """从环境变量 AFC_DETECT_<UPPER(name)> 读取覆盖值。"""
        env_key = "AFC_DETECT_" + name.upper()
        return os.getenv(env_key, None)

    def _cast_like(value, default):
        if value is None:
            return default
        # bool
        if isinstance(default, bool):
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        # int
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(value)
            except Exception:
                return default
        # float
        if isinstance(default, float):
            try:
                return float(value)
            except Exception:
                return default
        # string或其他
        return str(value)

    def cfg_get(name, default):
        # 1) JSON 配置优先（若存在该键）
        if isinstance(cfg, dict) and name in allowed and name in cfg:
            return cfg.get(name, default)
        # 2) 环境变量 AFC_DETECT_<NAME> 作为默认值覆盖
        if name in allowed:
            env_v = _env_get(name)
            if env_v is not None:
                return _cast_like(env_v, default)
        # 3) 回退 CLI/default
        return default
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
        overlay_mode_loaded=cfg_get("overlay_mode_loaded", args.overlay_mode_loaded),
        overlay_mode_tail=cfg_get("overlay_mode_tail", args.overlay_mode_tail),
        auto_close_overlays=cfg_get("auto_close_overlays", args.auto_close_overlays),
        overlay_close_selectors=cfg_get("overlay_close_selectors", args.overlay_close_selectors),
        overlay_hide_fixed_mask=cfg_get("overlay_hide_fixed_mask", args.overlay_hide_fixed_mask),
        overlay_wait_after_ms=cfg_get("overlay_wait_after_ms", args.overlay_wait_after_ms),
        extract_snippets=cfg_get("extract_snippets", args.extract_snippets),
        disable_proxy=cfg_get("disable_proxy", args.disable_proxy),
        single_instance_lock=(cfg_get("lock_file", args.lock_file) if cfg_get("single_instance", args.single_instance) else None),
        min_interval_seconds=cfg_get("min_interval_seconds", args.min_interval_seconds),
        jitter_seconds=cfg_get("jitter_seconds", args.jitter_seconds),
        rate_state_file=cfg_get("rate_state_file", args.rate_state_file),
        sleep_after_seconds=cfg_get("sleep_after_seconds", args.sleep_after_seconds),
        live_outline_controls=cfg_get("live_outline_controls", args.live_outline_controls),
        live_outline_color=cfg_get("live_outline_color", args.live_outline_color),
        live_outline_width_px=cfg_get("live_outline_width_px", args.live_outline_width_px),
        expand_to_container=cfg_get("expand_to_container", args.expand_to_container),
        bbox_inflate_px=cfg_get("bbox_inflate_px", args.bbox_inflate_px),
        force_include_ids=cfg_get("force_include_ids", args.force_include_ids),
        force_include_selectors=cfg_get("force_include_selectors", args.force_include_selectors),
        include_roles=cfg_get("include_roles", args.include_roles),
        include_class_kw=cfg_get("include_class_kw", args.include_class_kw),
        include_min_controls=cfg_get("include_min_controls", args.include_min_controls),
        export_tips=cfg_get("export_tips", args.export_tips),
        refine_parent_by_snippet=cfg_get("refine_parent_by_snippet", getattr(args, "refine_parent_by_snippet", True)),
        annotate_controls=cfg_get("annotate_controls", getattr(args, "annotate_controls", True)),
        annotate_probe=cfg_get("annotate_probe", getattr(args, "annotate_probe", True)),
        annotate_probe_max=cfg_get("annotate_probe_max", getattr(args, "annotate_probe_max", 30)),
        annotate_probe_wait_ms=cfg_get("annotate_probe_wait_ms", getattr(args, "annotate_probe_wait_ms", 200)),
        annotate_no_none=cfg_get("annotate_no_none", getattr(args, "annotate_no_none", False)),
        ai_blocks=cfg_get("ai_blocks", getattr(args, "ai_blocks", False)),
        ai_blocks_max=cfg_get("ai_blocks_max", getattr(args, "ai_blocks_max", 8)),
        blocks_strict=cfg_get("blocks_strict", getattr(args, "blocks_strict", True)),
        blocks_strict_require_inner=cfg_get("blocks_strict_require_inner", getattr(args, "blocks_strict_require_inner", True)),
        filter_tree_by_size=cfg_get("filter_tree_by_size", getattr(args, "filter_tree_by_size", True)),
        filter_min_w=cfg_get("filter_min_w", getattr(args, "filter_min_w", 96)),
        filter_min_h=cfg_get("filter_min_h", getattr(args, "filter_min_h", 80)),
        filter_min_area=cfg_get("filter_min_area", getattr(args, "filter_min_area", 20000)),
        filter_max_area_ratio=cfg_get("filter_max_area_ratio", getattr(args, "filter_max_area_ratio", 0.6)),
        filter_cap_small_per_parent=cfg_get("filter_cap_small_per_parent", getattr(args, "filter_cap_small_per_parent", 12)),
        filter_keep_important=cfg_get("filter_keep_important", getattr(args, "filter_keep_important", True)),
        explore_graph=cfg_get("explore_graph", getattr(args, "explore_graph", False)),
        explore_graph_max_ops_per_block=cfg_get("explore_graph_max_ops", getattr(args, "explore_graph_max_ops", 20)),
        explore_graph_wait_ms=cfg_get("explore_graph_wait_ms", getattr(args, "explore_graph_wait_ms", 500)),
        interactive_reveal=cfg_get("interactive_reveal", getattr(args, "interactive_reveal", True)),
        reveal_max_actions=cfg_get("reveal_max_actions", args.reveal_max_actions),
        reveal_total_budget_ms=cfg_get("reveal_total_budget_ms", args.reveal_total_budget_ms),
        reveal_wait_ms=cfg_get("reveal_wait_ms", args.reveal_wait_ms),
        verbose=cfg_get("verbose", args.verbose),
        export_cookies=cfg_get("export_cookies", getattr(args, "export_cookies", False)),
    )
    if args.return_info:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
