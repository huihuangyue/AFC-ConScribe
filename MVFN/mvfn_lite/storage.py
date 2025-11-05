"""存储与索引。

作用：将 AFC 节点写入 sqlite3，并构建/保存 FAISS 索引（如可用）。
输入：AFC 节点与其嵌入。
输出：数据库文件与索引文件。
"""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import List
from .schema import AFCNode


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS afc_nodes (
                id TEXT PRIMARY KEY,
                page_id TEXT,
                label TEXT,
                action TEXT,
                bbox TEXT,
                main_text TEXT,
                evidence TEXT,
                confidence REAL,
                embedding TEXT,
                relations TEXT
            )
            """
        )


def insert_nodes(db_path: str, nodes: List[AFCNode]) -> None:
    with sqlite3.connect(db_path) as conn:
        for n in nodes:
            conn.execute(
                """
                INSERT OR REPLACE INTO afc_nodes
                (id, page_id, label, action, bbox, main_text, evidence, confidence, embedding, relations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    n.id,
                    n.page_id,
                    n.label,
                    n.action,
                    json.dumps(n.bbox.dict() if n.bbox else {}),
                    n.main_text,
                    json.dumps([e.dict() for e in n.evidence]),
                    n.confidence,
                    json.dumps(n.embedding or []),
                    json.dumps(n.relations),
                ),
            )


def build_faiss_index(embeddings: List[List[float]]):
    try:
        import faiss  # type: ignore
    except Exception:
        return None
    import numpy as np  # type: ignore
    xb = np.array(embeddings, dtype="float32")
    d = xb.shape[1]
    index = faiss.IndexFlatIP(d)
    index.add(xb)
    return index


def save_faiss_index(index, index_path: str) -> None:
    if index is None:
        return
    from pathlib import Path
    Path(index_path).parent.mkdir(parents=True, exist_ok=True)
    import faiss  # type: ignore
    faiss.write_index(index, index_path)

