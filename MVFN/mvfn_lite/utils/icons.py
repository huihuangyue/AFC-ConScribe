"""图标语义小词表（占位）。

根据候选附近的提示或已知图标特征（如放大镜）提供弱语义线索。
"""

from .schema import Candidate


def guess_icon_semantics(c: Candidate) -> str | None:
    # 占位：如果已有文本提及“搜索/submit”等则返回对应标签关键字
    blob = " ".join([t for t in c.texts])
    if any(k in blob for k in ["搜索", "search", "🔍"]):
        return "search"
    if any(k in blob for k in ["提交", "submit", "go"]):
        return "submit"
    return None

