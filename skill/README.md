# Skill 子系统蓝图（S = (Preconditions, Program)）

本文件回答三件事：
- Preconditions 应包含哪些词条、如何从 detect 产物“可编程地”提取；
- 技能结构的各成员中，哪些可由经典编程直接得到，哪些需要 LLM 判断或生成；
- 需要调用 LLM 的环节应向其提供哪些变量，以及提示词模板如何撰写。

一切围绕核心表示 S = (Preconditions, Program)。其余能力（候选召回、定位器构造、评分与健康度等）均服务于这对核心。

## 1. 数据来源与粒度（detect → 控件级）

采集目录：`workspace/data/<domain>/<ts>/`（参见 `detect/collect_playwright.py` 与 `detect/constants.py`）。关键产物：
- `controls_tree.json`（主）：控件/内容节点、`selector`、`geom.bbox`、`action`。
- `dom_summary.json` / `dom_summary_scrolled.json`（辅）：DOM 元素表（可能含 `visible[_adv]`、`occlusion_ratio`、`role`、`class`、`parent_index` 等）。
- `ax.json`（辅）：可访问性树（`role`、可读名）。
- `meta.json`（辅）：`url`、域名、时间戳、视口等元信息（失败时至少包含 url/domain/timestamp）。

粒度定义：一个“可交互控件”对应一个技能（button/input/select/link 等）。

## 2. Preconditions 词条清单与“可编程提取”规则

必选与推荐的最小集合如下；能从 detect 数据确定的，优先用确定性算法生成；不稳定或需语义判断的，交给 LLM 精修（见第 4 节）。

- url_matches（必选）
  - 含义：当前页面 URL 应匹配的正则集合。
  - 提取：从 `meta.url` 解析域名，生成域级通配，如：`^https?://([^/]*\.)?example\.com/`。
  - 细化（可选）：若 `controls_tree`/`dom_summary` 显示该控件只出现在特定路径前缀（可从导航面包屑、选项卡等推断），可追加简单 path 片段，如 `/search`，但默认仅用域级规则以增强鲁棒性。

- exists（必选）
  - 含义：这些选择器必须存在。
  - 提取：至少包含该控件的主选择器 `controls_tree.nodes[i].selector`；若控件依赖某容器（如结果列表容器、工具条），可将容器选择器加入 exists（从父 `parent_index` 逆推构造稳健 selector：优先 `id/name/role + 稳定 class`）。

- not_exists（推荐）
  - 含义：这些遮挡/冲突性元素必须不存在（或未可见）。
  - 提取：从 `dom_summary*.json` 的 `class`/`id` 中，基于词典启发式识别覆盖元素：`modal|mask|backdrop|overlay|dialog|drawer|popup|toast|tooltip|snackbar|loading|spinner|progress|skeleton`。生成若干通用选择器（如 `.modal, .modal-mask, .ant-modal-wrap, .MuiBackdrop-root, .overlay, .backdrop` 等）。若该站点确有命中且元素 `visible[_adv]` 为真或面积>阈值，则将该 CSS 加入 not_exists。

- viewport（推荐）
  - 含义：对视口尺寸的最小要求。
  - 提取：若 `meta.viewport` 可得，则以 `min_width = viewport.width` 的 80%（向下取整）作为下界；若未知，桌面默认 `min_width=960`，移动端可设 `min_width=360`。仅设置必要维度，避免过拟合。

- visible / enabled / text_contains（可选）
  - visible：若 `dom_summary` 提供 `visible[_adv]` 或有 `occlusion_ratio`，且该控件在采集时确为可见，可加入一个“目标选择器可见”的检查；否则省略（存在跨状态抖动风险）。
  - enabled：仅当能从属性（如 `aria-disabled=false`）可靠判断时加入；否则交由 Program 内进行等待/重试。
  - text_contains：若 `ax.json` 或邻近文本能给出稳定 label，可作为弱约束加入，如 `"Images"`；但注意 i18n 与 A/B 的波动，默认不强制。

- login_state / cookie / feature_flag（高级，可选）
  - 若控件显然“仅登录可见”（如头像菜单内“Sign out”），但无法从静态数据可靠判断，可由 LLM 结合 `dom_summary/ax` 文本语义做判断；预条件可表示为 `login_state: 'logged_in'` 或添加 `exists: ['#avatar']` 等替代。

提取顺序建议：url_matches → exists → not_exists → viewport → 其他可选项。仅在证据充分时加入可选项，避免过拟合导致“上下文失配”。

## 3. Skill 结构中“可编程”与“需 LLM”划分

可由经典编程直接生成（无需 LLM）：
- id：`d<dom_index>`（必要时加 `-<timestamp>` 保证唯一）。
- domain/URL：来自 `meta.json`。
- action：来自 `controls_tree.nodes[i].action`（click/type/select/toggle/navigate/submit/open）。
- locators.primary：`controls_tree.nodes[i].selector`。
- locators.fallbacks：
  - by_role/name：来自 `ax.json` 或 `dom_summary.role`。
  - by_text：从元素自身或最邻近文本（`innerText`/AX name）抽取（注意去除空白与过长文本，长度≤64）。
  - by_dom_index：`index` 兜底（权重最低）。
- preconditions 基础骨架：`url_matches`（域级）、`exists`（主选择器）、`not_exists`（遮挡黑名单命中时）、`viewport`（最小尺寸）。
- evidence/meta：来自 detect 产物（tag/role/name/bbox/source_dir/timestamp）。
- args_schema（基础）：
  - action=type → `{"text": "string"}`；
  - action=select → `{"value": "string"}`；
  - 其他 → 空对象。

更适合交给 LLM 的环节：
- 复杂定位器归一与排序：在多候选 selector/role/text 存在冲突或不稳定时，请 LLM 给出“稳健首选 + 回退链”。
- Preconditions 精修：是否加入 `text_contains/visible/enabled`，以及冲突元素集合的取舍（降低误杀）。
- Program 代码细化：
  - 复杂表单/异步加载/滚动至可见/防抖与重试策略；
  - 需要先导步骤（展开抽屉、切换 tab、关闭遮罩）时的准备逻辑；
  - 可观测性与返回结构的补强（evidence）。
- 命名与描述：`label/slug/docstring` 的人类可读化与规范化。

原则：先用可编程手段生成“可运行的骨架”，仅将不确定部分交给 LLM 增量修饰，降低成本与脆弱性。

### 3.1 locators.fallbacks 实现细则（可编程）

目标：在不依赖 LLM 的前提下，稳定生成 `by_role/name`、`by_text`、`selector_alt` 与 `by_dom_index`。

- 建立元素映射
  - 控件节点 id 为 `d<index>`，可据此在 `dom_summary` 中定位同一元素（`elements[index]`）。
  - 若 `controls_tree` 未携带 index，可由其 `selector` 在 `dom_summary` 里做一次“最短匹配”回查；失败则仅生成 `by_dom_index`。

- by_role/name（优先使用语义 + 人类可读名称）
  1) 取 role：
     - 若 `dom_summary[index].role` 存在直接使用；
     - 否则按 tag/input_type 推断：
       - `a`→`link`；`button`→`button`；`textarea`→`textbox`；
       - `input[type=text|search|email|url|password]`→`textbox`；
       - `input[type=checkbox]`→`checkbox`；`input[type=radio]`→`radio`；
       - `select`→`combobox`；
       - 兜底不设置 role（则不生成 by_role）。
  2) 取 name（按优先级）：
     - `aria-label`（`dom_summary[index].aria.label`）；
     - `placeholder`（输入类常见）；
     - `title` 属性；
     - 元素自身可见文本（裁剪去噪，≤64）；
     - 由 `aria-labelledby` 解析到的 label 文本（若可得）。
  3) 归一化 name：去首尾空白、多空格折叠、移除不可见字符；若过长截断（≤64）。
  4) 生成：
     - 若仅有 role：`{"role": role}`；
     - 若有 role+name：`{"role": role, "name": name, "exact": true}`；
     - 若 name 疑似不稳定（含大量数字/动态片段），省略 `name` 或置 `exact=false`。

- by_text（文本回退，最多 1~3 条）
  1) 元素自身可见文本；
  2) 最近的 label/兄弟/上级中位于左侧或上方的短文本；
  3) AX name 作为备用；
  4) 清洗：
     - 去掉空白、重复空格；过滤过长（>64）、全符号/全数字/哈希样式字符串；
     - 去重后保留前 1~3 条。

- selector_alt（CSS 回退）
  - 候选生成：
    - 若有 id：`#<id>`；
    - 若有 name：`<tag>[name="..."]`；
    - 若有 role：`<tag>[role="..."]` 或 `[role="..."]`；
    - 稳定类名（从 `class` 中筛选“短且字母多于数字”的 ≤2 个）：`<tag>.<c1>.<c2>`；
  - 过滤：
    - 去重；剔除包含疑似哈希类名（数字多于字母且长度>3）；
  - 截断：保留至多 3 条。

- by_dom_index（兜底）
  - 直接取 `index` 数值；仅作最低优先级回退，不参与首选。

- 伪代码示例
```
def build_fallbacks(el):  # el = dom_summary[index]
    fb = {}
    role = el.role or infer_role(el.tag, el.input_type)
    name = el.aria.label or el.placeholder or el.title or short_text(el.text)
    if role:
        fb['by_role'] = {'role': role}
        if name:
            fb['by_role']['name'] = normalize(name)
            fb['by_role']['exact'] = True
    texts = dedup([short_text(el.text), nearest_label_text(el), ax_name(el)], limit=3)
    fb['by_text'] = [t for t in texts if t]
    fb['selector_alt'] = top3(dedup(derive_css(el)))
    fb['by_dom_index'] = el.index
    return fb
```


## 4. LLM 任务与提示词模板（输入/输出约定）

统一约束（适用于所有子任务）：
- 仅输出 JSON 或 Python 源码（按任务要求）；
- 不要臆造无法观测的站点私有知识；
- 优先稳定属性（role/aria-label/name/text/持久 class），避免易变 hash class；
- 若信息不足，应显式返回保守结果与原因。

4.1 定位器归一（Locator Chain Synthesizer）
- 目标：生成稳健的首选 CSS 选择器与回退链。
- 输入变量（JSON）：
  - `domain, url, element`: `{tag, id, role, name, classes[], index, bbox}`
  - `candidates`: `{css[], by_role?, by_text?, by_dom_index?}`
  - `neighborhood_texts[]`（邻近文本，降噪后的 top-K）
- 输出（JSON）：
  - `{primary: string, fallbacks: [string], rationale: string}`
- 模板片段：
  - 任务：在不访问网络的前提下，从候选中挑选最稳健的定位链；避免使用动态哈希类；优先 `#id`、`[name]`、`[role][name]`、稳定 `.class` 组合；给出 1 主 + ≤3 回退。

4.2 前置条件精修（Preconditions Refiner）
- 目标：在给定骨架上，补充或删减条目，避免过拟合。
- 输入（JSON）：
  - `skeleton`: 初始 preconditions（如第 2 节提取结果）
  - `signals`: 遮挡元素命中列表、可见性/遮挡比、视口信息、是否移动端
- 输出（JSON）：
  - `preconditions`: 规范化后的对象；并附 `notes` 说明哪些项被添加/移除及原因。
- 模板片段：
  - 任务：仅在“高确定性”信号下才加入 `visible/enabled/text_contains`；保留域级 `url_matches`；若遮挡词命中但频繁误杀，应删除该 not_exists 条目并在 notes 给出理由。

4.3 程序生成/修复（Program Generator/Fixer）
- 目标：生成符合运行约定的 Python 函数代码（或对旧版进行小修）。
- 输入：
  - `action, locators, args_schema, constraints`
  - `examples?`: few-shot 成功/失败片段（可选）
- 输出：
  - `python_code`: 带入口函数 `program__<skill_id>__<slug>(env, locators, args, options)` 的源码字符串；包含简短 Docstring（摘要/Args/Returns）。
- 安全/风格约束：
  - 禁止 `import/subprocess/os/system/eval/exec/network/file I/O`；
  - 仅调用 `env.click/type/select/wait_for_selector/...`；
  - 必须返回 `{"ok": bool, "message": str, "evidence"?: {}}`；
  - 前置条件不在函数里重复校验（由运行层处理）。
- 模板片段：
  - 任务：依据 action 选择最简单正确的步骤（如 click → wait 可选的后继选择器/URL 变化；type → 输入后可选回车）。若目标可能不可见，先 `env.scroll_into_view` 或 `wait_for_selector`，重试≤2 次，失败返回 `ok=false` 与原因。

4.4 准备步骤生成（Prepare Step Synthesizer）
- 目标：生成“轻量准备函数”，用于满足前置条件（如关闭遮罩、展开抽屉、切换到正确 tab）。
- 输入：
  - `context_signals`: 遮挡/抽屉/抽屉开关选择器、当前 tab 文本、是否存在 `.modal` 等
- 输出：
  - `python_code`: 入口 `prepare(env, ctx, options)` 的最短可行步骤；失败应返回原因，不影响主程序结构。

4.5 命名与描述（Skill Naming）
- 目标：生成一致的 `label` 与 `slug`（≤32，蛇形）。
- 输入：
  - `domain, role, ax_name, nearby_texts[]`
- 输出：
  - `{label: string, slug: string}`（如 `Navigate_Images` / `navigate_images`）。

## 5. 变量映射（detect → 模板占位）

- `domain/url` ← `meta.json`。
- `element.tag/id/role/name/classes/index/bbox` ← `controls_tree.nodes[i]` + `dom_summary`/`ax.json` 补充。
- `candidates.css[]` ← `controls_tree.nodes[i].selector` + 基于 `id/name/role/class` 的派生；
- `by_role` ← `ax.role/name`；`by_text` ← 从元素及邻近文本抽取；
- 遮挡信号 ← `dom_summary.occlusion_ratio` 高且类名命中遮挡词。
- 视口 ← `meta.viewport`（未知时用默认 1280x800）。

## 6. 技能 JSON 结构（v1 草案）

```
{
  "id": "d136",
  "domain": "example.com",
  "label": "Images link",
  "action": "navigate",
  "preconditions": {
    "url_matches": ["^https?://([^/]*\\.)?example\\.com/"],
    "exists": ["a[role='link'][aria-label='Images']"],
    "not_exists": [".modal,.modal-mask,.overlay,.backdrop"],
    "viewport": {"min_width": 960}
  },
  "locators": {
    "selector": "a[role='link'][aria-label='Images']",
    "by_role": {"role": "link", "name": "Images"},
    "by_text": ["Images"],
    "by_dom_index": 136,
    "bbox": [x, y, w, h]
  },
  "program": {
    "language": "python",
    "entry": "program__d136__navigate_images",
    "code": "...generated-by-template-or-LLM..."
  },
  "args_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": []},
  "evidence": {"tag": "a", "role": "link", "name": "Images", "from": "controls_tree+ax"},
  "meta": {"created_at": "...", "source_dir": "workspace/data/example_com/<ts>"}
}
```

## 7. 从网页数据到技能（离线流水线）

1) 读取 `controls_tree.json` → 过滤 `type=control`；
2) 为每个控件构造 locators（主 selector + by_role/by_text/by_dom_index）；
3) 生成 Preconditions 骨架（url_matches/exists/not_exists/viewport）；
4) 生成 Program 骨架（基于动作模板）；
5)（可选）调用 LLM：定位器归一、前置条件精修、程序细化、命名；
6) 导出技能 JSON：`skill/skill_library/<domain>/<skill_id>.json`。

## 8. 稳健性与安全约束（面向 LLM 代码）

- 不得导入任何库；禁止文件/网络/子进程/动态执行；
- 只调用 `env.*` API；失败要返回结构化 `{ok: false, message, evidence?}`；
- 适度等待/重试，避免无限循环；
- 对选择器缺失或不可见的情况，明确降级与报错信息。

—— 本文档仅定义数据→技能的工作流与 LLM 提示模板；按你的要求，已不再保留任何运行时代码，后续可在此基础上重建实现。

## 9. 技能字段全清单（v1 提议）

- id: 唯一技能 ID；建议 `d<dom_index>` 或 `d<dom_index>-<ts>`。
- domain: 技能适用站点域名（如 `example.com`）。
- slug: 机器友好的短名（蛇形、小写、≤32）。
- label: 人类可读名称（如 `Navigate_Images`）。
- action: 枚举 `click|type|select|toggle|navigate|submit|open`。
- preconditions: 运行前置条件对象（最小子集，避免过拟合）。
  - url_matches: [string] URL 正则列表（至少包含域级规则）。
  - exists: [string] 运行前必须存在的 CSS 选择器。
  - not_exists: [string] 运行前应不存在/不可见的遮挡元素选择器。
  - viewport: {min_width?: int, min_height?: int} 视口要求。
  - visible?: [string] 需要可见的选择器（可选）。
  - enabled?: [string] 需要可用/未禁用的选择器（可选）。
  - text_contains?: [string] 需要出现的稳定文本片段（可选，注意 i18n）。
  - login_state?: string 登录状态约束（可选，如 `logged_in`）。
  - cookies?: [string] 需要存在的 Cookie 名/模式（可选）。
- locators: 定位器集合（Program 的输入）。
  - selector: string 主 CSS 选择器（首选稳健）。
  - selector_alt?: [string] CSS 回退链（≤3）。
  - by_role?: {role: string, name?: string, exact?: boolean} 语义定位。
  - by_text?: [string] 可见文本/近邻文本回退。
  - by_dom_index?: number DOM 索引兜底。
  - bbox?: [x, y, w, h] 目标在页面坐标系的外接矩形。
  - page_bbox?: [x, y, w, h] 全页截图坐标（若与容器坐标有别）。
- args_schema: JSON Schema（程序入参定义）。
- program: 主程序段（如何操作该控件）。
  - language: 固定 `python`。
  - entry: 入口函数名（推荐 `program__<id>__<slug>`）。
  - code: Python 源码字符串（受限运行时执行）。
  - notes?: 额外说明/限制（可选）。
- prepare?: 可选准备步骤（用于满足前置条件）。
  - language: `python`。
  - entry: `prepare`。
  - code: Python 源码字符串。
- evidence: 证据与来源（用于审计与检索）。
  - tag?: string DOM 标签名。
  - role?: string 语义角色。
  - name?: string 可访问性名称/可见文本。
  - text_snippets?: [string] 近邻文本片段。
  - from: string 证据来源（如 `controls_tree+ax`）。
- meta: 元信息（溯源/版本/生成方式）。
  - version?: string 技能条目版本（如 `v0.1`）。
  - schema_version?: string 本 JSON 结构版本（如 `skill_schema_v1`）。
  - created_at?: string ISO 时间戳。
  - updated_at?: string ISO 时间戳。
  - source_dir?: string 采集源目录（如 `workspace/data/<domain>/<ts>`）。
  - source_files?: [string] 参与生成的文件相对路径列表。
  - detect_spec_version?: string detect 规格版本（参见 `detect/constants.py`）。
  - generator?: string 生成器标识（template/LLM/hybrid）。
- health?: 运行健康度与统计（可选，用于排序与运维）。
  - health_score?: number [0,1] 近期可靠性。
  - success_count?: integer 累计成功次数。
  - failure_count?: integer 累计失败次数。
  - last_success_at?: string ISO 时间戳。
  - last_failure_at?: string ISO 时间戳。
  - notes?: string 备注。

## 10. 模板 JSON（占位 + 简要说明）

注：以下为“可直接复制”的模板，占位值为中文说明文本；落库时请替换为真实值或删除可选字段。

```
{
  "id": "<string: 唯一技能ID，如 d136 或 d136-20251109>",
  "domain": "<string: 站点域名，如 example.com>",
  "slug": "<string: 机器短名，小写蛇形，≤32>",
  "label": "<string: 人类可读名称，如 Navigate_Images>",
  "action": "<enum: click|type|select|toggle|navigate|submit|open>",

  "preconditions": {
    "url_matches": ["<regex: 至少1条域级URL正则>"],
    "exists": ["<css: 目标/关键容器选择器>"],
    "not_exists": ["<css: 遮挡/弹窗等应不存在的元素选择器，可为空>"],
    "viewport": {"min_width": <int 或省略>, "min_height": <int 或省略>},
    "visible": ["<css: 需要可见的元素，可选>"],
    "enabled": ["<css: 需要未禁用的元素，可选>"],
    "text_contains": ["<string: 稳定文本片段，可选>"],
    "login_state": "<string: 如 logged_in，可选>",
    "cookies": ["<string: 需要存在的Cookie名或模式，可选>"]
  },

  "locators": {
    "selector": "<css: 主选择器，稳健优先>",
    "selector_alt": ["<css: 回退选择器1>", "<css: 回退选择器2>"],
    "by_role": {"role": "<string>", "name": "<string 可选>", "exact": <true|false 可选>},
    "by_text": ["<string: 可见文本或邻近文本，≥1可选>"],
    "by_dom_index": <int: DOM索引兜底，可选>,
    "bbox": [<x>, <y>, <w>, <h>],
    "page_bbox": [<x>, <y>, <w>, <h>]
  },

  "args_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string", "description": "<输入类动作的文本，可按需要扩展>"},
      "value": {"type": "string", "description": "<选择类动作的值，可选>"}
    },
    "required": ["<列出必需参数名，可为空>"]
  },

  "program": {
    "language": "python",
    "entry": "<string: 函数名，推荐 program__<id>__<slug>>",
    "code": "<string: Python 源码（受限运行），禁止导入/网络/文件>",
    "notes": "<string: 说明与限制，可选>"
  },

  "prepare": {
    "language": "python",
    "entry": "prepare",
    "code": "<string: 满足前置条件的最小步骤，可选>"
  },

  "evidence": {
    "tag": "<string: DOM标签，可选>",
    "role": "<string: 语义角色，可选>",
    "name": "<string: 可访问性名称/显示文本，可选>",
    "text_snippets": ["<string: 邻近文本片段，可选>"],
    "from": "<string: 证据来源，如 controls_tree+ax>"
  },

  "meta": {
    "version": "<string: 技能条目版本，如 v0.1>",
    "schema_version": "<string: 本JSON结构版本，如 skill_schema_v1>",
    "created_at": "<string: ISO时间>",
    "updated_at": "<string: ISO时间，可选>",
    "source_dir": "<string: 采集目录路径>",
    "source_files": ["<string: 相对路径>"],
    "detect_spec_version": "<string: 见 detect/constants.py>",
    "generator": "<string: template|LLM|hybrid>"
  },

  "health": {
    "health_score": <number 0..1>,
    "success_count": <int>,
    "failure_count": <int>,
    "last_success_at": "<string: ISO时间>",
    "last_failure_at": "<string: ISO时间>",
    "notes": "<string: 备注，可选>"
  }
}
```

## 11. 字段详解（类型/必选/来源/提取/注意）

- id
  - 类型：string（必须，唯一）。
  - 来源/提取：来自控件 DOM 索引，形如 `d<index>`；若同域下可能冲突，追加短时间戳后缀 `-<ts4>`。
  - 规则：仅包含 `[a-z0-9_-]`；建议稳定不随小改动变化（页面大改版可生成新 id 并维持旧条目健康度衰减）。
  - 示例：`d136`、`d482-1109`。

- domain
  - 类型：string（必须）。
  - 来源/提取：`meta.json.url` 解析的 `netloc`；存原始域，路径中使用 `sanitize_domain` 版本。
  - 示例：`example.com`、`www.csdn.net`（建议业务逻辑按“主域”归档）。

- slug
  - 类型：string（建议）。
  - 提取：由 label 或 AX 名称/可见文本转小写蛇形；长度 ≤ 32；字符集 `[a-z0-9_]`。
  - 示例：`navigate_images`、`search_box`。

- label
  - 类型：string（建议）。
  - 提取：优先 AX 名称/控件可见文本；不宜过长（≤ 40 字符）。
  - 示例：`Navigate_Images`、`Search_Box`。

- action
  - 类型：enum（必须）。取值：`click|type|select|toggle|navigate|submit|open`。
  - 来源/提取：`controls_tree.nodes[i].action`（detect 已基于 tag/role/type 推断）。
  - 说明：
    - click：按钮/可点击区域但不引起导航；
    - navigate：链接/点击后主要是跳转；
    - type：`input[type!=checkbox/radio/...]/textarea` 文本输入；
    - select：`<select>` 或等价组件；
    - toggle：checkbox/radio/switch；
    - submit：`input[type=submit]` 或表单提交入口；
    - open：较少用，指展开抽屉/二级面板（无导航）。

- preconditions（对象，建议最小必要）
  - url_matches
    - 类型：`string[]`（必须 ≥1）。
    - 来源：`meta.url` 生成域级正则，如 `^https?://([^/]*\.)?example\.com/`。
    - 注意：尽量不含具体 query；路径前缀仅在高度稳定时加入。
  - exists
    - 类型：`string[]`（必须 ≥1）。
    - 来源：主选择器 + 关键容器选择器；主选择器来自 `controls_tree.selector`。
  - not_exists
    - 类型：`string[]`（可选）。
    - 来源：遮挡词表命中（`modal|mask|backdrop|overlay|dialog|drawer|toast|loading|spinner|skeleton` 等）；仅在该站点确有命中且可见时加入通用 CSS。
  - viewport
    - 类型：`{min_width?: int, min_height?: int}`（可选）。
    - 来源：`meta.viewport` 的 80% 作为最小值；未知时桌面默认 `min_width=960`。
  - visible
    - 类型：`string[]`（可选）。
    - 来源：如 `dom_summary.visible_adv=true` 且稳定时才加入；否则留给 Program 等待。
  - enabled
    - 类型：`string[]`（可选）。
    - 来源：基于 `aria-disabled` 等静态属性时可加入；否则不建议使用。
  - text_contains
    - 类型：`string[]`（可选）。
    - 来源：稳定 UI 文本（短、无 i18n 风险）；默认不强制。
  - login_state
    - 类型：`string`（可选，如 `logged_in`）。
    - 说明：无法从静态数据可靠判断时，可用 `exists` 替代（如 `#avatar`）。
  - cookies
    - 类型：`string[]`（可选）。
    - 说明：轻量约束；避免写入站点私密信息。

- locators（对象，Program 输入）
  - selector
    - 类型：string（必须）。
    - 生成规则：优先 `#id`；其次 `tag[role][name]` 或 `tag.name`；否则 `tag.<稳定class1>.<稳定class2>`（class 需“短、字母多于数字、非 hash”，≤2 个）。
  - selector_alt
    - 类型：`string[]`（可选，≤3）。
    - 说明：去重、按稳健性降序；不重复 `selector`。
  - by_role
    - 类型：`{role: string, name?: string, exact?: boolean}`（可选）。
    - 来源：`ax.json` 或 `dom_summary.role`；`name` 取 AX name 或可见文本；`exact` 默认 `true`。
  - by_text
    - 类型：`string[]`（可选）。
    - 来源：元素文本或邻近文本（去噪、≤64 字符、≤3 条）。
  - by_dom_index
    - 类型：number（可选）。
    - 说明：最后兜底，权重最低。
  - bbox / page_bbox
    - 类型：`[x,y,w,h]`（可选，整数）。
    - 用途：可视化/overlay/调试；不参与匹配。

- args_schema
  - 类型：JSON Schema（对象，建议）。
  - 默认：
    - action=type → `{"text": "string"}`；
    - action=select → `{"value": "string"}`；
    - 其余为空对象。
  - 注意：保持“最小必要”，避免过度约束。

- program
  - language：固定 `python`（必须）。
  - entry：string（必须，推荐 `program__<id>__<slug>`）。
  - code：string（必须）。约束：
    - 禁止 `import/subprocess/os.system/eval/exec/文件/网络`；
    - 仅调用 `env.*` API；
    - 返回 `{"ok": bool, "message": str, "evidence"?: {}}`；
    - 失败抛出改为结构化返回，避免异常外泄。
  - notes：string（可选）。

- prepare
  - language：`python`（可选）。
  - entry：`prepare`（可选）。
  - code：string（可选）。用途：关闭遮罩/展开抽屉/切换 tab 等以满足 preconditions；失败不应破坏主流程结构。

- evidence
  - tag/role/name/text_snippets/from（均可选）。
  - 来源：`controls_tree` + `ax.json` + 文本邻域；用于审计与离线检索，不直接影响运行。

- meta
  - version/schema_version：版本字符串（建议）。
  - created_at/updated_at：ISO 时间戳（建议）。
  - source_dir/source_files：采集溯源（建议）。
  - detect_spec_version：取自 `detect/constants.py`（建议）。
  - generator：`template|LLM|hybrid`（建议）。

- health（可选）
  - health_score：`[0,1]`，建议用 EWMA（如 α=0.2）平滑；
  - success_count/failure_count：累计计数；
  - last_success_at/last_failure_at：最近时间戳；
  - notes：备注。

— 实践建议：必填最小集为 `id/domain/action/preconditions.url_matches/preconditions.exists/locators.selector/program.{entry,code}/meta.created_at`；其余按稳定性与需要渐进补充。
