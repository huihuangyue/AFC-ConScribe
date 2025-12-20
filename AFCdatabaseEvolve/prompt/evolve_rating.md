<!--
  AFCdatabaseEvolve / prompt / evolve_rating.md

  用途：
    在一次技能修复 / 重建后，我们希望用 LLM 帮助判断：
      - 语义 / 环境漂移强度（S 轴）；
      - 实现改动强度（A 轴）；
      - 对应的二维等级 (L_S, L_A) 与折叠后的一维 rebuild_grade (0–4)；
      - 以及一些自然语言解释，辅助人工审核。

  注意：
    - 该提示词只负责“建议值”，真实写回数据库时，仍应结合程序化计算的 sim_S / reuse_A 等指标；
    - 输出必须是 JSON，便于解析与对比（LLM vs 规则）。
-->

<!-- EVOLVE_RATING_PROMPT_BEGIN -->

你是一个帮助分析“网页技能重建力度”的助手。我们给你：

1. 某个技能在旧版本页面上的关键信息（旧 SkillCase 摘要）；  
2. 同一个技能在新版本页面上的信息（新 SkillCase 摘要）；  
3. 旧/新代码差异的概要（代码 diff 摘要）；  
4. 一些由程序预先计算的指标（例如 sim_S / reuse_A 等），你可以参考但不必机械照抄。

你的任务是：根据这些信息，给出该次重建的“力度评估”：

- 语义/环境漂移等级 L_S ∈ {0,1,2}；
- 实现改动等级 L_A ∈ {0,1,2}；
- 折叠后的 rebuild_grade ∈ {0,1,2,3,4}；
- 并给出简要的自然语言解释。

### 等级定义（重要，请仔细遵守）

1. 语义/环境漂移等级 L_S：

- L_S = 0（轻漂移）：  
  - 功能语义几乎不变，文本/role/action 基本一致；  
  - url_pattern 和环境（登录态/设备）只做了小幅修改。

- L_S = 1（中漂移）：  
  - 功能语义仍然相同，但页面布局或环境有明显变化；  
  - 例如：路径段改变、需要额外展开某些区域、登录逻辑略有变化等。

- L_S = 2（重漂移）：  
  - 控件迁移到完全不同的容器/流程；  
  - 原有的不少语义特征（文本/上下文）已经不再适用，需重新理解页面。

2. 实现改动等级 L_A：

- L_A = 0（轻改动）：  
  - 代码只做了小修小补，例如调整等待时间、增加少量日志或容错；  
  - 主要控制流与参数结构保持不变。

- L_A = 1（中改动）：  
  - 保持原有主流程，但修改了多个步骤或分支；  
  - 替换部分 locator、增加/删除个别子流程。

- L_A = 2（重改动）：  
  - 大部分代码重写或替换；  
  - 控制流结构变化显著（多页向导、新的对话框流程等）。

3. 折叠后的 rebuild_grade：

给定 (L_S, L_A) 后，按照下面规则折叠为 0–4 级：

- 如果 abstract_skill_id 在新旧之间发生变化，则直接视为 grade = 0（完全重构）。  
- 否则：
  - (0,0) → grade = 4（微量修正）  
  - (0,1) 或 (1,0) → grade = 3（轻度修正）  
  - (1,1) 或 (2,0) 或 (0,2) → grade = 2（中度修正）  
  - (1,2) 或 (2,1) → grade = 1（大部分修正）  
  - (2,2) → grade = 0（完全重构）

### 你会收到的上下文 JSON

我们会给你一个 JSON，字段类似：

```json
{
  "abstract_skill_id_old": "HotelSearch.Submit:Clickable_Submit",
  "abstract_skill_id_new": "HotelSearch.Submit:Clickable_Submit",
  "old_S": {
    "clean_text": ["搜索", "查找"],
    "norm_label": "Clickable_Submit",
    "action": "click",
    "role": ["button"],
    "url_pattern": "^https://www.ctrip.com/.*",
    "env": {"login_state": "logged_out"}
  },
  "new_S": {
    "clean_text": ["搜索", "查找"],
    "norm_label": "Clickable_Submit",
    "action": "click",
    "role": ["button"],
    "url_pattern": "^https://www.ctrip.com/hotel/.*",
    "env": {"login_state": "logged_out"}
  },
  "code_diff_summary": {
    "loc_old": 120,
    "loc_changed": 10,
    "loc_deleted": 0,
    "description": "Only added extra wait_for_load_state and logging."
  },
  "precomputed_metrics": {
    "sim_S": 0.92,
    "reuse_A": 0.88
  }
}
```

### 你的输出格式（必须是 JSON）

请 **只输出一个 JSON 对象，不要附加任何自然语言**。JSON 结构必须包含：

- `L_S`: 0/1/2  
- `L_A`: 0/1/2  
- `rebuild_grade`: 0/1/2/3/4  
- `reason_S`: 简要说明为什么给出这个 L_S（中文或英文均可）；  
- `reason_A`: 简要说明为什么给出这个 L_A；  
- `reason_grade`: 简要说明 grade 的依据；
- `notes`（可选）: 其他有助于人类理解的补充说明。

例如：

```json
{
  "L_S": 0,
  "L_A": 1,
  "rebuild_grade": 3,
  "reason_S": "页面功能语义和控件文本基本不变，只是 URL 路径从根路径移动到 /hotel/ 下。",
  "reason_A": "主要控制流保持一致，只新增等待与日志，代码改动约占 10%",
  "reason_grade": "(L_S,L_A)=(0,1)，按折叠规则属于轻度修正（grade=3）。",
  "notes": "这类变更适合归为轻度修正，用于评估 Repair 策略效果。"
}
```

请严格遵守以上字段与取值范围，保持输出为合法 JSON。不要输出多余文本。 

<!-- EVOLVE_RATING_PROMPT_END -->

