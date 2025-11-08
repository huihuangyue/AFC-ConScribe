# MVFN‑lite 代码结构说明（设计文档，仅文档不含代码）

本文件描述 mvfn_lite 目录下“拟定的文件结构”，逐一给出：作用、输入、输出、主要依赖与被谁调用。当前阶段仅为设计文档，尚未创建任何源代码文件。

## 总入口与类型

- pipeline.py
  - 作用：主流水线编排，将各阶段按顺序/条件连接，形成 `run(inputs) -> afc_nodes` 主流程。
  - 输入：Screenshot、DOM 简表、AX 树、（可选）OCR 结果与配置；统一封装为 `PipelineInputs`。
  - 输出：AFC 节点列表（含标签/动作/bbox/文本/证据/置信度/向量/关系）。
  - 依赖：candidate_generation、evidence/*、rules/classifier、scoring/fusion、refine/llm_refiner?、embedding/encoder、relations/graph_builder、export/dedup+sink、config。

- types.py
  - 作用：集中定义 `Candidate`、`Evidence`、`AFCNode`、`PipelineInputs/Outputs` 等数据结构与协议。
  - 输入：无（类型定义被动被引用）。
  - 输出：类型与协议，供所有模块 import 使用。
  - 依赖：无；被全局各模块引用。

- config.py（可选实现）
  - 作用：读取与校验配置项/特性开关（OCR/LLM/Embedding/权重/导出等）。
  - 输入：环境变量、配置文件（YAML/JSON）。
  - 输出：`Config` 对象（含阈值、权重、服务端点等）。
  - 依赖：无；被 pipeline 与各子模块读取。

- logging.py（可选实现）
  - 作用：统一日志/事件与阶段产物快照（便于复现与调参）。
  - 输入：阶段输出/指标。
  - 输出：结构化日志与可视化所需的元数据。
  - 依赖：无；被各模块调用。

## 候选生成

- candidate_generation.py
  - 作用：从 DOM/AX 生成可操作控件候选，完成可见性/遮挡/重叠过滤与去噪。
  - 输入：DOM 简表、AX 树、（可选）可见性快照。
  - 输出：`Candidate[]`（含 id/role/bbox/visible/raw_texts/dom_ref/ax_ref）。
  - 依赖：types、config；被 pipeline 首先调用。

## 证据提取（evidence/）

- evidence/text_extractor.py
  - 作用：优先从 AX/aria/innerText/placeholder/label-for 等抽取文本并清洗，形成主要文本与文本证据。
  - 输入：`Candidate[]` + AX/DOM 引用。
  - 输出：为候选附加 `main_text` 与 `evidence(type=text, source=AX/DOM, score=…)`。
  - 依赖：types、config；被 pipeline 在候选生成之后调用。

- evidence/ocr_adapter.py（可选）
  - 作用：在文本不足时对截图按候选 bbox 进行 OCR，补充文本证据并缓存。
  - 输入：Screenshot，候选 bbox 区域，OCR 配置。
  - 输出：文本框与置信分，附加到候选 `evidence(type=text, source=OCR)`。
  - 依赖：外部 OCR（本地或服务化）、types、config；被 pipeline 条件调用。

- evidence/icon_semantics.py
  - 作用：识别图标类线索（class/SVG/aria）并映射语义关键词（如 search/close）。
  - 输入：候选的图标相关属性或邻近小图元。
  - 输出：`evidence(type=icon, value=semantic_keyword, score=…)`。
  - 依赖：内置小词表（JSON），types；被 pipeline 调用。

## 规则分类（rules/）

- rules/classifier.py
  - 作用：基于别名词典与正则，将候选归一到标准 `label` 与 `action`。
  - 输入：携带文本/图标/角色/上下文等证据的候选。
  - 输出：加入 `label`、`action` 字段和分类命中明细。
  - 依赖：`rules/alias_lexicon.json`、`rules/regex_rules.yaml`、types、config；被 pipeline 调用。

- rules/alias_lexicon.json（数据文件）
  - 作用：同义词/别名映射（提交/发送/确认 → Submit；搜索/查找/🔍 → Search）。
  - 输入：无（静态词表）。
  - 输出：被 classifier 查询使用。
  - 依赖：无；被 classifier 读取。

- rules/regex_rules.yaml（数据文件）
  - 作用：多语言/变体正则（含优先级与标签/动作映射）。
  - 输入：无（静态规则）。
  - 输出：被 classifier 查询与匹配使用。
  - 依赖：无；被 classifier 读取。

## 打分融合（scoring/）

- scoring/fusion.py
  - 作用：将文本/角色/上下文/图标等局部分值融合为整体置信度，输出可解释明细。
  - 输入：已分类的候选及其证据分值、融合权重。
  - 输出：更新后的 `confidence` 与贡献度明细。
  - 依赖：types、config；被 pipeline 调用。

## 可选微调（refine/）

- refine/llm_refiner.py（可选）
  - 作用：对低置信度样本调用外部 LLM 根据证据摘要微调标签/动作。
  - 输入：证据摘要、上下文片段与配置（阈值/端点/密钥）。
  - 输出：调整后的标签/动作/置信度或解释理由。
  - 依赖：外部 LLM 服务、types、config；被 pipeline 条件调用。

## 向量与关系（embedding/，relations/）

- embedding/encoder.py
  - 作用：将控件语义编码为向量，用于检索与匹配。
  - 输入：控件 `label`、`main_text`、上下文摘要及模型配置。
  - 输出：`embedding: float[]`。
  - 依赖：本地或服务化向量模型、types、config；被 pipeline 调用。

- relations/graph_builder.py
  - 作用：构建控件间的表单/同行/邻接等关系。
  - 输入：控件集合与其布局/分组信息。
  - 输出：为控件附加 `relations[]`。
  - 依赖：types；被 pipeline 调用。

## 导出与去重（export/）

- export/dedup.py
  - 作用：基于文本相似/空间 IOU/动作类型进行去重合并，保留证据来源。
  - 输入：控件集合。
  - 输出：去重后的控件集合。
  - 依赖：types；被 pipeline 调用并在 sink 前执行。

- export/sink.py
  - 作用：将最终 AFC 节点导出至下游（文件/DB/索引）。
  - 输入：最终控件集合与导出配置。
  - 输出：持久化落地或索引写入结果。
  - 依赖：types、config；被 pipeline 最后调用。

## 阶段 I/O 总览（简表）
- 输入（整体）：Screenshot、DOM 简表、AX 树、（可选）OCR；规则与配置。
- 输出（整体）：AFC 节点列表（标签/动作/bbox/文本/证据/分值/向量/关系、可解释明细）。

> 说明：上述文件为“拟定结构”，为后续实现提供明确边界。当前阶段不创建任何 `.py` 或数据文件，避免越界实现。

