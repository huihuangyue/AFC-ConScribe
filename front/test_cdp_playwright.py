from __future__ import annotations

"""
最小 CDP 联调脚本：

目的：
  - 使用 connect_over_cdp 连接到容器里那只“挂在 VNC 上”的 Chrome（remote-debugging-port=9222）；
  - 在其中打开一个网页，确认动作能出现在 http://localhost:7777 的画面里。

使用方式（在仓库根目录）：
  1) 确保 front/docker-compose.yml 已经启动：
       cd front && docker-compose up --build
  2) 在另一个终端执行：
       conda activate afc-conscribe
       python front/test_cdp_playwright.py
  3) 在浏览器打开 http://localhost:7777/?autoconnect=1&resize=scale&password=secret
     观察 Chrome 是否自动打开指定页面。
"""

import os
import json
from urllib.request import urlopen
from playwright.sync_api import sync_playwright


def main() -> None:
    # 默认使用通过 socat 暴露的 9223 端口
    cdp_url = os.getenv("AFC_PLAYWRIGHT_CDP_URL", "http://localhost:9223")
    print(f"[test_cdp] 尝试通过 CDP 连接到已有 Chrome: {cdp_url}")

    # 先手动解析 /json/version，拿到 webSocketDebuggerUrl，避免 Playwright 内部 HTTP 客户端的问题。
    ws_endpoint = cdp_url
    if cdp_url.startswith("http://") or cdp_url.startswith("https://"):
        try:
            meta_url = cdp_url.rstrip("/") + "/json/version"
            print(f"[test_cdp] 拉取 DevTools 元信息: {meta_url}")
            with urlopen(meta_url, timeout=3.0) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            meta = json.loads(raw) if raw else {}
            ws = meta.get("webSocketDebuggerUrl") or ""
            if isinstance(ws, str) and ws.strip():
                ws_endpoint = ws.strip()
                print(f"[test_cdp] 解析到 webSocketDebuggerUrl: {ws_endpoint}")
        except Exception as e:
            print(f"[test_cdp] 解析 /json/version 失败，退回使用原始端点: {e}")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint)
        print("[test_cdp] connect_over_cdp 成功，当前 contexts 数量:", len(browser.contexts))

        # 复用现有 context（通常是默认的第一个），否则新建一个
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = browser.new_context()

        page = context.new_page()
        target_url = "https://www.ctrip.com"  # 或 https://www.taobao.com"
        print(f"[test_cdp] 在可视 Chrome 中打开: {target_url}")
        page.goto(target_url, wait_until="load")
        print("[test_cdp] 当前页面标题:", page.title())

        print("[test_cdp] 保持 15 秒供观察 VNC 画面...")
        page.wait_for_timeout(15000)

        # 不关闭 browser，让你手动观察；脚本结束后连接会自动断开。


if __name__ == "__main__":
    main()
