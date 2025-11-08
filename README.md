## 语言与系统
- Python 3.10+（Windows/macOS/Linux 均可）

## 创建 Conda 虚拟环境
- `conda create -n afc-conscribe python=3.10`
- `conda activate afc-conscribe`

## 安装依赖（requirement.txt）
- `pip install -r requirement.txt`
- 安装 Playwright 浏览器内核：`python -m playwright install chromium`

## 安装 Tesseract（OCR 可执行程序）
- macOS：`brew install tesseract`
- Ubuntu/Debian：`sudo apt-get install tesseract-ocr`
- Windows：下载安装官方包并将安装目录加入 PATH（例如 `C:\Program Files\Tesseract-OCR`）

## 环境变量（外接 LLM 可选）
- 在项目根目录创建 `.env` 并填写：
  - `AFC_LLM_PROVIDER=openai`
  - `AFC_LLM_MODEL=gpt-4o-mini`
  - `AFC_LLM_API_KEY=你的密钥`

## 采集 Detect 使用（Python + Playwright）
- 脚本：`detect/collect_playwright.py`
- 运行：`python detect/collect_playwright.py https://www.example.com`
- 产物目录：`workspace/data/<domain_sanitized>/<YYYYMMDDHHMMSS>/`
- 产物文件：
  - `screenshot_initial.png`：DOMContentLoaded 后全页截图
  - `screenshot_loaded.png`：load（+networkidle 如达成）后全页截图
  - `dom.html`：页面 outerHTML
  - `dom_summary.json`：DOM 简表（tag/id/class/role/visible/bbox/text）
  - `ax.json`：可访问性树快照（AXTree）
  - `meta.json`：URL/UA/viewport/时区偏移/状态/版本
  - `timings.json`：Navigation Timing（v2 或 legacy）
