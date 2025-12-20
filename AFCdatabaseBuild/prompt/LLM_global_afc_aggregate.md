# 全局 AFC 聚合与初始权重估计 Prompt（global_afc_aggregate）

> 适用场景：在“全局模式下聚合 + 初始化权重”这一步，用 LLM 对同一个 abstract_skill_id
> 在多个 run_dir / 多个 AfcControl 上的特征进行分析，帮助我们：
>
> - 识别哪些特征在跨版本/跨页面上更稳定（适合赋予更高权重）；
> - 给出该抽象技能的全局语义描述与环境要求；
> - 产出一份初始的 `theta_weights` 建议（作为程序默认值的参考）。
>
> 本模板只负责“建议参数”，真正写入数据库时仍应结合程序化统计与实验结果。

<!-- GLOBAL_AFC_AGG_PROMPT_BEGIN -->

你是一个帮助构建“跨版本网页技能库”的分析助手。  
我们会给你一个 JSON，描述某个抽象技能 `abstract_skill_id` 在多个采集 run_dir 中的观测情况。

每条观测对应一个控件实例（AfcControl 的摘要），包含：

- `run_dir`: 采集目录路径；
- `domain`: 域名；
- `S_invariant`: 该控件的相对稳定特征集合，包括：
  - `clean_text`: 已经过滤动态词片的文本 token 列表；
  - `norm_label`: 规范化控件标签（例如 `Clickable_Submit` / `Clickable_Login` 等）；
  - `action`: 典型操作类型（`click` / `navigate` / `input` 等）；
  - `role`: 可访问性角色列表；
  - `url_pattern`: 一般匹配该控件所在页面的 URL 正则；
  - `form_context`: 所在表单或模块的抽象上下文（如已提供）；
  - `env`: 环境信息（登录态、需要的 cookies、视口要求等）。

同时，我们会提供该抽象技能在单个 run_dir 上的初步抽象信息，如：

- `task_group` / `task_role`；
- `semantic_text`：任务级的一句话描述；
- `env_sensitivity`：环境敏感性摘要（requires_login 等）。

你的任务是：

1. 观察所有观测样本，识别哪些特征 **跨 run_dir 更稳定**，哪些特征波动较大；  
2. 给出一份“全局语义”描述（global_semantic）和“全局环境要求”描述（global_env）；  
3. 给出一份特征权重 `theta_weights` 建议，用于后续在新页面上做节点重定位时的相似度加权。

### 需要你输出的字段

请输出一个 JSON 对象，必须包含：

- `theta_weights`: 一个键值对对象，key 为特征名称，value 为建议权重（0~1 的浮点数），例如：
  ```json
  {
    "clean_text": 0.7,
    "norm_label": 0.9,
    "action": 0.8,
    "role": 0.6,
    "url_pattern": 0.5,
    "env.login_state": 0.6
  }
  ```
  要求：
  - 高权重表示该特征在你观察到的样本中更稳定、更可靠；  
  - 低权重表示该特征变化较大或容易误导；  
  - 不需要保证权重之和为 1，但每个值必须在 `[0.0, 1.0]` 区间内。

- `global_semantic_text`: 一句话描述该抽象技能的任务级含义，可以参考输入中的 semantic_text，但要考虑跨版本差异；

- `global_env_sensitivity`: 一个 JSON 对象，汇总环境要求（requires_login / device_sensitive 等），字段结构可参考：
  ```json
  {
    "requires_login": true,
    "device_sensitive": "any",
    "needs_cookies": false,
    "notes": "Typically used on the main hotel search form on the homepage."
  }
  ```

- `evidence_summary`: 一个简短说明（字符串），解释你得出这些权重与全局语义的原因，例如指出：
  - 哪些特征在所有样本中几乎不变；
  - 哪些特征在不同版本中变化较大；
  - 是否存在异常样本（outlier）。

### 输入 JSON 结构示例

你将收到的上下文大致如下：

```json
{
  "abstract_skill_id": "HotelSearch.Submit:Clickable_Submit",
  "task_group": "HotelSearch",
  "task_role": "Submit",
  "semantic_text": "Submit hotel search form",
  "env_sensitivity": {
    "requires_login": false,
    "device_sensitive": "any"
  },
  "observations": [
    {
      "run_dir": "workspace/data/ctrip_com/20251120004106",
      "domain": "ctrip.com",
      "S_invariant": {
        "clean_text": ["搜索", "查找"],
        "norm_label": "Clickable_Submit",
        "action": "click",
        "role": ["button"],
        "url_pattern": "^https://www.ctrip.com/.*",
        "env": {"login_state": "logged_out"}
      }
    },
    {
      "run_dir": "workspace/data/ctrip_com/20260302091133",
      "domain": "ctrip.com",
      "S_invariant": {
        "clean_text": ["搜索", "查找"],
        "norm_label": "Clickable_Submit",
        "action": "click",
        "role": ["button"],
        "url_pattern": "^https://www.ctrip.com/hotel/.*",
        "env": {"login_state": "logged_out"}
      }
    }
  ]
}
```

### 输出格式要求

请 **只输出一个 JSON 对象，不要附加任何自然语言**，结构类似：

```json
{
  "theta_weights": {
    "clean_text": 0.7,
    "norm_label": 0.9,
    "action": 0.8,
    "role": 0.6,
    "url_pattern": 0.5,
    "env.login_state": 0.6
  },
  "global_semantic_text": "Submit hotel search form on the main Ctrip hotel search page.",
  "global_env_sensitivity": {
    "requires_login": false,
    "device_sensitive": "any",
    "needs_cookies": false,
    "notes": "Typically used for anonymous hotel search; login may be required only for booking."
  },
  "evidence_summary": "Across all observed versions, norm_label and action are consistent, clean_text tokens remain stable, while url_pattern shifts from root to /hotel/. Therefore norm_label/action/clean_text receive higher weights, url_pattern slightly lower."
}
```

请严格遵守上述字段名称和取值范围，保持输出为合法 JSON。  
不要输出额外注释或 Markdown，只输出 JSON。 

<!-- GLOBAL_AFC_AGG_PROMPT_END -->

