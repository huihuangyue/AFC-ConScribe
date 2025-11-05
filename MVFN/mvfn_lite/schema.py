"""数据模型定义（Pydantic）。

作用：统一候选与 AFC 节点的数据结构，便于跨模块传递与落库。
输入：原始候选属性、证据、打分、向量与关系。
输出：Candidate / AFCNode 等结构化对象。
依赖：pydantic
"""

from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class Evidence(BaseModel):
    source: str = Field(description="来源：ax/aria/innerText/ocr/icon/role/context 等")
    value: str = Field(description="证据文本或标识")
    score: float = 0.0
    meta: Dict[str, Any] = Field(default_factory=dict)


class Candidate(BaseModel):
    id: str
    role: Optional[str] = None
    bbox: Optional[BBox] = None
    texts: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    visibility: Optional[bool] = True
    viewport: Optional[BBox] = None


class AFCNode(BaseModel):
    id: str
    label: str
    action: str
    bbox: BBox
    main_text: str = ""
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    embedding: Optional[List[float]] = None
    relations: Dict[str, List[str]] = Field(default_factory=dict)
    page_id: Optional[str] = None


