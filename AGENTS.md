# 仓库开发指南（中文）
使用中文进行交流，你是一个会反复核查的专家，保持怀疑态度，并会做研究，我不总是对的，你也不是，但我们都追求正确。
## 项目结构与模块组织
- `detect/` —— 采集与辅助模块（Python + Playwright）：
  - `collect_playwright.py`（CLI 与 API 入口）
  - `utils.py`、`scrolling.py`、`context_utils.py`、`constants.py`、`errors.py`
  - 页面端脚本：`collect_playwright.js`（DOM 摘要/可见性/遮挡/导航时序/滚动等）
- `MVFN/mvfn_lite/` —— MVFN 离线管线的设计文档（当前无代码）。
- `data/` —— 采集产物：`data/<domain_sanitized>/<YYYYMMDDHHMMSS>/`（截图/DOM/AX/meta 等）。避免提交过大的产物。
- `workspace/` —— 本地工作区（已 git 忽略）。通用文档见 `README.md` 与 `agent.md`。依赖清单：`requirement.txt`。

## 构建、开发与运行命令
- 环境（Python 3.10+）：`conda create -n afc-conscribe python=3.10 && conda activate afc-conscribe`
- 安装依赖：`pip install -r requirement.txt`
- 安装浏览器内核：`python -m playwright install chromium`
- 安装 Tesseract（OCR）：
  - macOS：`brew install tesseract`
  - Ubuntu/Debian：`sudo apt-get install tesseract-ocr`
  - Windows：安装后将目录加入 `PATH`
- 可选 `.env`（外接 LLM）：`AFC_LLM_PROVIDER`、`AFC_LLM_MODEL`、`AFC_LLM_API_KEY`
- 运行采集：`python detect/collect_playwright.py https://www.example.com`
  - 可选参数：`--device`、`--viewport 1148x1622`、`--dpr 1.0`、`--return-info`
- 测试（添加后）：`pytest -q`

## 代码风格与命名规范
- Python 风格：PEP 8、4 空格缩进、类型注解；适合时使用 dataclass；函数尽量小且职责单一，模块边界清晰。
- 命名：模块/文件 `lower_snake_case.py`；函数/变量 `lower_snake_case`；类 `UpperCamelCase`；常量 `UPPER_SNAKE_CASE`（参见 `detect/constants.py`）。
- 错误处理：采集致命错误抛出 `CollectError`；库代码优先返回结构化数据，少用 `print`。

## 测试规范
- 测试框架：`pytest`（作为开发依赖）。测试放在 `tests/` 或 `detect/tests/`，文件命名 `test_*.py`。
- 重点：确定性单测——mock Playwright、使用小型 HTML fixture，避免网络依赖。慢测/真机跑用 `@pytest.mark.slow` 标注。
- 覆盖优先：解析/域名清洗/路径生成/错误处理等纯函数逻辑。

## 提交与 PR 规范
- 提交信息：使用祈使语；推荐 Conventional Commits（如 `feat(detect): add auto-scroll fallback`、`fix: handle NAV_TIMEOUT`）。
- PR 内容：说明做了什么/为什么、关联 issue、复现命令，并在需要时附 `data/<domain>/<ts>/` 列表或截图。范围要聚焦，若 CLI 或产出有变化请同步文档。

## 安全与配置建议
- 不要提交密钥；`.env` 已忽略。请尽量减少向仓库提交的 `data/` 内容，优先使用匿名化的小样本。Windows 环境下运行 OCR 前确保 Tesseract 在 `PATH` 中。
