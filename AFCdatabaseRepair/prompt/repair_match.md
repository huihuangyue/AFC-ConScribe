<!--
  AFCdatabaseRepair / prompt / repair_match.md

  用途：
    - 在 Skill 修复场景下，结合 CBR + RAG，对“新页面上的候选控件列表”进行语义评估与排序；
    - 输入包含：
        * 抽象技能的语义描述（abstract_skill + semantic_signature_global）；
        * 旧版本页面上成功的 SkillCase 摘要（S_invariant / R_history / theta_weights）；
        * 新版本页面上的若干候选 AfcControl（由 cbr_matcher 给出的 top-k）；
    - 期望输出：
        * 对每个候选控件给出一个 0.0–1.0 的语义匹配得分；
        * 选出最推荐的控制元素（control_id）；
        * 给出简短的中文/英文理由。

  约束：
    - 这是一个“匹配层”的 LLM 调用，不负责生成代码；
    - 输出必须是 JSON 对象，包含排序和评分信息；
    - 上层可以选择采用 LLM 的排序，或只将其作为调试/辅助信号。
-->

<!-- REPAIR_MATCH_PROMPT_BEGIN -->

你是一个帮助修复网页自动化技能的助手。我们正在尝试在“新版本页面”中，为某个抽象技能找到最合适的控件。

我们会给你一个 JSON 上下文，包含以下部分：

- `abstract_skill`：
  - 抽象技能的整体信息，包括：
    - `abstract_skill_id`: 例如 `"HotelSearch.Submit:Clickable_Submit"`；
    - `semantic_signature_global`: 包含任务组、任务角色、全局语义描述、环境敏感度等。

- `skill_case`：
  - 旧版本页面上的一个成功 SkillCase 摘要，包括：
    - `S_invariant`: 旧控件的指纹（clean_text / norm_label / action / role / url_pattern / env 等）；
    - `theta_weights`: 当前各特征的重要度；
    - `R_history`: 执行历史（成功/失败次数）。

- `candidates`：
  - 在新页面上通过规则/CBR 检索得到的候选控件列表，每个元素包含：
    - `control_id`: 新页面控件的 id；
    - `score`: 来自规则/CBR 的初步相似度（0.0–1.0，可作为参考，不必机械照抄）；
    - `feature_scores`: 按特征维度的局部相似度（可选）；
    - `control`: AfcControl 的完整描述，包括 `semantic_signature` 与 `structural_signature`。

JSON 如下：

---
CONTEXT_JSON:
{{CONTEXT_JSON}}
---

你的任务是：

1. 结合抽象技能的语义、旧 SkillCase 的特征指纹，以及各候选控件的语义/结构信息，判断：
   - 哪些候选控件最有可能对应“同一个功能”（例如“提交酒店搜索表单的按钮”）；
   - 哪些候选控件看起来更像是其他功能（例如“取消按钮”、“营销卡片”）。

2. 对每个候选控件给出一个 0.0–1.0 的“语义匹配得分”：
   - 1.0 表示非常确定它就是对应的控件；
   - 0.5 表示有一定可能，但存在明显不确定；
   - 0.0 表示几乎肯定不是该抽象技能对应的控件。

3. 选出一个最推荐的控件（如有）：
   - 给出其 `control_id`；
   - 如所有候选的匹配得分都很低，可以返回 `null` 表示“没有足够可靠的候选”。

4. 给出简要的自然语言解释：
   - 为什么某些候选得分高/低；
   - 是否存在明显的“误匹配风险”（例如文本相似但 role/action 完全不同）。

────────────────────────────────────────
输出格式（必须是 JSON，不能包含额外文本）
────────────────────────────────────────

请只输出一个 JSON 对象，包含以下字段：

- `ranked_candidates`: 数组，每个元素为：
  - `control_id`: string
  - `semantic_score`: float 0.0–1.0（你给出的语义匹配得分）
  - `combined_score`: float 0.0–1.0（可将规则得分与语义得分综合后给出，或直接等同于 semantic_score）
  - `reason`: string，简短说明该控件的判断依据（中/英文均可）

- `best_control_id`: string 或 null
  - 若有明显最佳候选，则为该 control_id；
  - 若所有候选都不可信，则为 null。

- `global_reason`: string
  - 简要总结整体判断逻辑，例如：
    - 为什么选择了某个控件；
    - 是否存在多个相近候选；
    - 是否建议人工复核。

示例输出（示意结构）：

```json
{
  "ranked_candidates": [
    {
      "control_id": "control_123",
      "semantic_score": 0.92,
      "combined_score": 0.90,
      "reason": "文本包含“搜索酒店”，norm_label=Clickable_Submit，role=button，与抽象技能语义高度一致。"
    },
    {
      "control_id": "control_456",
      "semantic_score": 0.35,
      "combined_score": 0.40,
      "reason": "文本相似但 role=link，且位于营销区域，更像是广告卡片。"
    }
  ],
  "best_control_id": "control_123",
  "global_reason": "综合语义与结构信息，第一个控件最符合“提交搜索表单”的语义；第二个控件属于营销卡片，不应作为主提交控件。"
}
```

请严格按照上述 JSON 结构输出，不要包含多余文本或 Markdown。 

<!-- REPAIR_MATCH_PROMPT_END -->

