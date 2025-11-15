# 命名生成（Naming）模板

任务：为技能生成 `label` 与 `slug`，`slug` 需蛇形、小写、长度≤32；与动作/语义一致、简洁。

## 输入（占位符）
- 页面：{meta.domain}
- 语义：{feature.role}, {feature.aria_label}, {feature.text}
- 邻近文本：{neighbor_texts_json}
- 动作：{ct.action}

## 输出（占位符，JSON）
```json
{
  "label": "<string>",
  "slug": "<string>"
}
```
