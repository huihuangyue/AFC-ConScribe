from playwright.sync_api import sync_playwright
import os

"""
DEMO: 使用 remote_ws 连接到外部 Playwright 服务的示例脚本。

说明：
  - 当前 front/docker 镜像已不再内置 `playwright run-server`（不再在 4455 端口启动服务），
    因此本脚本不会连接到本仓库的容器；
  - 若需要测试 remote_ws 模式，请自行启动一个 Playwright Remote Server，并在环境变量中设置：
        AFC_PLAYWRIGHT_REMOTE_WS=ws://your-host:port/playwright
"""

REMOTE_WS_URL = os.getenv("AFC_PLAYWRIGHT_REMOTE_WS", "").strip()


def run_remote_playwright():
    if not REMOTE_WS_URL:
        print(
            "[run_automation] 未设置 AFC_PLAYWRIGHT_REMOTE_WS，本脚本仅作为示例，不会连接当前容器。\n"
            "如需使用，请自行启动 Playwright Remote Server，并设置该环境变量。"
        )
        return

    with sync_playwright() as p:
        print(f"尝试连接到远程浏览器: {REMOTE_WS_URL}")

        try:
            browser = p.chromium.connect(REMOTE_WS_URL)
            print("连接成功！")
        except Exception as e:
            print(f"连接失败，请检查远程 Playwright 服务是否运行。错误: {e}")
            return

        context = browser.new_context()
        page = context.new_page()

        print("导航到 Google...")
        page.goto("https://www.google.com")

        print("在搜索框中输入...")
        page.fill('textarea[name="q"]', "如何构建浏览器智能体")

        print("点击搜索按钮...")
        page.press('textarea[name="q"]', "Enter")

        print(f"当前页面标题: {page.title()}")
        print("自动化完成。保持连接 10 秒供观察...")
        page.wait_for_timeout(10000)

        browser.close()
        print("浏览器连接关闭。")


if __name__ == "__main__":
    run_remote_playwright()
