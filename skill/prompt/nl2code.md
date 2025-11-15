# Playwright Python 代码生成规范

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

### 4.2 选择器策略
**优先级顺序**:
1. `data-*` 属性、稳定的 `id`、`name`
2. `role`、`aria-*` 属性
3. 稳定的 `class`（避免哈希类名）
4. 文本匹配（`:text=`、`has_text`）
5. 结构性 CSS 选择器

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

**处理策略**:
```python
try:
    # Playwright 操作
except Exception as e:
    raise RuntimeError(f"操作失败: {详细描述}") from e
```

### 5.2 文档要求
**必须包含的 Docstring 结构**:
```python
"""
功能概述: 一句话描述函数目标

详细说明:
- 控件用途与核心功能
- 是否改变页面状态
- 幂等性说明
- 自动处理的场景

参数(Args):
    page (Page): Playwright 页面对象
    param_name (type): 参数说明

返回(Returns):
    type: 返回值说明

异常(Raises):
    ValueError: 触发条件
    LookupError: 触发条件  
    RuntimeError: 触发条件

示例(Example):
    function_name(page, param="value")

实现说明(Implementation):
    - 步骤1: ...
    - 步骤2: ...

选择器(Selectors):
    - 主选择器: "selector"
    - 备用选择器: "fallback_selector"

注意事项(Notes):
    - 重要限制或使用建议
"""
```

### 5.3 异常处理机制（必须实现）
- 所有页面交互（点击、输入、选择、等待）需置于 try/except 中；异常要附带操作类型与选择器信息。
- 明确的等待与超时：默认超时 5–10s；禁止无限等待；推荐使用 `locator.wait_for(state="visible")` 或 `page.wait_for_timeout(ms)` 作短暂节流。
- 备用策略与重试：主选择器失败后，按 selector_alt → by_role → by_text 顺序尝试；每一步重试 ≤2 次，间隔 300–800ms。
- 失败返回：
  - 抛出 `RuntimeError(f"<step> failed: <reason> [selector=<...>]")`
  - 或返回结构 `{ "ok": false, "message": "...", "step": "...", "selector": "..." }`
- 幂等与清理：必要时在 finally 中恢复状态（如关闭临时弹层、清理输入）。

示例模板：
```python
def _try_click(locator, *, timeout=8000, retries=2):
    for i in range(max(1, retries)):
        try:
            locator.wait_for(state="visible", timeout=timeout)
            locator.click(timeout=timeout)
            return True
        except Exception as e:
            if i + 1 >= retries:
                raise RuntimeError(f"click failed after {retries} tries: {e}") from e
            page.wait_for_timeout(300 * (i + 1))
```

### 5.3 代码结构
**推荐流程**:
1. **参数验证**: 检查输入参数合法性
2. **元素查找**: 构建候选元素列表
3. **状态检查**: 验证元素可见性和可操作性
4. **执行操作**: 按正确顺序执行交互
5. **结果验证**: 确认操作效果
6. **返回结果**: 返回执行状态或提取的数据

## 6. 高级要求

### 6.1 智能处理
- **动态内容**: 处理异步加载、分页等动态变化
- **状态感知**: 操作前后重新获取元素状态
- **容错机制**: 提供多种选择器的 fallback 策略
- **性能优化**: 避免不必要的重复查询
- **弹窗避免**: 如果进行某些操作之后会进入弹窗，可以将其关闭。之后如果没有达成目标操作，可以进行重试操作

### 6.2 业务适配
- **功能推测**: 当元素文本为空时，根据 HTML 结构推测功能
- **参数映射**: 业务参数与底层操作的合理映射
- **流程优化**: 确保操作顺序符合用户交互逻辑

## 7. 禁止事项

### 7.1 绝对禁止
- 生成多个顶层函数
- 使用未授权的第三方库
- 操作未标记的元素
- 使用 `time.sleep` 等阻塞等待
- 将属性当作方法调用（如 `first()`）
- 假设未在HTML中定义的子元素

### 7.2 代码规范
- 禁止硬编码不稳定的动态值
- 禁止假设不存在的 API 方法
- 禁止在函数外编写执行代码
- 禁止输出非 Python 语法内容

## 8. 输入数据（扩展自网页采集产物）

### 8.1 HTML 控件片段（outerHTML）
```html
{html}
```

### 8.2 页面与运行上下文（来自 meta.json）
```json
{
  "domain": "{meta.domain}",
  "url": "{meta.url}",
  "viewport": {"width": {meta.viewport.width}, "height": {meta.viewport.height}}
}
```

### 8.3 控件树节点（来自 controls_tree.json）
```json
{
  "id": "{ct.id}",
  "action": "{ct.action}",          // 可能为 click/type/select/toggle/navigate/submit/none
  "selector": "{ct.selector}",
  "bbox": {"x": {ct.bbox.x}, "y": {ct.bbox.y}, "w": {ct.bbox.w}, "h": {ct.bbox.h}}
}
```

### 8.4 定位器候选集合（程序应优先使用主选择器，并内置回退）
```json
{
  "selector": "{locators.selector}",
  "selector_alt": {locators.selector_alt_json},  // 数组，最多3条
  "by_role": {locators.by_role_json},            // 可能为空
  "by_text": {locators.by_text_json},            // 可能为空，≤3条
  "by_dom_index": {locators.by_dom_index}        // 兜底，不作为首选
}
```

### 8.5 前置条件骨架（用于推断等待与后置检查）
```json
{preconditions_json}
```

### 8.6 参数模式（args_schema，用于决定函数需要的业务参数）
```json
{args_schema_json}
```
- action=type → 可能需要 `{"text": "string"}`
- action=select → 可能需要 `{"value": "string"}`

### 8.7 后置检查提示（可选）
```json
{
  "expect_url_change": {post.expect_url_change_bool},
  "expect_appear": {post.expect_appear_selectors_json},   // 操作后应出现的选择器列表
  "expect_disappear": {post.expect_disappear_selectors_json}
}
```

## 9. 输出要求

**直接输出**: 一个完整的 Python 函数，包含：
- 完整的模块导入
- 函数定义与实现
- 详细的中文 docstring（注明使用了哪些定位器，后置检查策略）
- 内部辅助函数（如需要）

**必须满足**：
- 定位器使用顺序严格遵循 8.4 的优先级；为每一步交互实现“主定位 + 回退”链路；
- 根据 8.5/8.7 合理设置等待与后置检查：
  - 当 `expect_url_change` 为真时，操作后等待 URL 变化（或短暂 networkidle）；
  - 当 `expect_appear/disappear` 非空时，操作后等待对应元素出现/消失（存在超时与失败返回路径）；
- 严格遵守第 7 节“禁止事项”；不要发起网络/文件 IO；
- 若 `args_schema` 要求的参数缺失，应返回清晰的异常或校验错误；
- 若 `{locators.selector}` 与 HTML 的 `__selector` 同时存在但不一致，应在 docstring 的 Notes 中说明处理策略（以 `{locators.selector}` 为主）。

**示例格式**:
```python
from typing import Optional, List
from playwright.sync_api import Page, Locator

def function_name(page: Page, param: str) -> Optional[str]:
    """详细的文档字符串"""
    
    def _internal_helper():
        """内部辅助函数"""
        pass
    
    # 函数实现
    pass
```
