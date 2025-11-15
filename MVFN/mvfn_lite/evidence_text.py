"""
mvfn_lite.evidence_text
基于 Candidate 与 DOM 摘要，抽取主文本与文本证据。

输入：
  - <dir>/AFC/candidates.json
  - <dir>/dom_summary.json

输出：
  - <dir>/AFC/evidence_text.json
"""

from __future__ import annotations

from typing import Any, Dict, List
import os

from .types import TextEvidenceItem
from .utils_io import read_json, write_json


def _score(source: str) -> float:
    # 简单优先级分：aria > labels > name > title > text
    order = {
        "aria-label": 1.0,
        "aria-labelledby": 0.95,
        "aria-placeholder": 0.9,
        "labels": 0.9,
        "name": 0.85,
        "title": 0.75,
        "text": 0.6,
    }
    return float(order.get(source, 0.5))


def _best_text(pieces: List[Dict[str, Any]]) -> str:
    # 选分最高且非空者；若相同分，取最长
    good = [p for p in pieces if isinstance(p.get("value"), str) and p.get("value").strip()]
    if not good:
        return ""
    good.sort(key=lambda p: (_score(str(p.get("source"))), len(str(p.get("value")))), reverse=True)
    return str(good[0].get("value") or "").strip()


def _quality(pieces: List[Dict[str, Any]]) -> float:
    if not pieces:
        return 0.0
    return max((_score(str(p.get("source"))) for p in pieces), default=0.0)


def build_text_evidence(dir_path: str) -> str:
    cand_path = os.path.join(dir_path, "AFC", "candidates.json")
    dom_path = os.path.join(dir_path, "dom_summary.json")
    out_path = os.path.join(dir_path, "AFC", "evidence_text.json")

    cand_doc = read_json(cand_path)
    dom_doc = read_json(dom_path)
    cand_list = cand_doc.get("candidates") or []
    els = dom_doc.get("elements") or []

    by_id: Dict[str, Dict[str, Any]] = {}
    for e in els:
        try:
            idx = int(e.get("index"))
        except Exception:
            continue
        by_id[f"d{idx}"] = e

    items: List[TextEvidenceItem] = []
    # 为了在输出中保留候选的来源与启发式分，构建一个索引（若不存在则为空）
    cand_map = {c.get("id"): c for c in (cand_list or [])}
    for c in cand_list:
        nid = c.get("id")
        src = by_id.get(nid) or {}
        aria = src.get("aria") or {}
        pieces: List[Dict[str, Any]] = []
        for k in ("aria-label", "aria-labelledby", "aria-placeholder"):
            v = aria.get(k)
            if isinstance(v, str) and v.strip():
                pieces.append({"source": k, "value": v.strip(), "score": _score(k)})
        for k in ("labels",):
            labels = src.get(k) or []
            if isinstance(labels, list) and labels:
                val = ", ".join([x for x in labels if isinstance(x, str) and x.strip()])
                if val:
                    pieces.append({"source": k, "value": val, "score": _score(k)})
        for k in ("name", "title", "text"):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                pieces.append({"source": k, "value": v.strip(), "score": _score(k)})
        main_text = _best_text(pieces)
        items.append(TextEvidenceItem(id=nid, main_text=main_text, pieces=pieces))

    payload = {
        "version": "v0.1",
        "dir": dir_path,
        "count": len(items),
        "items": [it.to_dict() for it in items],
    }
    # 追加每个 id 的文本质量与候选来源/启发式分（供后续打分使用）
    extras: List[Dict[str, Any]] = []
    for it in items:
        q = _quality([p for p in it.pieces])
        c = cand_map.get(it.id) or {}
        extras.append({
            "id": it.id,
            "text_quality": q,
            "candidate_source": c.get("source"),
            "candidate_score": c.get("score"),
        })
    payload["extras"] = extras

    write_json(
        out_path,
        payload,
    )
    return out_path


__all__ = ["build_text_evidence"]
