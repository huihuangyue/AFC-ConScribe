# AFC‑Page 控件级 LLM Prompt（refine_text）

用于 `AFCdatabaseBuild/afc_page_snapshot.py` 中的 `llm_refine_text` 步骤：
- 输入：单个 AfcControl 的上下文（`CONTEXT_JSON`）；
- 输出：clean_text / norm_label / logical_name / semantic_text。

<!-- REFINE_TEXT_PROMPT_BEGIN -->
You are assisting with extracting stable functional descriptions of web UI controls on dynamic web pages.

We observe a single control. Its context is given as a JSON object below:

---
CONTEXT_JSON:
{{CONTEXT_JSON}}
---

The page is dynamic: texts may contain dates, numbers, prices, counts, or other frequently-changing tokens.

Your task:
1) Separate **functional tokens** (that describe the control's purpose) from **dynamic tokens** (dates, counts, prices, etc.).
2) Produce a **normalized functional label** for this control from a small controlled vocabulary.
3) Optionally produce a human-readable `logical_name` (Chinese is fine) and a short English `semantic_text`.

Definitions:
- Functional tokens: words like “搜索/查找/提交/登录/下一步/返回/取消/确认/筛选/排序/...”
- Dynamic tokens: dates, times, prices, counts, specific numbers of nights, rooms, passengers, etc.

Controlled vocabulary examples for `norm_label` (choose the closest, or a short new one if none fits):
- Clickable_Submit, Clickable_Next, Clickable_Back, Clickable_Login, Clickable_Logout, Clickable_NavTab,
- Clickable_MarketingCard,
- Editable_Textfield, Editable_SearchBox, Editable_Password,
- Toggle_Checkbox, Toggle_Switch, Toggle_Tab,
- Link_Navigate, Link_OpenDetail, Other_Clickable.

Notes:
- Use `Clickable_Submit` only for controls that clearly submit/confirm/search/advance a form or workflow.
- Use `Clickable_MarketingCard` for clickable marketing / promotion /保障 cards, not for primary submit/search buttons.

Output format:
Return a single JSON object, with keys:
- `"clean_text"`: list of functional tokens (strings, ordered by importance);
- `"norm_label"`: string, one of the controlled labels above (or a close variant);
- `"logical_name"`: string, a concise human-facing name for this control（可用中文）;
- `"semantic_text"`: string, an English sentence describing the control's function (e.g. `"Submit hotel search form"`).

Do NOT include any explanations. Return ONLY the JSON object.
<!-- REFINE_TEXT_PROMPT_END -->

