"""端到端编排（MVFN‑lite）。

作用：候选→证据→规则→打分→向量→关系→去重→落库与索引。
输入：screenshot_path, dom_path, ax_path, db_path, index_path。
输出：AFCNode 列表与持久化产物。
"""

from __future__ import annotations
from typing import List, Dict, Any
from .schema import Candidate, AFCNode
from . import candidates as C
from . import evidence as E
from . import rules as R
from . import scoring as S
from . import embeddings as EMB
from . import relations as REL
from . import dedupe as D
from . import storage as ST


def _load_json(path: str) -> Any:
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_page(
    page_id: str,
    screenshot_path: str,
    dom_path: str,
    ax_path: str,
    db_path: str,
    index_path: str,
    enable_llm: bool = False,
) -> List[AFCNode]:
    ax_nodes: List[Dict[str, Any]] = _load_json(ax_path)
    dom_nodes: List[Dict[str, Any]] = _load_json(dom_path)

    # 1) 候选生成
    cands: List[Candidate] = C.generate_candidates(ax_nodes, dom_nodes, viewport=None)

    # 2) 证据提取（文本→OCR→图标）
    E.attach_text_evidence(cands)
    E.attach_ocr_evidence(cands, screenshot_path)
    E.attach_icon_evidence(cands)

    # 3) 规则分类与打分
    afc_nodes: List[AFCNode] = []
    for c in cands:
        label, action = R.classify_label_and_action(c)
        conf, _channels = S.fuse_scores(c)
        main_text = next((e.value for e in c.evidence if e.value), "")
        if not c.bbox:
            # 跳过异常候选
            continue
        afc_nodes.append(
            AFCNode(
                id=c.id.replace("cand_", "afc_"),
                label=label,
                action=action,
                bbox=c.bbox,
                main_text=main_text,
                evidence=c.evidence,
                confidence=conf,
                page_id=page_id,
            )
        )

    # 4) 向量与关系
    embs = EMB.embed_texts([n.main_text or n.label for n in afc_nodes])
    for n, v in zip(afc_nodes, embs):
        n.embedding = v
    rel = REL.build_relations(cands)
    for n in afc_nodes:
        n.relations = {"same_line": rel.get(n.id.replace("afc_", "cand_"), [])}

    # 5) 去重
    # 这里直接对候选去重已在前置完成，如需对 AFC 再去重可扩展

    # 6) 落库与索引
    ST.init_db(db_path)
    ST.insert_nodes(db_path, afc_nodes)
    index = ST.build_faiss_index([n.embedding or [] for n in afc_nodes])
    ST.save_faiss_index(index, index_path)

    return afc_nodes

