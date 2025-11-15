# 定位器精修（Locator Refine）模板

任务：从提供的候选中选出最稳健的主定位器（primary）与≤3条回退（selector_alt），可补充 by_role/by_text；返回 JSON。

## 输入（占位符）
- 页面：{meta.domain}, {meta.url}
- 控件：{ct.id}, {ct.selector}, {ct.action}
- 片段：{snippet_html}
- 候选：
  - CSS：{candidates.css_json}
  - by_role：{candidates.by_role_json}
  - by_text：{candidates.by_text_json}
  - by_dom_index：{candidates.by_dom_index}
- 特征：{feature.tag}, {feature.id}, {feature.name}, {feature.role}, {feature.aria_label}, {feature.classes}, {feature.data_testid}
- 邻近文本：{neighbor_texts_json}

## 输出（占位符，JSON）
```json
{
  "primary": "<string>",
  "selector_alt": ["<string>"],
  "by_role": {"role": "<string>", "name": "<string>", "exact": true},
  "by_text": ["<string>"],
  "rationale": "<string>"
}
```
