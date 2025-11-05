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
