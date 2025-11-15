#!/usr/bin/env python3
"""
生成候选指标与综合得分：
  - 读取 AFC/candidates.json 与 AFC/evidence_text.json（可选 dom_summary.json）
  - 计算总体统计与直方概览
  - 为每个候选产生 final_confidence = 0.6*candidate.score +*text_quality + 0.1*(1-occlusion)
  - 输出：
      * AFC/metrics.json
      * AFC/candidates_scored.json
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List

from .utils_io import read_json, write_json, ensure_dir


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def main() -> int:
    p = argparse.ArgumentParser(description="MVFN-lite | 生成候选指标与综合得分")
    p.add_argument("--dir", required=True, help="采集目录 data/<domain>/<ts>")
    args = p.parse_args()

    d = os.path.abspath(args.dir)
    afc = ensure_dir(os.path.join(d, "AFC"))
    cand_doc = read_json(os.path.join(afc, "candidates.json"))
    evid_doc = read_json(os.path.join(afc, "evidence_text.json"))
    dom_doc = read_json(os.path.join(d, "dom_summary.json"))

    cands: List[Dict[str, Any]] = cand_doc.get("candidates") or []
    extras = evid_doc.get("extras") or []
    extras_map = {e.get("id"): e for e in extras}

    dom_map: Dict[str, Dict[str, Any]] = {}
    for e in dom_doc.get("elements", []) or []:
        try:
            idx = int(e.get("index"))
            dom_map[f"d{idx}"] = e
        except Exception:
            continue

    scored: List[Dict[str, Any]] = []
    confs: List[float] = []
    by_source: Dict[str, int] = {"tree": 0, "dom": 0, "other": 0}
    for c in cands:
        cid = c.get("id")
        text_quality = float((extras_map.get(cid) or {}).get("text_quality") or 0.0)
        c_score = float(c.get("score") or 0.0)
        occ = c.get("occlusion_ratio")
        if occ is None:
            occ = (dom_map.get(cid) or {}).get("occlusion_ratio")
        occ = float(occ) if isinstance(occ, (int, float)) else 0.0
        # 综合置信度：候选内在分(0.6) + 文本质量(0.3) + 低遮挡奖励(0.1)
        final_conf = clamp01(0.6 * c_score + 0.3 * text_quality + 0.1 * (1.0 - occ))
        cc = dict(c)
        cc["text_quality"] = text_quality
        cc["final_confidence"] = final_conf
        scored.append(cc)
        confs.append(final_conf)
        src = (c.get("source") or "other").lower()
        if src not in by_source:
            by_source[src] = 0
        by_source[src] += 1

    # 统计直方
    buckets = {"[0,0.2)": 0, "[0.2,0.4)": 0, "[0.4,0.6)": 0, "[0.6,0.8)": 0, "[0.8,1]": 0}
    for v in confs:
        if v < 0.2:
            buckets["[0,0.2)"] += 1
        elif v < 0.4:
            buckets["[0.2,0.4)"] += 1
        elif v < 0.6:
            buckets["[0.4,0.6)"] += 1
        elif v < 0.8:
            buckets["[0.6,0.8)"] += 1
        else:
            buckets["[0.8,1]"] += 1

    # 读取候选阶段的筛选统计
    cand_stats = cand_doc.get("stats") or {}
    cand_rates = cand_doc.get("rates") or {}

    metrics = {
        "dir": d,
        "total": len(cands),
        "by_source": by_source,
        "hist_final_confidence": buckets,
        "mean_confidence": (sum(confs) / len(confs)) if confs else 0.0,
        "filtering": {
            **({k: v for k, v in cand_stats.items()}),
            **{f"rate.{k}": v for k, v in cand_rates.items()},
        },
    }

    write_json(os.path.join(afc, "metrics.json"), metrics)
    write_json(os.path.join(afc, "candidates_scored.json"), {"dir": d, "count": len(scored), "candidates": scored})

    print("[OK] metrics:", os.path.join(afc, "metrics.json"))
    print("[OK] candidates_scored:", os.path.join(afc, "candidates_scored.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
