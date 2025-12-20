<!--
  AFCdatabaseRepair / prompt / repair_code.md

  用途：
    - 在 Skill 修复场景下，给定：
        * 旧技能的 JSON 描述（含 locators / preconditions / program.code 摘要）；
        * 新页面上候选控件的 AfcControl 描述；
        * 全局 AFC 库中该 SkillCase 的 S_invariant / theta_weights；
      请 LLM 生成一份“适配新页面”的 Python 代码实现，以及简短的修复说明。

  约束：
    - 输出必须是 JSON 对象，便于解析；
    - 不负责写回文件，只给出代码和说明；
    - 代码语言固定为 Python，同步使用 Playwright sync API。
-->

<!-- REPAIR_CODE_PROMPT_BEGIN -->

你是一个帮助修复网页自动化技能的助手。我们给你一个 JSON 上下文，其中包含：

- `old_skill`：
  - 旧技能的精简描述，包括：
    - `id` / `domain`
    - `locators`: 旧版本的主要 selector / by_text / by_role 等
    - `preconditions`: URL 匹配条件、遮罩黑名单等
    - `program`: 旧的 Python 代码文本（使用 Playwright 的同步 API）
- `candidate_control`：
  - 新版本页面上、经 CBR 匹配得到的候选控件 AfcControl 描述，包含：
    - `semantic_signature`: 文本、norm_label、role、url_pattern、env
    - `structural_signature`: selector_candidates、bbox 等
- `skill_case`：
  - 全局 AFC 库中该技能对应的 SkillCase，包含：
    - `S_invariant`: 参考的不变特征指纹；
    - `theta_weights`: 当前各特征的重要度。

JSON 如下：

---
CONTEXT_JSON:
{{CONTEXT_JSON}}
---

你的任务是：根据上述信息，生成一份“适配新页面”的 Python 代码实现，遵守以下要求：

1. 代码风格与旧版本保持一致：
   - 使用 Playwright 的同步 API (`Page` / `Locator`)；
   - 使用类似的参数签名，如果旧代码已经是一个功能完整的函数，请尽量复用参数列表；
   - 保留原有功能语义（例如“提交酒店搜索表单”的整体流程）。

2. 更新定位逻辑（Selectors）：
   - 用 `candidate_control.structural_signature.selector_candidates` 以及
     `semantic_signature.clean_text` / `role` 作为主要依据；
   - 尽量避免使用脆弱的 index-only selector；
   - 可以使用 `page.locator("CSS")` 或 `page.get_by_role(...).get_by_text(...)` 等更稳健的组合。

3. 保留原有的错误处理与参数校验逻辑：
   - 若旧代码对参数做了校验（ValueError 等），请在新代码中保留；
   - 异常类型可以适当简化，但不要完全删除错误处理。

4. 结构化输出：
   - 不要输出解释性文字或 Markdown；
   - 只输出一个 JSON 对象，包含：
     - `program_code`: 字符串，新版完整 Python 代码；
     - `notes`: 简短说明(中文或英文)，解释你如何利用新控件信息调整了 selector / 逻辑。

示例输出格式（注意：这是结构示例，具体代码和说明需根据上下文生成）：

```json
{
  "program_code": "from typing import Optional, Dict\\nfrom playwright.sync_api import Page\\n\\n# ... 你的新实现 ...",
  "notes": "保留了原有 perform_hotel_search 的参数和业务逻辑，将主要入口容器的 selector 更新为新的 class，并使用 get_by_role/get_by_text 辅助定位提交按钮。"
}
```

请严格按照上述 JSON 格式输出，不要添加多余文本。 

<!-- REPAIR_CODE_PROMPT_END -->

