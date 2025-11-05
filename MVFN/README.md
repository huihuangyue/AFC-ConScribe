# MVFN‑lite 设计与模块说明

本目录包含“Multi‑View Fusion Network（MVFN‑lite）”的最小可用项目骨架与说明。其目标是将网页上的可操作控件抽取为结构化的 AFC 节点，便于后续检索与自动化。

## 为什么要做
- 目标：让电脑读懂网页上的“可操作控件”（如提交、搜索、登录），用于自动化和语义检索。
- 价值：把杂乱的页面信息变成结构化“控件知识库”，后续能快速定位、搜索、复用。

## 项目流程（位置与产物）
- 采集：页面截图、DOM 简表、可访问性树（AX）。
- 理解（MVFN‑lite）：候选生成→证据提取→规则分类→打分融合→向量与关系→去重→AFC 节点。
- 存储/索引：写入 sqlite3 与 faiss 索引，供检索与运行时使用。

## 输出（AFC 节点）
- 标签（如 `Clickable_Submit`）
- 操作类型（Click/Input/...）
- 位置框（bbox）
- 主要文本
- 证据与打分
- 语义向量
- 上下文关系

## 模块与文件说明
- `mvfn_lite/schema.py`
  - 作用：定义 Pydantic 数据模型（Candidate/AFCNode/BBox/Evidence 等）。
  - 输入：原始候选属性与证据。
  - 输出：结构化对象，供后续模块消费。
  - 依赖：`pydantic`。

- `mvfn_lite/candidates.py`
  - 作用：从 AX/DOM 生成候选控件，过滤不可见/重叠。
  - 输入：AX 节点、DOM 节点、截图几何信息。
  - 输出：`Candidate` 列表。
  - 依赖：`mvfn_lite.schema`, `mvfn_lite.utils.dom`, `mvfn_lite.utils.vision`。

- `mvfn_lite/evidence.py`
  - 作用：提取文本/图标等证据；文本优先（AX/aria/innerText），缺再 OCR；简单图标词典。
  - 输入：候选、截图、AX/DOM 文本。
  - 输出：证据列表写回 `Candidate`。
  - 依赖：`mvfn_lite.utils.ocr`, `mvfn_lite.utils.icons`。

- `mvfn_lite/rules.py`
  - 作用：基于别名词典与正则将候选归一到标准标签与操作类型。
  - 输入：聚合证据（文本/图标/角色）。
  - 输出：`label`, `action`。
  - 依赖：内置别名表；可扩展自定义规则。

- `mvfn_lite/scoring.py`
  - 作用：多源证据打分融合，产出总体置信度与权重解释。
  - 输入：证据项及各通道权重。
  - 输出：`confidence`、通道分项分数。
  - 依赖：无硬依赖。

- `mvfn_lite/embeddings.py`
  - 作用：生成文本语义向量；优先使用 `sentence-transformers`，无则退化为确定性哈希向量。
  - 输入：主要文本/上下文文本。
  - 输出：嵌入向量（list[float]）。
  - 依赖：可选 `sentence-transformers`。

- `mvfn_lite/relations.py`
  - 作用：基于布局/表单归属/邻接建立上下文关系图。
  - 输入：候选与其布局/表单信息。
  - 输出：关系映射（如同一行/同表单/邻接）。
  - 依赖：可选 `networkx`（内部懒加载）。

- `mvfn_lite/dedupe.py`
  - 作用：根据位置/文本/标签去重与合并重复候选。
  - 输入：候选或初步 AFC 节点。
  - 输出：去重后的节点列表。

- `mvfn_lite/storage.py`
  - 作用：写入 sqlite3 与构建 FAISS 索引（如可用）。
  - 输入：AFC 节点与嵌入。
  - 输出：数据库文件与索引文件。
  - 依赖：`sqlite3`，可选 `faiss-cpu`。

- `mvfn_lite/llm_refine.py`（可选）
  - 作用：当置信度低时，封装 LLM 做轻量纠偏（需密钥）。
  - 输入：候选摘要与当前判定。
  - 输出：校正后的标签/操作或置信度调整。
  - 依赖：`python-dotenv`, `tenacity`, 可选 `openai` 或 `httpx`。

- `mvfn_lite/pipeline.py`
  - 作用：端到端编排：候选→证据→规则→打分→向量→关系→去重→入库/建索引。
  - 输入：`screenshot_path`, `dom_path`, `ax_path`, 目标 `db_path` 与 `index_path`。
  - 输出：AFC 节点列表与持久化产物。

- `mvfn_lite/utils/ocr.py` / `dom.py` / `vision.py` / `icons.py` / `config.py` / `indexing.py`
  - 作用：与外部世界交互的适配层与配置常量。

## 简要数据流
截图/DOM/AX → 候选生成 → 证据提取 → 规则分类 → 打分融合 → 嵌入与关系 → 去重 → sqlite3 + FAISS 索引。

## 快速上手（伪代码）
```python
from mvfn_lite.pipeline import run_page
nodes = run_page(
    page_id='demo',
    screenshot_path='workspace/sample.png',
    dom_path='workspace/sample.dom.json',
    ax_path='workspace/sample.ax.json',
    db_path='workspace/afc.db',
    index_path='workspace/afc.index',
    enable_llm=False,
)
```

> 说明：本骨架侧重结构与接口定义，算法可逐步替换/迭代。

