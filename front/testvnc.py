import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def test_visual_browser():
    # 1. 设置选项
    chrome_options = Options()
    
    # 【关键点】绝对不要开启 headless 模式，否则浏览器会在后台运行，你看不到它
    # chrome_options.add_argument("--headless") 

    print("正在连接容器...")
    
    # 2. 连接到 Docker (注意端口是 4444，不是 7777)
    driver = webdriver.Remote(
        command_executor='http://localhost:4444/wd/hub',
        options=chrome_options
    )

    try:
        print("连接成功！浏览器应该弹出来了！")
        
        # 3. 让浏览器打开一个网页
        driver.get("https://www.google.com")
        
        # 4. 强制等待 10 秒，让你有时间看一眼 localhost:7777
        print("请查看 localhost:7777 窗口，保持 10 秒...")
        time.sleep(10)
        
        print(f"当前网页标题: {driver.title}")

    finally:
        # 5. 关闭浏览器
        driver.quit()
        print("测试结束，浏览器已关闭。")

if __name__ == "__main__":
    test_visual_browser()