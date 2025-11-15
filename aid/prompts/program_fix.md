# 程序修补（Program Fix）— 增强版提示词

任务：依据下列上下文，对现有 `program.code` 做“最小修补/加固”，加入等待、滚动、重试与回退定位，补齐返回值与证据，不重写业务流程。最终仅输出一个可直接替换 `program.code` 的 Python 函数，禁止输出说明文字或 Markdown。

## 1) 上下文输入
- 技能：id={ct.id}, action={ct.action}, selector={ct.selector}, domain={meta.domain}
- 定位器（主/备/role/text/index）：
```json
{locators_json}
```
- 参数模式（调用时可能提供的键）：
```json
{args_schema_json}
```
- 片段（outerHTML，作为语义参考）：
```
{snippet_html}
```
- 当前代码（原函数源码，需在其上加固，非重写）：
```
{current_code}
```

## 2) 约束与执行环境
- 仅使用 `env.*` API：
  - `env.current_url()`、`env.exists(selector, timeout_ms=None)`、`env.click(selector, timeout_ms=None)`、`env.type(selector, text, delay_ms=None)`、`env.select(selector, value)`、`env.press(selector, key)`、`env.wait_for_selector(selector, state='visible', timeout_ms=None)`、`env.scroll_into_view(selector)`。
- 禁止：导入第三方库/IO/eval/exec/`time.sleep`。
- 返回结构：`{"ok": bool, "message": str, "evidence": {...}}`，`evidence` 可包含 used_locator、fallback_path、url_before/after、elapsed_ms、tries 等。
- 签名保持：
  - 若 `current_code` 中已有入口函数（如 `program(env, locators, args, options)` 或其他名字/参数），必须保留原名与参数顺序；
  - 若缺失入口，生成 `def program(env, locators, args, options):`。

## 3) 回退与重试策略（必须实现）
优先级与流程：
1. primary: `locators.selector`
2. `locators.selector_alt`（按顺序）
3. `locators.by_role`（role+name/exact）
4. `locators.by_text`（≤3 条）
5. `by_dom_index`（兜底，优先用主选择器 + 位置判断，而非直接使用 index 定位）

实现要求：
- 每个候选定位器最多重试 2 次（渐进 backoff：300ms, 600ms，可用 `env.wait_for_selector(...); env.scroll_into_view(...)` 代替睡眠）。
- 每次尝试前：`env.scroll_into_view(sel)`；尝试 `env.wait_for_selector(sel, state='visible', timeout_ms=timeout)`。
- 交互失败（点击/输入/选择）或超时：记录并尝试下一候选；全部失败则抛出 `RuntimeError` 或返回 `{ok:false}`。

## 4) 等待与后置校验
- 交互前等待：目标元素可见/启用（`wait_for_selector`）。
- 交互后校验：
  - 若 `action` in {navigate, submit, open}：监测 URL 变化（`env.current_url()` 前后对比）；
  - 若 `action` in {type, select, toggle, click}：
    - type：再次读取输入框 `exists`/`wait_for_selector` 以确认无异常；
    - select/toggle：确认元素仍可见，必要时再次滚动；
  - 失败则进入回退/重试。

## 5) 参数与业务映射
- 从 `args` 读取输入：
  - type: `args.get("text", "")`
  - select: `args.get("value", "")`
  - 其他 action：若 `args_schema` 定义了键，则按键名读取。
- 若 `args` 缺省，对只读/点击类操作允许无参；写入类操作须给出合理兜底（如空串 → 不写入）。

## 6) 代码骨架（必须包含但可精简）
- 推荐内置工具：
  - `_candidates_from_locators(locs) -> List[dict]`：产出有标注的候选（type,label,selector）。
  - `_try_once(sel, op) -> bool`：单次尝试（等待→滚动→操作）。
  - `_operate_with_fallback(op) -> (ok, used, path)`：执行带回退的操作，返回是否成功、使用的选择器、回退路径。
- 主流程：
  - `url_before = env.current_url()`
  - `(ok, used, path) = _operate_with_fallback(...)`
  - `url_after = env.current_url()`；计算 `elapsed_ms`
  - `return { "ok": ok, "message": "ok" if ok else "failed", "evidence": { "used_locator": used, "fallback_path": path, "url_before": url_before, "url_after": url_after, "elapsed_ms": elapsed_ms } }`

## 7) 仅输出代码
- 只输出一个顶层函数的完整源码，不要包含 Markdown/注释/说明性文字。

---

# Playwright Python 代码生成规范（摘要，便于对齐实现）

## 1. 角色设定
**身份**: 精通 Python 编程与 Playwright 自动化的资深软件工程师  
**任务**: 基于提供的 HTML 控件代码，生成**唯一一个**功能完整、健壮可靠的 Python 函数

## 2. 核心要求
### 2.1 输出约束
- **唯一性**: 仅生成一个顶层函数，可包含内部辅助函数
- **纯代码**: 输出纯 Python 代码文本，禁止 Markdown 标记或额外说明
- **完整性**: 必须覆盖 HTML 中所有可交互的元素
- **语法正确**: 确保代码可直接执行，无语法错误

### 2.2 功能要求
- **自动查找**: 函数能自动查找目标元素，处理翻页、动态加载等场景
- **状态处理**: 自动处理页面状态变化，确保操作的完全性与准确性
- **单次完成**: 一次调用完成所有必要操作，无需用户额外干预
- **异常安全**: 包含完善的异常处理机制

## 3. 函数设计规范
### 3.1 参数设计
```python
def function_name(
    page: Page,           # 第一个参数固定为 Page 对象,还有别的参数表现
    # 业务参数：覆盖 HTML 功能需求，具有明确业务含义
    # 可选参数：timeout, strict 等控制参数
    # 参数必须覆盖该组件正常行为，如为组件内部文本框提供填入文本，或者为选项提供选项参数
) -> 返回类型:
```

### 3.2 命名规范
- **操作类型前缀**:
  - 只读操作: `get_`、`is_`、`has_`
  - 状态改变: `click_`、`select_`、`set_`、`perform_`、`fill_`
- **格式**: `操作类型_目标描述`，使用下划线分隔
- **内部函数**: 以 `_` 开头，如 `_find_elements`、`_validate_state`

## 4. 技术实现要求
### 4.1 Playwright API 使用
**允许的操作**:
```python
# 元素定位
page.locator("selector")
locator.nth(index), locator.first, locator.last

# 元素操作  
locator.click()
locator.fill("text")
locator.select_option("value")
locator.set_checked(True/False)
locator.hover()
locator.dblclick()

# 元素状态
locator.is_visible()
locator.is_enabled()
locator.is_checked()
locator.count()

# 内容获取
locator.text_content()
locator.inner_text()
locator.input_value()
locator.get_attribute("name")
```

**约束**:
- 提供 fallback 选择器策略

### 4.3 交互规则
**严格限制**:
- **禁止**: 使用未定义的操作，调用外部框架（Selenium、bs4等）

## 5. 代码质量要求
### 5.1 错误处理
**异常类型**:
- `ValueError`: 参数非法（空值、格式错误）
- `LookupError`: 元素未找到
- `RuntimeError`: 操作失败、状态验证失败

### 5.2 Docstring（若需要）
包含功能概述/参数/返回/异常/示例/实现说明/选择器/注意事项，但本任务输出时可以省略 docstring 以简化。

### 5.3 异常处理机制（必须实现）
- 所有页面交互（点击、输入、选择、等待）需置于 try/except 中；异常要附带操作类型与选择器信息。
- 明确的等待与超时：默认超时 5–10s；禁止无限等待；推荐使用 `locator.wait_for(state="visible")` 或 `page.wait_for_timeout(ms)` 作短暂节流。
- 备用策略与重试：主选择器失败后，按 selector_alt → by_role → by_text 顺序尝试；每一步重试 ≤2 次，间隔 300–800ms。
- 失败返回：
  - 抛出 `RuntimeError(f"<step> failed: <reason> [selector=<...>]")`
  - 或返回结构 `{ "ok": false, "message": "...", "step": "...", "selector": "..." }`
- 幂等与清理：必要时在 finally 中恢复状态（如关闭临时弹层、清理输入）。

### 5.4 代码结构（建议）
1. **参数验证**
2. **候选定位器构造**
3. **等待/滚动并尝试交互（带重试）**
4. **后置校验（URL/可见性）**
5. **返回结果（含 evidence）**

## 6. 输入数据（扩展自网页采集产物）
### 6.1 HTML 控件片段（outerHTML）
```html
{html}
```

### 6.2 页面与运行上下文（来自 meta.json）
```json
{
  "domain": "{meta.domain}",
  "url": "{meta.url}",
  "viewport": {"width": {meta.viewport.width}, "height": {meta.viewport.height}}
}
```

### 6.3 控件树节点（来自 controls_tree.json）
```json
{
  "id": "{ct.id}",
  "action": "{ct.action}",          // 可能为 click/type/select/toggle/navigate/submit/none
  "selector": "{ct.selector}",
  "bbox": {"x": {ct.bbox.x}, "y": {ct.bbox.y}, "w": {ct.bbox.w}, "h": {ct.bbox.h}}
}
```

### 6.4 定位器候选集合（程序应优先使用主选择器，并内置回退）
```json
{
  "selector": "{locators.selector}",
  "selector_alt": {locators.selector_alt_json},
  "by_role": {locators.by_role_json},
  "by_text": {locators.by_text_json},
  "by_dom_index": {locators.by_dom_index}
}
```

