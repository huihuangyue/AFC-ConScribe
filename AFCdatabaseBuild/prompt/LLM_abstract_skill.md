# 抽象技能层 LLM Prompt（abstract_skill）

用于 `AFCdatabaseBuild/skill/skill_snapshot.py` 中的 `_llm_refine_abstract_skill`：
- 输入：一个抽象技能簇的上下文（`CONTEXT_JSON`）；
- 输出：task_group / task_role / semantic_text / env_sensitivity。

<!-- ABSTRACT_SKILL_PROMPT_BEGIN -->
You are helping to define abstract skills for a web automation agent.

We observed one abstract skill cluster. Its context is provided as a JSON object:

---
CONTEXT_JSON:
{{CONTEXT_JSON}}
---

The JSON describes:
- `abstract_skill_id`: current cluster id (derived from task_group/task_role/norm_label);
- `current_task_group` / `current_task_role`: current coarse task group / role (sometimes already set by simple rules, otherwise `UnknownGroup` / `UnknownRole`);
- `norm_label`: normalized control label such as:
  - `Clickable_Submit`, `Clickable_Login`, `Clickable_MarketingCard`, `Link_Navigate`,
  - `Editable_SearchBox`, `Editable_Textfield`, `UnknownLabel`, etc.;
- `action`: typical UI action (`click`, `navigate`, `none`, ...);
- `afc_control_ids`: ids of concrete controls that belong to this cluster (for your reference only);
- `skills`: concrete skill implementations, each with `id`, `action`, `description`, and `preconditions`;
- `current_semantic_text`: an existing English description if any;
- `current_env_sensitivity`: an existing environment-sensitivity summary if any.

Your task:
1) Based on the skills' descriptions, actions, preconditions and the `norm_label`, assign:
   - `task_group`: a short high-level task family, e.g.
     - generic: `Login`, `Auth`, `Search`, `Booking`, `Marketing`, `Navigation`, `Profile`, `Misc`, ...
     - e-commerce style examples (if applicable): `ProductSearch`, `ProductDetail`, `Cart`, `Checkout`, `Order`, `Account`, `Home`.
   - `task_role`: a short role within the task, e.g.
     `Submit`, `Search`, `OpenDetail`, `ViewMarketingCard`, `Login`, `Logout`, `Filter`, `Navigate`, `AddToCart`, `Checkout`, ...

   Use the following mapping hints when reasonable (do not follow blindly if skills clearly indicate something else):
   - If `norm_label = "Clickable_Login"` or descriptions mention login / sign in:
     - prefer `task_group = "Auth"` and `task_role = "Login"`.
   - If `norm_label = "Editable_SearchBox"` or `Clickable_Submit` used to trigger search:
     - prefer `task_group = "Search"` or `ProductSearch`,
     - and `task_role = "EnterQuery"` (for the input) or `Submit` (for the button).
   - If `norm_label = "Clickable_MarketingCard"`:
     - prefer `task_group = "Marketing"`, `task_role = "ViewCard"` or `OpenPromotion`.
   - If `norm_label = "Link_Navigate"` and preconditions / descriptions show simple navigation:
     - prefer `task_group = "Navigation"`, `task_role = "Navigate"`.
   - If `norm_label = "UnknownLabel"` and there is no clear semantic grouping:
     - you may keep `task_group = "Misc"` and choose a generic task_role like `GenericClick` or `GenericControl`.

   Try to avoid leaving `UnknownGroup` / `UnknownRole` unless the intent is truly unclear.
2) Produce an English `semantic_text`: one sentence describing what this abstract skill does at the task level
   (e.g. `"Submit hotel search form"`, `"Open a marketing promotion card"`, `"Navigate to login page"`).
3) Summarize environment sensitivity as a JSON object `env_sensitivity`, aggregating from skills' preconditions.
   Suggested keys (set to true/false/null as appropriate):
   - `requires_login`: whether this abstract skill requires the user to be logged in;
   - `requires_enterprise_account`: whether it requires an enterprise/corporate account;
   - `device_sensitive`: `"any"`, `"mobile_only"`, `"desktop_only"`, or null;
   - `needs_cookies`: whether specific cookies are required;
   - you may add other boolean or string flags if clearly useful.
   - Optionally, add a nested object `workflow_requires` to describe **workflow-level dependencies**, e.g.:
     ```json
     "workflow_requires": {
       "must_follow_abstract_skills": ["Auth.Login:Clickable_Login"],
       "must_follow_norm_labels": ["Clickable_Login"],
       "notes": "Typical flow: user logs in, then opens search form, then submits."
     }
     ```
4) Define an abstract I/O schema `io_schema` for this abstract skill, aggregating from the skills' arguments:
   - Use a JSON object with:
     - `"args"`: an array of argument specs, each like:
       ```json
       { "name": "destination", "type": "string", "required": true, "description": "Hotel destination or city name" }
       ```
       Use simple types like `"string"`, `"number"`, `"integer"`, `"boolean"`, `"object"`, `"array"`, `"date"`, `"datetime"`, `"enum"`, or `"unknown"`.
     - Optional `"return"`: an object like
       ```json
       { "type": "none", "description": "This skill only triggers UI changes and does not return a value." }
       ```
5) Define an abstract precondition summary `preconditions_abstract` beyond environment flags:
   - Use a JSON object such as:
     ```json
     {
       "typical_url_patterns": ["^https://www.ctrip.com/.*"],
       "required_ui_context": ["home_page", "hotel_search_form_open"],
       "notes": "Typically used on the main hotel search form on the homepage."
     }
     ```

Output format:
Return ONLY a single JSON object with keys:
- `"task_group"`: string;
- `"task_role"`: string;
- `"semantic_text"`: string;
- `"env_sensitivity"`: JSON object or null;
- `"io_schema"`: JSON object or null;
- `"preconditions_abstract"`: JSON object or null.

Do NOT include explanations or extra text.
<!-- ABSTRACT_SKILL_PROMPT_END -->
