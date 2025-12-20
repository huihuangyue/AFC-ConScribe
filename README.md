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

python -m planner.run_task \
  --run-dir "workspace/data/ctrip_com/20251116234238" \
  --task "在携程首页搜索上海 2025年11月19日入住 11月23日退房 1间房 2位成人 0儿童 五星（钻），在外滩"
python -m planner.run_task   --run-dir "workspace/data/ctrip_com/20251116234238"   --task "元旦节我们一家5口要去北京长城旅游，帮我找一下那个时候的旅店信息"
python -m skill.select --run-dir "$RUN_DIR" --top-k 20
python -m skill.select \
  --run-dir "$RUN_DIR" \
  --top-k 20 \
  --min-children 1 \
  --no-require-submit \
  --no-require-inner-kw \
  --min-area 10000
find "$RUN_DIR/skill" -name "Skill_*.json" -print0 \
  | xargs -0 -n1 -I{} python -m skill.description --skill "{}"
python -m planner.env_summary --run-dir "$RUN_DIR"
python -m planner.skill_index --skills-root "$RUN_DIR/skill"
python -m skill.select \
  --run-dir "$RUN_DIR" \
  --top-k 20 \
  --min-children 1 \
  --no-require-submit \
  --no-require-inner-kw \
  --min-area 10000 \
| while read -r sel; do
    python -m skill.generate \
      --run-dir "$RUN_DIR" \
      --selector "$sel" \
      --with-codegen
  done

## 浏览器前端与 Agent 交互设计（草案）

> 前端文件根目录：`front/`（静态资源）；  
> 规划文档：`workspace/前端.md`。  
> 目标：在 `http://localhost:7777` 暴露一个简单但美观的 Web UI，用于“用户 ↔ Agent ↔ 浏览器”的闭环交互。

### 1. 前端运行方式（初版）

- 使用任意静态文件服务器将 `front/` 挂到 7777 端口，例如：
  - 在仓库根目录运行：
    - `python -m http.server 7777 -d front`
  - 然后在浏览器打开：`http://localhost:7777`
- 首版只假设一个页面：`front/index.html`，后续需要的 JS/CSS 均从这里引入。

### 2. 页面布局（先实现右侧聊天区）

首版只实现“右侧类似 QQ 的聊天框”，左侧浏览器视图面板暂时留空或占位。

- **整体布局**
  - 页面整体使用左右分栏布局（CSS flex），但左栏先留白，只占少量宽度；
  - 右栏占大约 70% 宽度，作为唯一重点区域。

- **右栏：聊天区（单会话）**
  - 上半部分：消息列表（Message List）
    - 滚动容器，显示从上到下的对话记录；
    - 采用“QQ 风格”美化：
      - `agent` 消息：左对齐，淡灰或淡紫色气泡，左侧可预留一个小圆头像（图标可选）；
      - `user` 消息：右对齐，蓝色或青色气泡，右侧可预留一个用户头像占位；
      - 气泡有圆角、轻微阴影，行间距适中；
      - 每条消息下方可选显示浅灰色时间戳（如 `18:42`）。
  - 下半部分：输入区（Composer）
    - 一行或多行文本输入框，用于输入自然语言 task；
    - 右侧一个“发送”按钮（文字如“发送任务”或 “Send”），按钮使用明显的主色（如蓝色）；
    - 输入框与按钮固定在页面底部，消息列表填满其上方空间。

### 3. 消息模型与交互协议（用户 ↔ Agent）

前端内部使用统一的消息结构（与后端接口对齐）：

```json
{
  "id": "msg-20251207-001",
  "role": "user",        // "user" | "agent"
  "text": "自然语言内容",
  "timestamp": "2025-12-07T18:30:00Z"
}
```

- `role = "user"`：表示用户输入的任务或补充说明；
- `role = "agent"`：表示智能体对“已经在浏览器里执行过的操作”的文字描述。

首版约定“一页 = 一个会话”，不做多 Session 列表；页面刷新即视为新会话。

### 4. 用户视角下的交互流程

1. 用户在浏览器打开 `http://localhost:7777`，看到右侧聊天界面（左侧暂为空白或简单提示“浏览器视图预留区域”）。
2. 用户在页面底部的输入框中输入自然语言任务，例如：
   - “在淘宝首页搜索 iPhone 手机，挑选 3000 到 5000 元的商品并打开第一个结果。”
3. 用户点击“发送任务”：
   - 前端立即在消息列表中追加一条右侧蓝色气泡：`role="user"`，内容为刚才的 task；
   - 同时通过 HTTP/WS 等方式，将 `{task, run_dir(可选)}` 发送给后端。
4. 后端收到任务后：
   - 使用现有的 `planner` 流水线（env_summary + skill_index + planner.build_plan + run_task 等）选择技能并驱动浏览器；
   - 每当在真实浏览器中完成一个关键步骤（打开页面、输入文本、点击按钮等），就生成一条自然语言描述，并作为 `role="agent"` 的消息推送给前端。
5. 前端收到 agent 消息后：
   - 追加左侧灰/紫色气泡，例如：
     - “已打开淘宝首页。”
     - “在搜索框中输入 `iPhone`。”
     - “点击搜索按钮，等待结果加载。”
   - 用户可以通过这些文字，理解左侧（未来的）浏览器视图中发生了什么。

### 5. 美观化原则（后续实现时参考）

- 尽量靠近主流 IM（如 QQ/微信）聊天窗体验：
  - 左右分明的消息气泡；
  - 颜色、圆角和间距简洁一致，不做复杂动效；
  - 字体使用系统默认字体即可（如 `system-ui`），保证跨平台效果。
- 交互响应要“即时”：
  - 用户点击发送时，立刻在前端显示自己的消息，不依赖后端返回；
  - agent 消息按时间逐条追加，避免“一口气刷屏”难以对应浏览器动作。

> 左侧浏览器视图面板的具体实现（截图展示 / 实时画面 / 高亮控件等）在前端迭代的下一阶段补充，当前 README 只规定用户与 Agent 的交互方式与右侧 UI 形态。  

### 6. 启动基于 browser-use 的浏览器智能体（复用 VNC Chrome）

在前端 UI 中通过右侧对话框驱动 `browser-use` Agent 操纵 VNC 中的可视 Chrome，需要以下步骤（在仓库根目录执行）：

```bash
# 1) 确保已安装依赖（如已安装可跳过）
conda activate afc-conscribe
pip install -r requirement.txt

# 2) 确保 .env 已配置 OpenRouter / OpenAI 兼容接口，例如：
#   AFC_LLM_API_KEY=你的 OpenRouter 密钥
#   AFC_LLM_BASE_URL=https://openrouter.ai/api/v1
#   AFC_LLM_MODEL=qwen/qwen3-32b
#   AFC_BROWSER_BACKEND=cdp
#   AFC_PLAYWRIGHT_CDP_URL=http://localhost:9223

# 3) 启动带 VNC + CDP 的浏览器容器（暴露 noVNC 和 Chrome CDP）
docker-compose -f front/docker-compose.yml up --build

# 4) 启动前端开发服务器，并绑定一个 Detect 产物目录作为 run_dir
#    front/run.py 中 EXEC_MODE 默认已设置为 "browser_use" 模式，如需切回 planner 可手动修改该文件。
python front/run.py --run-dir workspace/data/ctrip_com/20251116234238

# 5) 在浏览器打开前端界面并与智能体交互
#   在地址栏访问：
#     http://localhost:7776
#   在右侧对话框输入自然语言任务（例如“打开 Bing 搜索 AFC-ConScribe 并读出第一条结果标题”），
#   点击“提交任务”后：
#     - browser-use Agent 会通过 CDP 连接到 VNC 中的 Chrome；
#     - 在左侧 VNC 画面中执行真实页面操作；
#     - 在右侧对话记录中返回每一步的自然语言操作描述与最终结果。
```
“在 GitHub 上搜索仓库 AFC-ConScribe，打开这个仓库首页，读一读 README，最后在右侧对话里总结一下这个项目是做什么的、主要用到哪些技术栈。”

“在 GitHub 上搜索 linux 官方内核仓库（即 Linus Torvalds 名下的那个），打开后找到 Releases 页面，列出最近 3 个 Release 的版本号和发布日期，并说明一下相互之间的大致间隔。”

“在 GitHub 上搜索 Python 的官方仓库 cpython，打开仓库后进入 Lib 目录，找到并打开 asyncio 相关目录，简要说明这个目录下面主要是什么类型的代码（例如协议、事件循环、任务调度等）。”

“在 GitHub 上搜索 pytorch/pytorch 仓库，打开首页，读取它的 README，概括一下这个项目的定位、主要功能特点，并统计一下当前的 star 数量和主要使用的编程语言。”
//单步结果
//阈值
//特定场景
//解耦
//
cd /mnt/f/paperwork/AFC-ConScribe

PYTHONPATH=. python3 - << 'PY'
from pathlib import Path
from AFCdatabaseBuild.init_global_afc import build_initial_global_afc

# 1. 指定要用来建库的 run_dir 列表（这里只用一个）
run_dir = Path("workspace/data/jd_com/20251219213050")

# 2. 指定全局库输出路径
out_db = Path("workspace/data/jd_com/db/abstract_skills_global.jsonl")

# 3. 构建初始全局 AFC 库
build_initial_global_afc(
    run_dirs=[run_dir],
    out_path=out_db,
    use_llm=False,    # 如需用 LLM_global_afc_aggregate 做权重估计就改成 True
    overwrite=True,   # 若 out_db 已存在，会覆盖（符合“如果全局库里面有东西就清空”的需求）
)

print("global AFC written to:", out_db)
PY
