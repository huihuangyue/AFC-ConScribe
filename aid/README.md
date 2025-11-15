# AID：技能修理子系统（Skill Repair / Evolution）

目标：当技能执行失败时，输入“已失效的技能 JSON”与“关联采集信息（旧）+ 当前网页采集信息（新）”，产出一个“修复后的技能 JSON”。强调“修理而非重构”——尽量保持技能 ID、结构与字段不变，只对必要项做最小补丁。

服务对象：S = (Preconditions, Program)。
- 修预条件：Refine Preconditions（减少因环境状态变化导致的误触/误杀）。
- 修程序：Patch Program（增强定位/时序/等待/滚动/回退等稳定性）。
- 辅助修理：修定位器（Locators）作为 Program 的输入；必要时微调 `args_schema`、`label/slug`。

本文件提供：
- 完整的 AID 目录结构（仅文档与接口说明，暂不落代码）。
- 端到端工作流（从输入到修复产出）。
- 哪些步骤需 LLM / 不需 LLM。
- 各程序文件的输入/输出、引用关系与产出约定。

## 1. 修理触发与判定（高层工作流）

1) 失败捕获（由编排/执行层驱动）
- 输入：一次执行失败的上下文（技能 JSON、运行参数、页面快照/DOM 摘要、异常消息）
- 输出：失败事件，交给 AID 处理

2) 快速诊断（上下文失配 vs 技能损坏）
- 步骤：
  - 先用 `preconditions` 做确定性校验（URL/exists/not_exists/viewport/可选 visible 等）
  - 若“不满足”，归因为“上下文失配”(Mismatch)；若“满足”仍失败，归因为“技能损坏”(Damage)
- 输出：`DiagnosticReport`（包含结论与证据）

3) 分流处理
- Mismatch：返回“重新规划”(Re-plan) 建议（如先关闭遮罩、展开抽屉、切换 tab、跳转到正确 URL）
- Damage：进入修理管线（见第 2 节），生成一个或多个候选修理补丁（Patches）

4) 候选验证与选择
- 对每个补丁进行静态安全检查 + 干跑/真跑验证（可配置）
- 按“通过率/稳定性/最小改动”择优

5) 应用与记录
- 应用补丁，写回技能库（或生成新版本）；记录 `health` 统计与经验样本（供提示优化/回溯）

## 2. 修理管线（Damage 方向）

- Root Cause 猜测（并行/按优先级）：
  1) 选择器/定位器失效（DOM 属性微调/类名变化）
  2) 时序问题（等待不足、懒加载/滚动缺失）
  3) 遮挡问题（遮罩/抽屉/对话框覆盖）
  4) 文本/国际化变更（name/text 不稳定）
  5) 页面结构改动（容器层级、路径前缀变化）

- 修理策略（按影响面从小到大）：
  A) Locator Patch（仅更新 `locators` 与 `preconditions.exists`）
  B) Program Patch（在程序中加入等待/滚动/可见性检查/重试/回退路径）
  C) Preconditions Refine（增删 `not_exists/visible/viewport/text_contains` 等弱约束）
  D) 结构性变动（必要时新增一个技能版本，保留旧版以便兼容）

- 候选生成方式：
  - 优先规则/模板生成（可编程）：基于 `dom_summary/controls_tree/ax` 派生候选 selector/role/text；生成最小差异 Program 片段
  - 不足时调用 LLM：根据模板化提示，生成或修订 `locators/preconditions/program` 的候选，并解释原因与风险

## 3. 项目文件结构（aid/）

仅给出结构与职责，不落具体实现代码：

```
aid/
├─ README.md                      # 本文档
├─ repair.py                      # 总入口：修理单个技能（CLI + API）
├─ io.py                          # I/O 适配：加载/写回 skill JSON 与采集产物
├─ schemas/
│  └─ patch_schema.json           # PatchDocument 的 JSON Schema（可选）
├─ diagnostic_core.py             # 失败归因：Mismatch vs Damage + 根因线索
├─ diff_analyzer.py               # 旧/新采集对比：selector 存活、文本/role/属性差异
├─ locator_repair.py              # 定位器修复与排序（primary/selector_alt/by_role/by_text）
├─ preconditions_refiner.py       # 前置条件精修（exists/not_exists/visible/viewport/...）
├─ program_repair.py              # 程序最小修补（等待/滚动/后检/重试），保留签名
├─ repair_planner.py              # 组合策略：A→B→C；优先“最小变更”
├─ validation_runner.py           # 静态/干跑验证：结构/Schema/基本可达性
├─ patch_ops.py                   # PatchDocument 应用（add/remove/replace）
├─ prompts/
│  └─ program_fix.md              # 仅保留“程序修补”模板（其余共享模板复用 skill/prompt/）
└─ utils.py                       # 公用：选择器派生/类名稳健性/文本去噪/时间戳
```

输出路径与命名：
- 默认写入：`<new_run_dir>/skill/Skill_(选择器)_(控件ID)_repaired.json`
- 原 skill JSON 不覆写（可通过 CLI 指定 `--in-place` 覆写）。

下文详述模块职责与 I/O 约定。为避免与 skill 重复：
- 模板与接口边界
  - skill 提供：代码生成（codegen）、运行（run_skill）、共享 LLM 模板（locator_refine / preconditions_refine / naming）保存在 `skill/prompt/`；
  - aid 提供：修理编排与补丁应用；仅在 `aid/prompts/` 保留“program_fix.md”；其余 LLM 模板直接引用 `skill/prompt/`，不复制；
  - LLM 客户端统一在 `skill/llm_client.py`，aid 仅复用，不再单独提供。

- aid/diagnostic_core.py
  - 角色：失败归因（Mismatch vs Damage）与粗粒度定位（locator/timing/overlay/text）
  - in：
    - `skill`: 旧技能 JSON（待修理）
    - `old_run_dir`: 旧采集目录（可从 `skill.meta.source_dir` 取得）
    - `new_run_dir`: 新采集目录（当前网页）
    - `exec_trace?`: 失败日志（可选）
  - out：`DiagnosticReport`（JSON）

- aid/diff_analyzer.py
  - 角色：比对旧/新采集的 DOM/AX/控件树，输出差异线索
  - in：`old_run_dir`、`new_run_dir`、`skill.locators`
  - out：`DiffSignals`（JSON）：`{selector_alive, id/name/role/text changes, overlay_hits, candidate_attrs}`

- aid/locator_repair.py
  - 角色：生成与排序替代定位器（主 selector/selector_alt/by_role/by_text）
  - in：`skill.locators`、`new_run_dir` 的 `dom_summary.json/controls_tree.json/ax.json`、`DiffSignals`
  - out：`LocatorPatch[]`（按稳健性排序的候选）

- aid/program_repair.py
  - 角色：对 `program.code` 进行“最小修补”
  - in：`skill.program.code`、`skill.action`、修复后的 `locators`、`DiffSignals`（遮挡/可见性/滚动/网络空闲）
  - out：`ProgramPatch`（最小改动的源码差异或替换版）+ `SafetyReport`
  - 约束：禁止引入 import/IO/网络；仅使用 `env.*`；返回结构必须保持一致

- aid/preconditions_refiner.py
  - 角色：增删改 `preconditions` 的弱约束，避免过拟合/误杀
  - in：`skill.preconditions`、`DiffSignals`（遮挡命中/可见性/视口/设备）
  - out：`PreconditionsPatch`（JSON）

- aid/repair_planner.py
  - 角色：根据 DiagnosticReport 选择修理策略与顺序（A→B→C），聚合多候选并给出优先级
  - in：`DiagnosticReport`、各子模块候选
  - out：`RepairPlan`（JSON），用于驱动验证与应用

- aid/validation_runner.py
  - 角色：对补丁进行静态校验与（可选）干跑/真跑验证
  - in：`PatchedSkill`（内存对象或临时文件）、`run_sandbox` 配置
  - out：`EvalReport`（通过率、时延、稳定性、重试次数）

- aid/patch_ops.py
  - 角色：统一补丁结构与应用（JSON-Patch 风格）
  - in：`Skill JSON` + `PatchDocument`（详见下）
  - out：`PatchedSkill JSON` 与 `diff` 摘要

- aid/prompts/*.md
  - 角色：LLM 提示词模板（locator_refine / preconditions_refine / program_fix）
  - in：模板变量（见 skill/README.md 第 4 节）
  - out：LLM 输出（JSON 或 Python 代码）

- aid/repair.py（总入口）
  - 角色：组合以上模块，串联“加载→诊断→方案→应用→验证→输出”。
  - in：`skill.json`（失效）、`old_run_dir`（可从 skill.meta.source_dir 推断）、`new_run_dir`（当前采集）
  - out：`repaired_skill.json`（写入 `<new_run_dir>/skill/Skill_(selector)_(id)_repaired.json`）

## 4. 统一补丁结构（PatchDocument）

用于在不同修理策略间保持一致的表示与应用逻辑（便于审计与回滚）。

```json
{
  "patch_id": "<uuid>",
  "skill_id": "<d136>",
  "base_version": "<v0.1>",
  "ops": [
    {"op": "replace", "path": "/locators/selector", "value": "a[role='link'][aria-label='Images']"},
    {"op": "add", "path": "/locators/selector_alt/-", "value": "a.nav.images"},
    {"op": "add", "path": "/preconditions/not_exists/-", "value": ".modal,.backdrop"}
  ],
  "rationale": "classname rolled; add overlay guard",
  "created_at": "<ISO time>",
  "author": "aid",
  "eval": {"pass_rate": 1.0, "trials": 3, "avg_latency_ms": 5200}
}
```

- op：`add|remove|replace`（JSON Pointer 的 path）
- path：按 `skill/schema.json` 的字段树
- value：新值（若 `add` 到数组尾使用 `/-`）

## 5. 输入/输出摘要（总览）

- 共同输入（多数模块）
  - 失效技能：`skill/schema.json` 定义的 Skill JSON（修理目标）
  - 旧采集数据（与技能关联）：`controls_tree.json`、`dom_summary.json`、`ax.json`、`snippets/*`、`meta.json`
  - 新采集数据（当前网页）：同上结构
  - 失败上下文：异常、运行参数、（可选）执行轨迹

- 共同输出
  - DiagnosticReport：`{"root_cause": "mismatch|damage", "signals": {...}, "notes": "..."}`
  - RepairPlan：`{"steps": ["locator_patch", "program_patch", ...]}`
  - PatchDocument：见第 4 节
  - EvalReport：通过率/时延/重试次数等
  - PatchedSkill：应用补丁后的技能 JSON（仍应符合 `skill/schema.json`）

## 6. 引用关系（与仓内其他目录）

- 与 detect/
  - 读取：`controls_tree.json`、`dom_summary.json`、`ax.json`、`screenshot*`、`meta.json`
  - 目的：定位器修复、遮挡识别、文本与 role 佐证

- 与 skill/
  - 读取/写入：`skill/skill_library/<domain>/<id>.json`（技能条目）
  - 约束：校验遵循 `skill/schema.json`
  - 依赖：见 `skill/README.md` 的 LLM 模板与变量映射（aid/prompts 可直接使用）

- 与 learning/（如后续加入）
  - 写入：经验库（few-shot 案例）
  - 读取：提示优化后的 few-shot 样本（用于 LLM 提升稳定性）

## 7. LLM vs 非 LLM（逐步细化）

- 纯可编程（默认）：
  - I/O 处理、Schema 校验、字段保全与版本元数据更新；
  - 旧/新差异分析（selector 存活检测、id/name/role/text/属性差异）；
  - 定位器候选派生与排序（id/name/data-testid/role/稳定 class）；
  - 前置条件骨架精修（exists/not_exists/viewport 的保守调整）；
  - 程序“模板化”小修（仅等待/滚动/后检的微调，非逻辑重写）。
- 需 LLM（仅在不确定/复杂场景）：
  - 多候选定位器的风险权衡与说明（locator_refine.md）；
  - 前置条件是否引入 `visible/enabled/text_contains`（preconditions_refine.md）；
  - 程序代码的最小修补片段（program_fix.md，遵循禁止导入/网络/IO 的约束）；
  - 命名规范化（naming.md，可选）。

## 8. CLI（建议，暂不落代码）

- 失败归因：
  - `python -m aid.diagnostic --skill <skill.json> --run-dir <detect_run_dir> --trace <exec_trace.json> --out <dir>`
- 自动修理：
  - `python -m aid.repair --skill <skill.json> --run-dir <detect_run_dir> --out <dir> [--strategy auto|locators|program|preconditions]`
- 验证评估：
  - `python -m aid.validate --skill <patched_skill.json> --run-dir <detect_run_dir> --trials 3`

## 9. 安全与风险控制

- 程序补丁必须通过：
  - 静态安全检查（禁 import/IO/网络/子进程/动态执行）
  - 签名/Docstring 规范（见 `skill/README.md`）
- 定位器补丁需：
  - 优先稳定属性（id/name/role/aria-label/稳定 class）
  - 保留原 selector 作为回退（避免一次性破坏）
- 预条件补丁：
  - 仅在高确定性信号下加入 `visible/enabled/text_contains` 等弱约束
  - 避免写入站点私密信息（cookie/登录等）

---

“技能修理”是让技能生态保持“低腐化率”的关键环节。AID 子系统以补丁为中心，通过可编程+LLM 的混合策略，优先选择最小变更方案，配合验证与健康度统计，构成技能生命周期的闭环修复能力。
