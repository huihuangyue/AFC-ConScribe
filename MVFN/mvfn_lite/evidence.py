"""证据提取。

作用：为候选控件提取文本与图标证据；文本优先（AX/aria/innerText），缺失则 OCR；图标用小词表。
输入：Candidate 列表、截图路径及可选 innerText/aria 文本。
输出：写回 Candidate.evidence。
依赖：utils.ocr, utils.icons
"""

from typing import List, Dict, Any
from .schema import Candidate, Evidence


def attach_text_evidence(candidates: List[Candidate], inner_text_map: Dict[str, str] | None = None) -> None:
    for c in candidates:
        # 1) 直接已有的 texts（AX/aria/name/value）
        for t in c.texts:
            c.evidence.append(Evidence(source="ax", value=str(t), score=1.0))
        # 2) innerText 兜底
        if inner_text_map and c.id in inner_text_map:
            c.evidence.append(Evidence(source="innerText", value=inner_text_map[c.id], score=0.8))


def attach_ocr_evidence(candidates: List[Candidate], screenshot_path: str) -> None:
    try:
        from .utils.ocr import ocr_text
    except Exception:
        return
    for c in candidates:
        if any(e.source in {"ax", "innerText", "aria"} for e in c.evidence):
            continue
        text = ocr_text(screenshot_path, bbox=c.bbox)
        if text:
            c.evidence.append(Evidence(source="ocr", value=text, score=0.5))


def attach_icon_evidence(candidates: List[Candidate]) -> None:
    from .utils.icons import guess_icon_semantics
    for c in candidates:
        label = guess_icon_semantics(c)
        if label:
            c.evidence.append(Evidence(source="icon", value=label, score=0.6))

