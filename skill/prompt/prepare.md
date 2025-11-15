# 准备步骤生成（Prepare）模板

任务：生成 `prepare(env, ctx, options)` 的最小 Python 源码，用于满足前置条件（如关闭遮罩、展开抽屉、切换 tab）。

## 输入（占位符）
- 遮挡/抽屉选择器：{context.overlay_selectors_json}, {context.drawer_selectors_json}
- Tab 文本/选择器：{context.tab_texts_json}
- 就绪选择器：{context.ready_selector}
- 页面：{meta.url}

## 输出（占位符，代码）
```python
{python_code}
```
