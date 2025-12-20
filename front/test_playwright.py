from playwright.sync_api import sync_playwright
import time

def run():
    print("启动 Playwright...")
    with sync_playwright() as p:
        # 【关键】headless=False 让浏览器以有头模式运行，这样才能显示在桌面上
        browser = p.chromium.launch(headless=False, slow_mo=1000) # slow_mo 慢放动作方便观看
        context = browser.new_context()
        page = context.new_page()
        
        print("正在打开 Bilibili...")
        page.goto("https://www.bilibili.com")
        
        print(f"当前页面标题: {page.title()}")
        
        # 在这里停 10 秒，让你好好看看浏览器
        print("浏览器已打开，请观看屏幕 10 秒...")
        time.sleep(10)
        
        # 截图纪念
        page.screenshot(path="bilibili_docker.png")
        print("已截图保存为 bilibili_docker.png")
        
        browser.close()
        print("测试结束。")

if __name__ == "__main__":
    run()