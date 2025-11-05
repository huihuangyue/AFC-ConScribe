"""MVFN‑lite package.

提供从多视图线索（文本/结构/视觉/上下文）抽取网页可操作控件为 AFC 节点的最小骨架。

公开入口：参见 pipeline.run_page。
"""

__all__ = [
    "schema",
    "candidates",
    "evidence",
    "rules",
    "scoring",
    "embeddings",
    "relations",
    "dedupe",
    "storage",
    "llm_refine",
    "utils",
]

