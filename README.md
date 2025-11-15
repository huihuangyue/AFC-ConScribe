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
  
- 默认滚动策略：
- 预热滚动（默认开启）：在任何采集动作前按“等待 → 慢速下拉 → 再等待”的节奏进行预热。默认参数：
  - `--prewarm-wait-before-ms 1200`
  - 步进模式（默认）：`--prewarm-max-steps 3`、`--prewarm-delay-ms 1500`
  - `--prewarm-wait-after-ms 1200`
  - 可选改为按距离滚动：`--prewarm-scroll-ratio <0~1>` 或 `--prewarm-scroll-pixels <px>`（与步骤上限共同生效）
  可用 `--no-prewarm` 关闭。
- 截图前滚动：在“整页截图”前仍会再做一轮轻量自动滚动（默认 3 步、步间 1200ms），随后会重新抽取 DOM 简表并用于控件树生成，最后输出整页截图（fullPage）。可用 `--autoscroll-max-steps`、`--autoscroll-delay-ms` 调整，或 `--no-auto-scroll` 关闭。

截图稳定性优化：
- 在生成 `screenshot_initial.png` 与 `screenshot_loaded.png` 前，默认会等待 2 帧 rAF 并额外静默 200ms（`--stabilize-frames` / `--stabilize-wait-ms` 可调），以降低过渡动画和微抖动带来的偏差。

资源就绪等待（默认开启）：
- 默认会等待“视口内图片（<img>）”与“CSS 背景图（background-image）”就绪后再继续采集与截图：
  - 图片/背景等待超时：默认 45s（可用 `--images-wait-timeout-ms` 改）
  - 最大检查张数：`--images-max-count 256`
- 如需关闭等待，可使用：`--no-ensure-images`、`--no-ensure-backgrounds`。

容器拼接（长图）默认关闭：
- 若页面存在内部可滚动大容器，采集可尝试“容器感知拼接”；但为避免误选小容器与接缝问题，默认关闭，始终回退为整页截图兜底（`screenshot_scrolled_tail.png`）。
- 可通过 `--container-stitch` 显式开启；并可用 `--max-stitch-seconds`/`--max-stitch-segments`/`--max-stitch-pixels` 控制拼接预算。
- 长图裁剪与清理（默认开启）：
  - 依据控件/内容最大 bottom + 边距，以及自底向上“方差扫描”清理尾部空白/未加载块，输出裁剪版：
    - `screenshot_loaded_cropped.png`、`screenshot_loaded_cropped_overlay.png`
  - 裁剪上限：最多保留 4 屏（可在配置中调 `crop_max_screens`）
python -m skill.select --run-dir "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251116032614" --top-k 999 | while read -r sel; do python -m skill.generate --run-dir "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251116032614" --selector "$sel" --with-codegen; done
python -m browser.invoke --skill "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112150152/new_skill/Skill_kakxi_d321.json" --invoke $'search_hotel(\npage,\ndestination="上海",\ncheckin_year=2025, checkin_month=12, checkin_day=17,\ncheckout_year=2025, checkout_month=12, checkout_day=25,\nrooms=1, adults=2, children=4,\nstar_ratings=["五星（钻）"],\nkeyword="外滩"\n)' --slow-mo-ms 150 --keep-open（运行）
python -m browser.invoke --skill "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112150152/old_skill/Skill_kakxi_d321.json" --invoke $'perform_hotel_search(\n page,\n destination="上海",\n check_in_date=datetime(2025,11,12),\n check_out_date=datetime(2025,11,13),\n rooms=2,\n adults=3,\n star_level="四星"\n)' --slow-mo-ms 150 --default-timeout-ms 12000 --keep-open
python -m skill.build --run-dir /mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112014916 --out /mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112014916/new_skill

REPAIR_USE_LLM=1 python -m aid.repair --skill "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112014916/skill/Skill_kakxi_d321.json" --new-run-dir "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112143400" --old-run-dir "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112014916" --out "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112143400/repair_skill/Skill_kakxi_d321_repaired.json" --log-dir "/mnt/f/paperwork/AFC-ConScribe/workspace/data/ctrip_com/20251112143400/repair_skill/_logs" --use-llm-locators --use-llm-preconditions --use-llm-program --use-llm-naming

