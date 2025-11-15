# Detect 采集接口规范（仅文档，不含代码）

目标：输入一个 URL，采集该页面的多种视图工件，并按“域名_时间戳”的目录落地到 `data/` 数据集下。采集内容包含：
- 全页截图（初始/加载完成两个时点）
- DOM 快照（原始 HTML + 可选简表 JSON）
- AX 可访问性树（AXTree JSON）
- 元信息（URL、时间戳、UA、视口、加载时序）

保存路径规则（示例）：`workspace/data/baidu_com/20251116171811/…`
- 域名清洗：将 `.` 替换为 `_`，非字母数字也替换为 `_`，连续 `_` 折叠为单个 `_`
- 时间戳：`YYYYMMDDHHMMSS`（本地时间或 UTC，需在 meta 中标注时区）

## 建议的文件结构

```
workspace/data/
  <domain_sanitized>/
    <timestamp>/
      meta.json                # URL、时间、UA、viewport、loadState 等
      screenshot_initial.png   # DOMContentLoaded 直后，全页截图
      screenshot_loaded.png    # load(+networkidle) 后，全页截图（含自动滚动触发的懒加载内容）
      screenshot_loaded_overlay.png # 自动生成的控件框可视化
      screenshot_scrolled_tail.png # 自动滚动完成后的全页截图（容器感知+限额，失败则退回页面 fullPage）
      segments/                # 滚动后的局部图若干（默认最多 6 张，segments/index.json 为索引）
      dom.html                 # documentElement.outerHTML
      dom_summary.json         # DOM 简表（tag/id/class/role/visible/bbox 等）
      dom_summary_scrolled.json# 自动滚动后重新采样的 DOM 简表
      dom_scrolled_new.json    # 与初始简表对比的新增元素清单（近似指纹匹配）
      ax.json                  # 可访问性树（AXTree snapshot）
      scroll_info.json         # 滚动前后文档高度/网络空闲/新增元素计数摘要
      timings.json             # load/domcontentloaded/networkidle 时间点与耗时
      controls_tree.json       # 极简控件树（parent/children/selector/geom）
      icons/                   # 基于控件 bbox 的贴图裁剪（icons/<node_id>.png），并在控件树节点追加 icon.path/roi
```

可选扩展：
- `screenshot_viewport.png`（仅视口截图，便于对比）
- `network_log.json`（请求/响应摘要，脱敏后存）

## 接口定义（语言无关描述）

- 函数：`collect(url: str, out_root: str = "data", timeout_ms: int = 45000) -> str`
  - 输入：
    - `url`：需要采集的页面 URL
    - `out_root`：输出根目录，默认 `data`
    - `timeout_ms`：加载与采集的超时时间
  - 行为：
    1) 解析域名并清洗，生成 `domain_sanitized`
    2) 生成 `timestamp`（`YYYYMMDDHHMMSS`）
    3) 启动无头浏览器，设置 UA/视口（如 1280x800）
    4) 导航到 URL，等待 `domcontentloaded`，截图保存为 `screenshot_initial.png`
    5) 采集 `dom.html`（outerHTML）
    6) 采集 `ax.json`（完整 AXTree；不限制 `interestingOnly`）
    7) 生成 `dom_summary.json`（元素简表：tag/id/class/role/visible/bbox/层级）
    8) 等待 `load` + `networkidle`（或超时退化），截图 `screenshot_loaded.png`
    9) 写入 `meta.json`（URL、时区、UA、viewport、采集版本等）与 `timings.json`
    10) 返回最终目录路径：`data/<domain>/<timestamp>`
  - 输出：最终保存目录的绝对或相对路径字符串
  - 失败处理：
    - 超时/脚本错误时，写入 `meta.json` 标注 `status=failed` 与错误原因；尽可能保留已完成的工件
    - 外部网络/服务受限时，可仅落地 `meta.json` 说明原因

## 关键实现要点（建议）

- 浏览器引擎：Playwright/Puppeteer/Selenium 均可（推荐 Playwright）
- 截图模式：全页截图（`fullPage=true`），避免仅视口造成信息缺失
- AX 采集：Playwright 可用 `page.accessibility.snapshot({ interestingOnly: false })`
- DOM 简表：建议保留最小必要字段以便后续候选生成（tag/id/class/name/role/aria-*/bbox/visible/父子关系）
- 可见性与 bbox：使用 `getBoundingClientRect()` + 计算是否出屏/重叠（重叠可留给 MVFN 阶段处理）
- 隐私与脱敏：
  - 禁止采集表单已填值；
  - 可选遮蔽 input[type=password] 与邮件/手机号模式文本
- 幂等：同 URL 重复采集应生成不同 `timestamp` 目录；错误重试可追加 `-retryN`

## 目录存在性与并发

- 若 `data/<domain>/<timestamp>` 已存在，追加 `-1`、`-2` 后缀或滚动时间戳（秒级+随机）
- 并发多 URL：每个 URL 独立目录，不共享浏览器上下文（避免污染）

## 示例

- 输入：`collect("https://www.baidu.com")`
- 输出目录：`workspace/data/baidu_com/20251116171811/`
- 产物：`screenshot_initial.png`、`screenshot_loaded.png`、`dom.html`、`dom_summary.json`、`ax.json`、`meta.json`、`timings.json`

## 与 MVFN 的衔接

- MVFN 的输入即该目录下的 `screenshot_*`、`dom_summary.json`、`ax.json` 等工件
- `meta.json` 中建议写入 `pipeline_version` 与 `rule_version` 以便后续追踪

> 说明：当前为接口与落地约定说明，不包含任何可执行代码。待确认后，可据此生成采集脚本（Python+Playwright 或 Node+Puppeteer）。

## 样例实现（Python + Playwright）

- 脚本：`detect/collect_playwright.py`
- 依赖：`pip install playwright`，随后执行 `playwright install chromium`
- 运行：`python detect/collect_playwright.py https://www.baidu.com`
- 产物：输出目录路径，内部包含上述截图/DOM/AX/简表/元信息/时序文件。
  - 默认会在生成最终截图前“自动滚动到页面底部”，以触发懒加载并记录滚动带来的变化：
    - `screenshot_scrolled_tail.png`：底部视口截图
    - `dom_summary_scrolled.json`：滚动后的全量简表
    - `dom_scrolled_new.json`：与初始简表的“新增元素”近似集（基于 tag/id/class/role/name/text/bbox 指纹）
    - `scroll_info.json`：滚动前后 `scrollHeight`、是否达到底部、是否达到 `networkidle`、新增元素计数

### 函数式调用示例（直接在 Python 中调用）

- 依赖准备：
  - `pip install -r requirement.txt`
  - `python -m playwright install chromium`

- 示例代码：
  ```python
  from detect.collect_playwright import collect

  # 必填：采集目标 URL
  url = "https://www.baidu.com"

  # 可选：输出根目录（默认 'data'），超时时间（毫秒，默认 45000）
  out_root = "data"
  timeout_ms = 45000

  out_dir = collect(url, out_root=out_root, timeout_ms=timeout_ms)
  print("Artifacts saved to:", out_dir)
  ```

- 输出保存位置：
  - 根目录：`data/`
  - 子目录：`<domain_sanitized>/<YYYYMMDDHHMMSS>/`（如 `data/baidu_com/20251116171811/`）
  - 若同名目录已存在，会自动追加 `-1`、`-2` 后缀以避免覆盖
  - 目录内包含：`screenshot_initial.png`、`screenshot_loaded.png`、`dom.html`、`dom_summary.json`、`ax.json`、`meta.json`、`timings.json`
  - 滚动扩展产物（如启用自动滚动）：`screenshot_scrolled_tail.png`、`dom_summary_scrolled.json`、`dom_scrolled_new.json`、`scroll_info.json`

### JS 助手文件

- 采集脚本会在导航完成后注入 `detect/collect_playwright.js`，并通过其中的函数完成页面端逻辑：
  - `DetectHelpers.getDomSummary(limit)`：生成 DOM 简表
  - `DetectHelpers.getNavigationTiming()`：获取 Navigation Timing
  - `DetectHelpers.getDocMetrics()`：获取文档尺寸（scrollHeight/clientHeight）
  - `DetectHelpers.getUserAgent()`：获取 UA
  - `DetectHelpers.scrollStep()`：滚动一步，返回是否到达底部
- 若注入失败，脚本会回退到精简的内联实现，并在 `meta.json.warnings[]` 中记录 `INJECT_JS_*` 告警。

### 控件极简树（controls_tree.json）

- 目的：提取“可操作控件”的轻量结构树，便于自动化/检索。
- 节点字段：
  - `id`: 节点 id（如 `d45`，源自 DOM 索引）
  - `type`: `control|content`（交互控件或内容卡片/列表项）
  - `parent`: 父节点 id（根为 null）
  - `children`: 子节点 id 列表（节点间的父子）
  - `selector`: 简易 CSS 选择器（稳定优先，可能非唯一）
  - `geom`: `{ bbox: [x,y,w,h], shape: rect|pill|round[, page_bbox] }`
- 来源：默认使用 `dom_summary_scrolled.json.elements`（若为空退回 `dom_summary.json.elements`），由 `detect/controls_tree.py` 生成。
- 规则摘要：
  - 控件判定：标签/角色/交互分等启发式（button/input/select/textarea/a；role=button/link/textbox 等）
  - 内容判定：role=article/listitem/feed/region、tag=article，或 class 含 card/tile/list-item/grid-item/cell 等关键词
  - 父子关系：按最近的控件祖先（跳过纯容器）
- 形状：基于 `border-radius` 与 bbox 近似判定 rect/pill/round

### 图标贴图（icons/）

- 脚本：`detect/icon_patches.py`（采集流程中已自动执行）
- 输入：`controls_tree.json`、`screenshot_loaded.png`
- 规则：
  - 小控件（min(w,h)≤48）或 round/pill 且尺寸不大 → 直接使用整个 bbox 作为贴图
  - 其他控件 → 在控件左内侧截取近似正方形区域（最大 32px，垂直居中）
- 输出：`icons/<node_id>.png`，同时在节点中追加 `icon: { path, roi }`

### 滚动后局部图（segments/）
- 目的：在整页图之外，提供若干代表性局部图便于人工核查与对比。
- 来源：
  - 若启用容器感知拼接：从分段截图中挑选顶部/中部/底部等最多 6 张；
  - 否则：从整页图均匀切片生成最多 6 张。
- 索引：`segments/index.json` 记录每张片段的文件相对路径与对应滚动位置/尺寸。

### 控件框 Overlay 可视化（已自动生成）

- 功能：在截图上给控件节点打框，按“树深度”使用不同颜色与线宽；可选叠加半透明填充与 id 标签。
- 脚本：`detect/overlay.py`
- 自动生成：采集流程完成后，默认会基于 `controls_tree.json` 对 `screenshot_loaded.png` 生成一张 `screenshot_loaded_overlay.png`。
- 手动运行（可自定义参数）：
  - `python detect/overlay.py --dir data/baidu_com/20251116171811 --image screenshot_loaded.png --label`
  - 输出：`data/baidu_com/20251116171811/screenshot_loaded_overlay.png`
- 参数（关键项）：
  - `--mode {viewport,page}`：叠加模式。viewport 仅使用节点的视口坐标 `bbox` 并限制在视口高度；page 使用“容器拼接映射/页面绝对坐标”，适合整页大图。
  - `--min-thickness`/`--max-thickness`：线宽范围（默认 1~6，靠近根的节点线更粗）。
  - `--alpha`：填充透明度 0~255（默认 0 即不填充；建议 0~128）。
  - `--label`：是否在框左上角绘制节点 id。
  - 可见性/遮挡筛选：默认关闭（不丢框）。如需过滤，可加 `--filter-occluded [--occ-threshold 0.98]` 或 `--no-only-visible/--no-filter-occluded` 显式控制。

> 对齐策略与默认值：为避免错位，采集器在自动生成 overlay 时，默认使用 page 模式（`--mode page`），并且不进行“可见性/遮挡”过滤，以保证“不错失框”。

#### 与采集器参数的联动

- `detect/collect_playwright.py` 提供 Overlay 生成模式选择：
  - `--overlay-mode-loaded {auto|page|viewport}`：控制 `screenshot_loaded*.png` 的叠加模式；
  - `--overlay-mode-tail {auto|page|viewport}`：控制 `screenshot_scrolled_tail.png` 的叠加模式；
  - `auto` 行为：按当前实现默认选择 `page`，以降低整页/拼接图的错位概率。
  - 采集器在生成 overlay 时固定使用 `only_visible=False`, `filter_occluded=False`（即不过滤），若需筛选请单独调用 `detect/overlay.py`。

### 模块化与解耦

- 错误类型：`detect/errors.py`（`CollectError`）
- 通用工具：`detect/utils.py`（域名清洗/时间戳/目录/JSON/URL 校验/视口解析）
- 上下文构造：`detect/context_utils.py`（设备/视口/DPR → BrowserContext 参数）
- 滚动逻辑：`detect/scrolling.py`（自动滚动到底部，优先调用页面 JS）
- 页面 JS：`detect/collect_playwright.js`（DOM 摘要/可见性/遮挡/导航时序等）

### 返回信息（用于从 data/ 快速定位）

- 若需要不仅返回目录，还返回“定位信息”，可使用：
  ```python
  info = collect(
      url,
      out_root=out_root,
      timeout_ms=timeout_ms,
      return_info=True,
  )
  # info 结构：
  # {
  #   'url': 'https://www.baidu.com',
  #   'domain': 'www.baidu.com',
  #   'domain_sanitized': 'baidu_com',
  #   'timestamp': 'YYYYMMDDHHMMSS',
  #   'out_dir': 'data/baidu_com/YYYYMMDDHHMMSS',
  #   'status': 'ok' | 'failed',
  #   'params': { out_root, timeout_ms, auto_scroll_before_loaded_shot, autoscroll_max_steps, autoscroll_delay_ms },
  #   'achieved_networkidle': bool,
  #   'auto_scroll_reached_bottom': bool | None,
  #   'artifacts': {
  #       'screenshot_initial': 'screenshot_initial.png',
  #       'screenshot_loaded': 'screenshot_loaded.png',
  #       'screenshot_scrolled_tail': 'screenshot_scrolled_tail.png',
  #       'dom_html': 'dom.html',
  #       'dom_summary': 'dom_summary.json',
  #       'dom_summary_scrolled': 'dom_summary_scrolled.json',
  #       'dom_scrolled_new': 'dom_scrolled_new.json',
  #       'ax': 'ax.json',
  #       'timings': 'timings.json',
  #       'meta': 'meta.json',
  #       'scroll_info': 'scroll_info.json'
  #   }
  # }
  print("Locate artifacts under:", info["out_dir"])  # 可直接拼接 artifacts 文件名
  ```

### 指定分辨率/设备（与浏览器开发者工具类似）

- 使用内置设备：
  - `python detect/collect_playwright.py https://www.example.com --device "iPhone 12 Pro"`
  - 将应用 Playwright 的内置设备描述（UA、视口、DPR、是否移动端等）。

- 自定义视口与 DPR：
  - `python detect/collect_playwright.py https://www.example.com --viewport 1148x1622 --dpr 1.0 --autoscroll-max-steps 200`
  - 若同时指定 `--device` 与 `--viewport/--dpr`，后两者会覆盖设备描述中的对应项。

- 函数调用：
  ```python
  info = collect(
      url,
      return_info=True,
      device="iPhone 12 Pro",     # 或 None
      viewport="1148x1622",       # 或 (1148, 1622) 或 None
      dpr=1.0,                     # 或 None
  )
  ```

### 指定分辨率/设备（与浏览器开发者工具类似）

- 使用内置设备：
  - `python detect/collect_playwright.py https://www.example.com --device "iPhone 12 Pro"`
  - 将应用 Playwright 的内置设备描述（UA、视口、DPR、是否移动端等）。

- 自定义视口与 DPR：
  - `python detect/collect_playwright.py https://www.example.com --viewport 1148x1622 --dpr 1.0`
  - 若同时指定 `--device` 与 `--viewport/--dpr`，后两者会覆盖设备描述中的对应项。

- 函数调用：
  ```python
  info = collect(
      url,
      return_info=True,
      device="iPhone 12 Pro",     # 或 None
      viewport="1148x1622",       # 或 (1148, 1622) 或 None
      dpr=1.0,                     # 或 None
  )
  ```

### 异常状态处理与抛出

- 参数：`collect(url, out_root='data', timeout_ms=45000, raise_on_error=False)`
  - `raise_on_error=True` 时：发生致命错误（浏览器启动/导航失败）将抛出 `CollectError`，并已写入 `meta.json`（`status=failed`）。
  - `raise_on_error=False`（默认）：不抛异常，函数返回输出目录路径；失败信息在 `meta.json` 中体现。

- 可能的错误码（`meta.json.error_code` 或 `CollectError.code`）：
  - `INVALID_URL`（URL 无效或非 http/https）
  - `LAUNCH_ERROR`（浏览器引擎/上下文启动失败）
  - `NAV_TIMEOUT`（导航超时）
  - `NAV_ERROR`（导航错误）
  - `UNEXPECTED_ERROR`（其他未分类致命错误）

- 非致命告警（仅记录在 `meta.json.warnings[]`，流程继续）：
  - `SCREENSHOT_INITIAL_ERROR`、`SCREENSHOT_LOADED_ERROR`
  - `DOM_HTML_ERROR`、`DOM_HTML_WRITE_ERROR`
  - `AX_SNAPSHOT_ERROR`、`AX_WRITE_ERROR`
  - `DOM_SUMMARY_ERROR`、`DOM_SUMMARY_WRITE_ERROR`
  - `LOAD_STATE_ERROR`、`TIMINGS_ERROR`、`TIMINGS_WRITE_ERROR`、`UA_ERROR`

- 抛出用法示例：
  ```python
  from detect.collect_playwright import collect, CollectError

  try:
      out_dir = collect("https://bad.example", raise_on_error=True)
  except CollectError as e:
      print("Collect failed:", e.code, e.stage, e)
      # e.out_dir 中仍然包含已写入的 meta.json，以便排查
  ```

## dom_summary.json 字段字典

- 顶层字段
  - `count`：整数，元素条目数量
  - `viewport`：对象，页面视口信息
    - `width`：整数，像素
    - `height`：整数，像素
  - `elements`：数组，元素简表条目列表

- `elements[]` 字段（每个 DOM 元素一条）
  - `index`：整数，遍历顺序索引（从 0 开始）
  - `tag`：字符串，小写标签名（如 `button`/`input`）
  - `id`：字符串或 null，元素 `id`
  - `class`：字符串或 null，原始 `className`
  - `role`：字符串或 null，显式 `role` 属性（无则为 null）
  - `name`：字符串或 null，`name` 属性
  - `aria`：对象，包含所有 `aria-*` 属性键值对（仅存在的项）
  - `bbox`：整数数组 `[x, y, width, height]`，来自 `getBoundingClientRect()` 四舍五入
  - `visible`：布尔，基于宽高>0 且 `visibility != hidden` 且 `display != none`
  - `text`：字符串，`innerText` 截断到最多 160 字符

扩展字段（已启用高级摘要）：
- `visible_adv`：更严格的可见性（display/visibility/opacity/pointer-events/aria-hidden 综合）
- `in_viewport`：是否与当前视口相交
- `occlusion_ratio`：遮挡比例（基于 elementFromPoint 采样，0~1）
- `z_index`、`opacity`、`pointer_events`：样式线索
- `labels`：与元素关联的 label/title/aria-label 文本
- `interactive_score`：交互性启发式得分（0~1）

说明：元素数量默认上限 20000 条以避免极端大页；如需调整请在实现中修改上限。

### 最小示例（片段）

```json
{
  "count": 2,
  "viewport": {"width": 1280, "height": 800},
  "elements": [
    {
      "index": 42,
      "tag": "input",
      "id": "kw",
      "class": "s_ipt",
      "role": null,
      "name": "wd",
      "aria": {"aria-label": "搜索"},
      "bbox": [100, 200, 340, 32],
      "visible": true,
      "text": ""
    },
    {
      "index": 45,
      "tag": "button",
      "id": "su",
      "class": "btn primary",
      "role": null,
      "name": null,
      "aria": {},
      "bbox": [450, 200, 88, 32],
      "visible": true,
      "text": "搜索"
    }
  ]
}
```

### 常见设备快捷别名（示例）

可直接用于 `--device "<名称>"`（以本地 Playwright 版本支持为准）：
- Pixel 7：`"Pixel 7"`
- iPad Air：`"iPad Air"`
- Nest Hub：`"Nest Hub"`

提示：若与 `--viewport/--dpr` 同时提供，后两者会覆盖设备描述中的视口与像素比。
