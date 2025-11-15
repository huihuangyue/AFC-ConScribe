"""
mvfn_lite.types
最小数据结构定义：Candidate 与文本证据条目。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Candidate:
    id: str
    role: Optional[str]
    bbox: List[int]
    page_bbox: Optional[List[int]]
    visible: bool
    raw_texts: List[str]
    dom_ref: Optional[int]
    ax_ref: Optional[str]
    # 新增：来源/选择器/遮挡/启发式分
    source: str | None = None  # "tree" | "dom"
    selector: Optional[str] = None
    occlusion_ratio: Optional[float] = None
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class TextEvidenceItem:
    id: str
    main_text: str
    pieces: List[Dict[str, Any]]  # {source, value, score}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
