<!--
  AFCdatabaseEvolve / prompt / update_case_rating.md

  用途：
    - 在 update_case 阶段，针对单个 SkillCase + 一次执行/修复结果，
      使用 LLM 帮助判断：
        * 需要如何微调 theta_weights（哪些特征更可靠 / 更不可靠）；
        * 是否需要对 (L_S, L_A, rebuild_grade) 做轻微覆写；
        * 是否应将当前样本标记为负样本 / 可能过时。

  设计约束：
    - 这是一个“可注入”的建议接口：LLM 只给出建议，不直接写库；
    - 调用层可以选择完全采用、部分采用或完全忽略 LLM 的建议；
    - 输出必须是严格的 JSON，字段尽量稳定、易于版本演化。
-->

<!-- UPDATE_CASE_RATING_PROMPT_BEGIN -->

You are assisting an AFC (Abstract Function Control) evolution engine to update a single
SkillCase in its global database.

We provide you with a compact JSON context consisting of:

---
CONTEXT_JSON:
{{CONTEXT_JSON}}
---

The JSON contains three main parts:

- `skill_case`:
  - A simplified view of one SkillCase from the AFC global DB, including:
    - `S_invariant`: the current invariant state fingerprint of the control;
    - `theta_weights`: current feature weights (0.0–1.0) for fields such as
      `clean_text`, `norm_label`, `action`, `role`, `url_pattern`, `env.login_state`, etc.;
    - `R_history`: execution statistics, with `exec_success` and `exec_fail` counters;
    - `levels`: optional `{ "L_S": 0/1/2, "L_A": 0/1/2 }` (semantic/env drift vs. implementation change);
    - `rebuild_grade`: optional 0–4 (folded grade from levels).

- `exec_result`:
  - A single execution / repair result for this SkillCase, containing (at least):
    - `exec_success`: boolean;
    - `error_type`: string or null (e.g. `"NoSuchElement"`, `"Timeout"`), if failed;
    - `timestamp`: ISO datetime string or null;
    - optional other fields (e.g. run_dir, afc_control_id, skill_id).

- `diff_info`:
  - Structured comparison between the old and new versions of this skill, including:
    - `sim_S`: float similarity of S_invariant (0.0–1.0);
    - `reuse_A`: float measure of code/template reuse (0.0–1.0);
    - `L_S`: optional int in {0,1,2} (semantic/env drift level);
    - `L_A`: optional int in {0,1,2} (implementation change level);
    - `rebuild_grade`: optional int in {0,1,2,3,4};
    - optional `drift_E` or other fields;
    - optional `notes` summarizing the change.

Your job is to provide a **small, structured update suggestion** for this SkillCase, focusing on:

1. How to adjust `theta_weights`:
   - Decide which features should slightly increase or decrease in weight,
     based on success/failure and the kind of change (L_S, L_A, sim_S, reuse_A).
   - The update should be incremental, not drastic (think of learning rate style).

2. Whether the current labels `(L_S, L_A)` and `rebuild_grade` look reasonable:
   - If they are clearly inconsistent with the described changes, you may propose overrides;
   - Otherwise, leave them as-is (do not override).

3. Whether this sample should be considered:
   - a negative example (bad match, should be penalized in future retrieval);
   - a maybe-obsolete sample (this SkillCase seems no longer relevant to current versions).

────────────────────────────────────────
Guidelines (high-level intuition)
────────────────────────────────────────

- If `exec_success == true` and the change is minor (high sim_S, high reuse_A, high grade):
  - Slightly increase weights for semantic features that remained stable:
    `clean_text`, `norm_label`, `action`, `role`.
- If `exec_success == true` but drift is high (L_S >= 1 or grade <= 2):
  - It suggests that url-pattern / environment features also helped.
  - Slightly increase `url_pattern` and `env.login_state`.
- If `exec_success == false`:
  - Overall, the current feature combination is unreliable for this environment:
    - Slightly decrease most weights, in particular features that look misleading
      (e.g. wrong text, wrong role).
  - For severe mismatches (e.g. matched a "Cancel" button instead of "Submit"):
    - Consider marking this as a negative sample.
- For very old patterns that repeatedly fail and show low sim_S to new pages:
  - You may suggest `mark_maybe_obsolete = true`.

You do NOT need to apply these rules literally; treat them as hints. The final logic is your own
expert judgement built from the context JSON.

────────────────────────────────────────
Output format (strict JSON, no extra text)
────────────────────────────────────────

Return **only** a single JSON object with the following fields:

- `theta_delta` (object):
  - A dictionary of feature -> delta_weight suggestions (small increments):
    ```json
    {
      "clean_text": +0.05,
      "norm_label": -0.10,
      "url_pattern": +0.03,
      "env.login_state": 0.0
    }
    ```
  - Each value should be between -0.5 and +0.5 (inclusive).
  - If you do not want to change a feature, you may omit it or set it to 0.0.

- `override_levels` (object or null):
  - Either `null`, or an object with:
    ```json
    { "L_S": 0, "L_A": 1 }
    ```
  - Use this only if the existing levels are clearly wrong. Otherwise set to null.

- `override_rebuild_grade` (integer or null):
  - Either `null`, or an integer in {0,1,2,3,4}.
  - Again, only override when the current grade is clearly inconsistent.

- `flags` (object):
  - A set of booleans to guide higher-level logic:
    - `mark_negative_sample`: true/false
    - `mark_maybe_obsolete`: true/false
  - For example:
    ```json
    { "mark_negative_sample": true, "mark_maybe_obsolete": false }
    ```

- `reasoning` (string):
  - A short natural-language explanation (Chinese or English) summarizing:
    - why you adjusted certain weights;
    - why you overrode (or did not override) levels/grade;
    - why you set the flags.

Example output:

```json
{
  "theta_delta": {
    "clean_text": 0.05,
    "norm_label": 0.03,
    "url_pattern": 0.02,
    "env.login_state": 0.0
  },
  "override_levels": null,
  "override_rebuild_grade": null,
  "flags": {
    "mark_negative_sample": false,
    "mark_maybe_obsolete": false
  },
  "reasoning": "The skill succeeded with small UI changes; text and norm_label remained stable, so their weights are slightly increased. L_S and L_A look consistent with the diff, no need to override."
}
```

Do NOT output any extra commentary, markdown, or surrounding text.  
Only return the naked JSON object.

<!-- UPDATE_CASE_RATING_PROMPT_END -->

