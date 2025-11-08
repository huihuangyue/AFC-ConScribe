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

