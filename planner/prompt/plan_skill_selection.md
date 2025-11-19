# 技能选择说明（planner.planner）

你将收到一个 JSON 对象，包含字段：

- `task`: 用户的自然语言任务。
- `page`: 当前页面的摘要信息。
  - `url`: 当前页面 URL。
  - `title`: 页面标题。
  - `domain`: 站点域名。
  - `main_block`: 页面主模块的摘要，包含 `id`、`selector`、`short_name`、`short_desc`。
- `candidates`: 候选技能列表，每个元素包含：
  - `id`: 技能唯一标识（如 `d316`）。
  - `name`: 技能名称（若无则与 `id` 相同）。
  - `selectors`: 一组用于定位该技能相关控件的 CSS 选择器。
  - `has_args`: 布尔值，指示该技能是否需要参数。
  - `arg_names`: 参数名称列表（若有）。
  - `score`: 本地打分（BM25 + 规则偏置）。
  - `reason`: 本地打分的简要说明（如 `main_block_related; intent=hotel`）。

你的目标：**仅在 `candidates` 中选择一到数个技能组成执行计划**，并可选给出若干备选技能。

---

## 输出要求

- 必须返回一个单一的 JSON 对象，不要包含额外文字、说明或 Markdown。
- 结构必须为：

```json
{
  "steps": [
    {
      "skill_id": "d316",
      "reason": "先在酒店搜索模块填写并提交表单"
    }
  ],
  "backups": ["d97", "d896"]
}
```

- 字段含义：
  - `steps`: 按执行顺序排列的步骤列表，每个步骤指定一个 `skill_id` 和中文 `reason`。
  - `backups`: 可选备选技能列表，按优先级从高到低排列。

---

## 约束与建议

- 只能使用 `candidates` 中出现过的 `id` 作为 `skill_id`，**不要发明新的技能**。
- `steps` 可以只包含一个技能，也可以包含多个步骤（例如先点击导航，再填写表单）。
- 若单个技能即可完成任务，可以只返回一个 step。
- `backups` 中的技能必须在 `candidates` 中出现，且不与 `steps` 中重复。
- `reason` 请使用简短中文，说明：
  - 为什么选择这个技能；
  - 如果有多步，简要说明执行顺序和依赖关系。

输出中不要加入任何额外解释，只返回符合上述结构的 JSON。
