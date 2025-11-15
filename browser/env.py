"""
Playwright-backed BrowserEnv used to execute Skill Program code.

Methods provided are intentionally minimal and stable for program code:
  - current_url() -> str
  - exists(selector, *, timeout_ms=None) -> bool
  - click(selector, *, timeout_ms=None) -> None
  - type(selector, text, *, delay_ms=None) -> None
  - select(selector, value) -> None
  - press(selector, key) -> None
  - wait_for_selector(selector, *, state='visible', timeout_ms=None) -> None
  - viewport_size() -> (width, height)
  - scroll_into_view(selector) -> None

Usage:
  from browser.env import make_env
  with make_env(url, headless=True) as env:
      program(env, locators, args, options)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional, Tuple, List, Dict, Any

from playwright.sync_api import sync_playwright


class PWEnv:
    def __init__(self, page) -> None:
        self._page = page

    # Query and navigation
    def current_url(self) -> str:
        try:
            return self._page.url or ""
        except Exception:
            return ""

    def exists(self, selector: str, *, timeout_ms: Optional[int] = None) -> bool:
        try:
            loc = self._page.locator(selector).first
            if callable(loc):
                loc = loc()
            if timeout_ms is not None:
                return loc.count() > 0 and loc.wait_for(state="attached", timeout=max(1, int(timeout_ms))) is None
            return loc.count() > 0
        except Exception:
            return False

    # Actions
    def click(self, selector: str, *, timeout_ms: Optional[int] = None) -> None:
        self._page.locator(selector).first.click(timeout=None if timeout_ms is None else int(timeout_ms))

    def type(self, selector: str, text: str, *, delay_ms: Optional[int] = None) -> None:
        self._page.locator(selector).first.fill("")
        self._page.locator(selector).first.type(text, delay=delay_ms or 0)

    def select(self, selector: str, value: str) -> None:
        self._page.locator(selector).first.select_option(value=value)

    def press(self, selector: str, key: str) -> None:
        self._page.locator(selector).first.press(key)

    def wait_for_selector(self, selector: str, *, state: str = "visible", timeout_ms: Optional[int] = None) -> None:
        self._page.wait_for_selector(selector, state=state, timeout=None if timeout_ms is None else int(timeout_ms))

    def viewport_size(self) -> Tuple[int, int]:
        try:
            vs = self._page.viewport_size
            if isinstance(vs, dict):
                return int(vs.get("width", 0)), int(vs.get("height", 0))
        except Exception:
            pass
        return (0, 0)

    def scroll_into_view(self, selector: str) -> None:
        try:
            self._page.locator(selector).first.scroll_into_view_if_needed()
        except Exception:
            pass

    # Visual highlight helpers
    def highlight(self, selector: str, *, color: str = "rgba(255,0,0,0.9)", width: int = 2) -> None:
        """Add an outline to matched elements; mark them with data-afc-highlight attribute."""
        try:
            self._page.evaluate(
                "(sel, color, w) => {\n"
                "  const list = document.querySelectorAll(sel);\n"
                "  for (const el of list) {\n"
                "    try {\n"
                "      el.style.setProperty('outline', `${w}px solid ${color}`, 'important');\n"
                "      el.setAttribute('data-afc-highlight', '1');\n"
                "    } catch(_){}\n"
                "  }\n"
                "}",
                selector,
                color,
                int(width),
            )
        except Exception:
            pass

    def clear_highlights(self, selector: Optional[str] = None) -> None:
        """Remove outlines added by highlight(). If selector is None, clear all marked elements."""
        try:
            if selector:
                self._page.evaluate(
                    "(sel) => {\n"
                    "  const list = document.querySelectorAll(sel);\n"
                    "  for (const el of list) {\n"
                    "    try { el.style.removeProperty('outline'); el.removeAttribute('data-afc-highlight'); } catch(_){}\n"
                    "  }\n"
                    "}",
                    selector,
                )
            else:
                self._page.evaluate(
                    "() => {\n"
                    "  const list = document.querySelectorAll('[data-afc-highlight]');\n"
                    "  for (const el of list) {\n"
                    "    try { el.style.removeProperty('outline'); el.removeAttribute('data-afc-highlight'); } catch(_){}\n"
                    "  }\n"
                    "}",
                )
        except Exception:
            pass

    # Click flash helpers
    def enable_click_flash(self, *, color: str = "rgba(255,215,0,0.5)", duration_ms: int = 1000, mode: str = "background") -> None:
        """Install a document-level click listener that flashes the target element.

        mode: 'background' (default) sets backgroundColor; 'outline' sets outline.
        """
        js = (
            "(cfg)=>{\n"
            "  if (window.__afcClickFlashInstalled) return;\n"
            "  const color = cfg && cfg.color || 'rgba(255,215,0,0.5)';\n"
            "  const dur = Math.max(0, (cfg && cfg.duration_ms) || 1000);\n"
            "  const mode = (cfg && cfg.mode) || 'background';\n"
            "  const handler = (e)=>{\n"
            "    try {\n"
            "      let el = e.target;\n"
            "      if (!el || !(el instanceof Element)) return;\n"
            "      const target = el.closest('*');\n"
            "      if (!target) return;\n"
            "      if (mode === 'outline') {\n"
            "        const prev = target.style.outline;\n"
            "        target.style.setProperty('outline', `2px solid ${color}`, 'important');\n"
            "        setTimeout(()=>{ try{ target.style.outline = prev || ''; }catch(_){} }, dur);\n"
            "      } else {\n"
            "        const prev = target.style.backgroundColor;\n"
            "        target.style.setProperty('transition', 'background-color 120ms ease');\n"
            "        target.style.backgroundColor = color;\n"
            "        setTimeout(()=>{ try{ target.style.backgroundColor = prev || ''; }catch(_){} }, dur);\n"
            "      }\n"
            "    } catch(_){}\n"
            "  };\n"
            "  window.addEventListener('click', handler, true);\n"
            "  window.__afcClickFlashInstalled = true;\n"
            "  window.__afcClickFlashHandler = handler;\n"
            "}"
        )
        try:
            self._page.add_init_script(js, {"color": color, "duration_ms": int(duration_ms), "mode": mode})
            self._page.evaluate(js, {"color": color, "duration_ms": int(duration_ms), "mode": mode})
        except Exception:
            pass

    def disable_click_flash(self) -> None:
        try:
            self._page.evaluate(
                "()=>{ if (window.__afcClickFlashHandler) { window.removeEventListener('click', window.__afcClickFlashHandler, true); delete window.__afcClickFlashHandler; } window.__afcClickFlashInstalled=false; }"
            )
        except Exception:
            pass


def _sanitize_cookies(cookies: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in (cookies or []):
        try:
            name = str(c.get("name") or "").strip()
            value = str(c.get("value") or "").strip()
            if not name:
                continue
            item: Dict[str, Any] = {"name": name, "value": value}
            # Either provide url or (domain, path)
            url = c.get("url")
            domain = c.get("domain")
            path = c.get("path") or "/"
            if url:
                item["url"] = str(url)
            elif domain:
                item["domain"] = str(domain)
                item["path"] = str(path)
            # Optional attributes
            for k in ("expires", "httpOnly", "secure", "sameSite"):  # sameSite: 'Lax' | 'Strict' | 'None'
                if k in c:
                    item[k] = c[k]
            out.append(item)
        except Exception:
            continue
    return out


@contextmanager
def make_env(
    url: Optional[str] = None,
    *,
    headless: bool = True,
    slow_mo: Optional[int] = None,
    default_timeout_ms: Optional[int] = None,
    auto_close: bool = True,
    cookies: Optional[List[Dict[str, Any]]] = None,
):
    """Context manager to create a PWEnv ready for Program execution.

    headless=True by default; set False to run headed.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=(slow_mo or 0))
        context = browser.new_context()
        # Pre-set cookies on the fresh context if provided
        try:
            ck = _sanitize_cookies(cookies)
            if ck:
                context.add_cookies(ck)
        except Exception:
            pass
        page = context.new_page()
        if isinstance(default_timeout_ms, int) and default_timeout_ms > 0:
            try:
                page.set_default_timeout(int(default_timeout_ms))
            except Exception:
                pass
        if url:
            page.goto(url, wait_until="domcontentloaded")
        try:
            yield PWEnv(page)
        finally:
            if auto_close:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
