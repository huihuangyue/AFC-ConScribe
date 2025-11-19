from __future__ import annotations

"""
planner.candidate_selector

候选技能预筛 + 轻量 BM25/TF‑IDF 打分（纯本地，不调 LLM）。

输入：
- 自然语言任务 task；
- page_summary（env_summary.build_page_summary 输出的结构）；
- skills_index（skill_index.build_skill_index 输出的结构或其 JSON）；
- 当前 URL（可选，若为空则用 page_summary.meta.url）。

流程（尽量低 token 成本）：
1. 硬过滤：
   - 仅保留 domain 与当前页面一致或为空的技能；
   - 若 preconditions.url_matches 与当前 URL 有命中，则视为“URL 强相关”。
2. 构建查询：
   - query_text = task + 页面标题 + main_block.short_name + main_block.short_desc
   - 以与 skill_index 相同的规则分词（中文+英文）。
3. 对每个候选技能：
   - 使用 skill_index.bm25.doc_freq/total_docs + 自身 term 频率，计算简化 BM25 分数；
   - 若 selector 与 main_block.selector 明显相关，则加一个小偏置；
   - 若 URL 正则命中当前 URL，也加一点偏置。
4. 返回按 score 排序的 TopK 列表，并附上简单理由（matched_terms 等）。

CLI：
  python -m planner.candidate_selector --run-dir <dir> --task "..." --top-k 5
"""

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


# 任务/技能意图关键词到“意图标签”的粗略映射（当前主要服务常见出行场景）
INTENT_KW_TO_TAG = {
    "酒店": "hotel",
    "机票": "flight",
    "航班": "flight",
}


def _tokenize(text: str) -> List[str]:
    """与 planner.skill_index 中 _build_terms 一致的分词规则（中英混合）。"""
    if not text:
        return []
    tokens = re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", str(text).lower())
    return [t for t in tokens if t]


def _skill_terms(skill: Dict[str, Any]) -> List[str]:
    """从技能卡片中构造 term 列表，逻辑与 skill_index._build_terms 保持一致。"""
    parts: List[str] = []
    parts.append(skill.get("id") or "")
    parts.append(skill.get("name") or "")
    parts.append(skill.get("description") or "")
    parts.append(skill.get("domain") or "")
    for s in skill.get("selectors") or []:
        parts.append(s)
    for u in skill.get("url_matches") or []:
        parts.append(u)
    for a in skill.get("args") or []:
        parts.append(a.get("name", ""))
        parts.append(a.get("description", ""))
    full = " ".join(p for p in parts if p)
    return _tokenize(full)


def _build_doc_stats(skills: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], Dict[str, int], float]:
    """构建每个技能的 term 列表与长度，以及平均文档长度。"""
    doc_terms: Dict[str, List[str]] = {}
    doc_len: Dict[str, int] = {}
    total_len = 0
    for s in skills:
        sid = str(s.get("id") or "")
        terms = _skill_terms(s)
        doc_terms[sid] = terms
        doc_len[sid] = len(terms)
        total_len += len(terms)
    avgdl = float(total_len) / max(1, len(skills))
    return doc_terms, doc_len, avgdl


def _idf(term: str, bm25_doc: Dict[str, Any]) -> float:
    """根据预构建的 doc_freq/total_docs 计算 IDF。"""
    df = int((bm25_doc.get("doc_freq") or {}).get(term, 0))
    total = int(bm25_doc.get("total_docs") or 0) or 1
    # 标准 BM25 型 IDF，加入 0.5 平滑
    return math.log((total - df + 0.5) / (df + 0.5) + 1.0)


def _bm25_score(
    query_terms: List[str],
    doc_terms: List[str],
    doc_len: int,
    bm25_doc: Dict[str, Any],
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> Tuple[float, List[str]]:
    """对单个技能计算简化 BM25 分数，并返回匹配到的 query term 列表。"""
    if not query_terms or not doc_terms:
        return 0.0, []
    tf: Dict[str, int] = {}
    for t in doc_terms:
        tf[t] = tf.get(t, 0) + 1
    avgdl = float(bm25_doc.get("avgdl") or 1.0)
    score = 0.0
    matched: List[str] = []
    for t in query_terms:
        if t not in tf:
            continue
        idf_t = _idf(t, bm25_doc)
        f = tf[t]
        denom = f + k1 * (1.0 - b + b * doc_len / max(1.0, avgdl))
        s = idf_t * (f * (k1 + 1.0) / max(1e-9, denom))
        score += s
        matched.append(t)
    return score, matched


def _task_intents(task: str) -> List[str]:
    """从自然语言任务中抽取粗粒度意图标签（hotel/flight 等）。"""
    tags: List[str] = []
    text = task or ""
    for kw, tag in INTENT_KW_TO_TAG.items():
        if kw in text and tag not in tags:
            tags.append(tag)
    return tags


def _skill_intent_tags(skill: Dict[str, Any]) -> List[str]:
    """基于技能 JSON（locators.by_text + program.code）推断其可能覆盖的意图标签。

    为保持成本可控，仅使用少量关键词匹配，不做分词或复杂 NLU。
    """
    tags: List[str] = []
    path = skill.get("skill_path")
    doc: Dict[str, Any] = {}
    try:
        if path and os.path.exists(path):
            doc = _read_json(path)  # type: ignore[assignment]
    except Exception:
        doc = {}

    loc = doc.get("locators") or {}
    texts: List[str] = []
    # 优先使用 by_text（通常是控件可见文案，噪声较小）
    bt = loc.get("by_text") or []
    if isinstance(bt, list):
        texts.extend(str(x) for x in bt if x)
    # 补充 program.code 的文档字符串（截断以降低开销）
    prog = doc.get("program") or {}
    code = prog.get("code") or ""
    if isinstance(code, str) and code:
        texts.append(code[:4000])

    blob = " ".join(texts)
    for kw, tag in INTENT_KW_TO_TAG.items():
        if kw in blob and tag not in tags:
            tags.append(tag)
    return tags


def _domain_ok(skill_domain: str, page_domain: str) -> bool:
    if not skill_domain:
        return True  # 视为“通用技能”，不过滤
    if not page_domain:
        return True
    sd = skill_domain.lower()
    pd = page_domain.lower()
    return sd == pd or sd.endswith("." + pd) or pd.endswith("." + sd)


def _url_match_ok(url_matches: List[str], url: str) -> bool:
    if not url_matches or not url:
        return True
    for pat in url_matches:
        try:
            if re.search(pat, url):
                return True
        except re.error:
            # 非法正则当作子串匹配兜底
            if pat in url:
                return True
    return False


def _main_block_for_summary(page_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    blocks = page_summary.get("blocks") or []
    mbid = page_summary.get("main_block_id")
    if not isinstance(blocks, list) or not mbid:
        return None
    for b in blocks:
        if isinstance(b, dict) and str(b.get("id")) == str(mbid):
            return b
    return None


def _main_related(selectors: List[str], main_selector: Optional[str]) -> bool:
    if not main_selector:
        return False
    ms = main_selector.lower().strip()
    if not ms:
        return False
    for s in selectors or []:
        ss = s.lower().strip()
        if not ss:
            continue
        if ss in ms or ms in ss:
            return True
    return False


@dataclass
class CandidateSkill:
    id: str
    name: str
    score: float
    reason: str
    selectors: List[str]
    raw: Dict[str, Any]


def select_candidates(
    task: str,
    page_summary: Dict[str, Any],
    skills_index: Dict[str, Any],
    *,
    current_url: Optional[str] = None,
    top_k: int = 5,
) -> List[CandidateSkill]:
    """基于页面摘要与技能索引，返回 TopK 候选技能（纯本地 BM25 + 规则）。"""
    meta = page_summary.get("meta") or {}
    page_domain = str(meta.get("domain") or "").strip()
    page_url = current_url or str(meta.get("url") or "").strip()
    main_block = _main_block_for_summary(page_summary) or {}
    main_sel = (main_block.get("selector") or "").strip()
    main_name = str(main_block.get("short_name") or "")
    main_desc = str(main_block.get("short_desc") or "")
    title = str(meta.get("title") or "")

    skills: List[Dict[str, Any]] = [s for s in (skills_index.get("skills") or []) if isinstance(s, dict)]
    if not skills:
        return []

    # 任务意图标签（用于简单的“酒店/机票”等领域偏置）
    task_intents = _task_intents(task)

    # 1) 硬过滤：按 domain 与 url_matches 粗筛一轮
    filtered: List[Dict[str, Any]] = []
    url_good: List[bool] = []
    for s in skills:
        sd = str(s.get("domain") or "")
        if not _domain_ok(sd, page_domain):
            continue
        ums = [str(x) for x in (s.get("url_matches") or []) if x]
        um_ok = _url_match_ok(ums, page_url)
        filtered.append(s)
        url_good.append(um_ok)

    if not filtered:
        # 若全部被 domain 过滤掉，则回退到原始 skills（避免空结果）
        filtered = skills
        url_good = [True] * len(skills)

    # 2) 构建查询 term 列表
    query_text = " ".join(
        part
        for part in [task, title, main_name, main_desc]
        if part
    )
    query_terms = _tokenize(query_text)
    query_terms = query_terms[:64]  # 简单截断，避免无意义长 query

    # 3) 预备文档统计 + BM25 语料
    bm25_doc = skills_index.get("bm25") or {}
    doc_terms, doc_len, avgdl = _build_doc_stats(filtered)
    bm25_doc = dict(bm25_doc)  # 拷贝一份，补充 avgdl
    bm25_doc["avgdl"] = avgdl

    candidates: List[CandidateSkill] = []

    for s, um_ok in zip(filtered, url_good):
        sid = str(s.get("id") or "")
        name = str(s.get("name") or sid)
        selectors = [str(x) for x in (s.get("selectors") or []) if x]
        d_terms = doc_terms.get(sid, [])
        d_len = doc_len.get(sid, len(d_terms))

        base_score, matched = _bm25_score(query_terms, d_terms, d_len, bm25_doc)

        # 适度偏置：主控件块相关 + URL 命中
        bonus = 0.0
        if _main_related(selectors, main_sel):
            bonus += 0.5
        if um_ok:
            bonus += 0.2

        # 任务意图与技能意图的简单对齐（hotel/flight 等）
        intent_bonus = 0.0
        intent_tags: List[str] = []
        if task_intents:
            skill_tags = _skill_intent_tags(s)
            overlap = [t for t in task_intents if t in skill_tags]
            if overlap:
                intent_tags = overlap
                intent_bonus = 0.6 * len(overlap)
                if intent_bonus > 0.8:
                    intent_bonus = 0.8

        score = base_score + bonus + intent_bonus

        # 构造简单理由
        parts: List[str] = []
        parts.append(f"bm25={base_score:.3f}")
        if bonus > 0:
            parts.append(f"bonus={bonus:.3f}")
        if matched:
            parts.append(f"matched={','.join(sorted(set(matched))[:5])}")
        if _main_related(selectors, main_sel):
            parts.append("main_block_related")
        if um_ok:
            parts.append("url_match")
        if intent_bonus > 0 and intent_tags:
            parts.append(f"intent={','.join(sorted(set(intent_tags)))}")

        reason = "; ".join(parts)
        candidates.append(
            CandidateSkill(
                id=sid,
                name=name,
                score=score,
                reason=reason,
                selectors=selectors,
                raw=s,
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[: int(max(1, top_k))]


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Select candidate skills by BM25 + rules (no LLM)")
    ap.add_argument("--run-dir", required=True, help="Detect run dir (must contain page_summary.json and skill/skills_index.json)")
    ap.add_argument("--task", required=True, help="Natural language task description")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--skills-index", type=str, default=None, help="Optional explicit path to skills_index.json")
    ap.add_argument("--url", type=str, default=None, help="Override current URL (default: meta.url)")
    args = ap.parse_args()

    run_dir = args.run_dir
    ps_path = os.path.join(run_dir, "page_summary.json")
    if not os.path.exists(ps_path):
        raise SystemExit(f"page_summary.json not found under {run_dir}, please run planner.env_summary first")
    page_summary = _read_json(ps_path)

    if args.skills_index:
        si_path = args.skills_index
    else:
        si_path = os.path.join(run_dir, "skill", "skills_index.json")
    if not os.path.exists(si_path):
        raise SystemExit(f"skills_index.json not found under {si_path}, please run planner.skill_index first")
    skills_index = _read_json(si_path)

    cands = select_candidates(
        task=args.task,
        page_summary=page_summary,
        skills_index=skills_index,
        current_url=args.url,
        top_k=args.top_k,
    )
    for c in cands:
        # 简单 TSV：id, score, first_selector, reason
        first_sel = c.selectors[0] if c.selectors else ""
        print(f"{c.id}\t{c.score:.3f}\t{first_sel}\t{c.reason}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
