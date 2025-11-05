"""语义向量。

作用：生成文本语义向量；优先使用 sentence-transformers，缺失则退化到确定性哈希向量。
输入：文本列表。
输出：向量列表（list[list[float]]）。
"""

from typing import List


def _hash_embed(text: str, dim: int = 64) -> List[float]:
    # 简单确定性哈希向量（占位实现，便于无依赖运行）
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h *= 16777619
        h &= 0xFFFFFFFF
    # 展开为 dim 维
    vec = [(float(((h >> (i % 16)) & 0xFF)) / 255.0) for i in range(dim)]
    return vec


def embed_texts(texts: List[str], model_name: str | None = None) -> List[List[float]]:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        model = SentenceTransformer(model_name or "all-MiniLM-L6-v2")
        embs = model.encode(texts, show_progress_bar=False)
        return [list(map(float, e)) for e in embs]
    except Exception:
        return [_hash_embed(t) for t in texts]

