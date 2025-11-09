"""
detect.scrolling
页面滚动相关逻辑，优先调用页面端 JS 助手，失败时回退内联方案。
"""

import time


def auto_scroll_full_page(page, max_steps: int = 50, delay_ms: int = 200) -> bool:
    """逐步滚动到页面底部以触发懒加载。

    返回：是否到达底部（或接近底部）。
    """
    reached_bottom = False
    for _ in range(max_steps):
        try:
            try:
                reached_bottom = page.evaluate("() => window.DetectHelpers.scrollStep()")
            except Exception:
                reached_bottom = page.evaluate(
                    """
                    () => {
                      const y = window.scrollY || window.pageYOffset || 0;
                      const h = window.innerHeight || 0;
                      const sh = Math.max(
                        document.body?.scrollHeight || 0,
                        document.documentElement?.scrollHeight || 0
                      );
                      if (y + h >= sh - 2) return true;
                      window.scrollBy(0, Math.max(64, Math.floor(h * 0.9)));
                      return false;
                    }
                    """
                )
            if reached_bottom:
                break
            time.sleep(max(0.0, delay_ms / 1000.0))
        except Exception:
            break
    return reached_bottom


def scroll_by_distance(page, total_px: int, *, step_px: int = 200, delay_ms: int = 200) -> bool:
    """以小步前进的方式在页面上滚动给定总距离。

    返回：是否到达页面底部（或接近底部）。
    """
    try:
        total_px = int(total_px)
        step_px = max(1, int(step_px))
        delay_ms = max(0, int(delay_ms))
    except Exception:
        total_px = int(total_px or 0)
        step_px = max(1, int(step_px or 200))
        delay_ms = max(0, int(delay_ms or 200))

    if total_px <= 0:
        # 不滚动，直接检测是否在底部
        try:
            return page.evaluate(
                "() => { const y=window.scrollY||window.pageYOffset||0; const h=window.innerHeight||0; const sh=Math.max(document.body?.scrollHeight||0, document.documentElement?.scrollHeight||0); return y + h >= sh - 2; }"
            )
        except Exception:
            return False

    scrolled = 0
    reached = False
    for _ in range(max(1, (total_px + step_px - 1) // step_px)):
        step = min(step_px, total_px - scrolled)
        if step <= 0:
            break
        try:
            page.evaluate("(dy) => window.scrollBy(0, dy)", int(step))
        except Exception:
            break
        scrolled += step
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        try:
            # 早停：已接近底部
            reached = page.evaluate(
                "() => { const y=window.scrollY||window.pageYOffset||0; const h=window.innerHeight||0; const sh=Math.max(document.body?.scrollHeight||0, document.documentElement?.scrollHeight||0); return y + h >= sh - 2; }"
            )
            if reached:
                break
        except Exception:
            pass
    return bool(reached)
