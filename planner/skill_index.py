from __future__ import annotations

"""
planner.skill_index

扫描技能目录，构建轻量“技能索引”与 BM25 语料（倒排索引 + 文档频率），
供后续候选筛选与检索使用（当前阶段不实现排序，只负责构建数据）。
"""

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SkillCard:
    """技能索引中的一条记录（不含 program.code）。"""

    id: str
    name: str
    description: str
    domain: str
    selectors: List[str]
    url_matches: List[str]
    args: List[Dict[str, Any]]
    skill_path: str  # Skill_*.json 的绝对路径


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def _safe_list_str(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, (list, tuple)):
        return [str(x) for x in val if str(x)]
    return []


def _extract_args_schema(skill: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 skill JSON 中抽取简化后的 args_schema 列表。"""
    args: List[Dict[str, Any]] = []
    schema = skill.get("args_schema") or skill.get("program", {}).get("args_schema")
    if isinstance(schema, dict):
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        if isinstance(required, list):
            required = [str(x) for x in required]
        for name, info in props.items():
            if not isinstance(info, dict):
                continue
            arg_type = info.get("type") or "any"
            desc = info.get("description") or ""
            args.append(
                {
                    "name": str(name),
                    "type": str(arg_type),
                    "description": str(desc),
                    "required": str(name) in required,
                }
            )
    return args


def _build_terms(card: SkillCard) -> List[str]:
    """为 BM25/倒排索引构建简单 term 列表（全部小写，用空格/非字母分词）。"""
    text_parts: List[str] = []
    text_parts.append(card.id)
    text_parts.append(card.name)
    text_parts.append(card.description)
    text_parts.append(card.domain)
    text_parts.extend(card.selectors)
    text_parts.extend(card.url_matches)
    for a in card.args:
        text_parts.append(a.get("name", ""))
        text_parts.append(a.get("description", ""))
    full = " ".join([p for p in text_parts if p])
    # 用非字母数字分割
    tokens = re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", full.lower())
    return [t for t in tokens if t]


def _build_bm25_corpus(cards: List[SkillCard]) -> Dict[str, Any]:
    """构建简单的 BM25 语料：doc_freq + total_docs。

    这里只计算文档频率和总文档数，真正的打分逻辑由后续模块使用时实现。
    """
    doc_freq: Dict[str, int] = {}
    for card in cards:
        terms = set(_build_terms(card))
        for t in terms:
            doc_freq[t] = doc_freq.get(t, 0) + 1
    return {
        "doc_freq": doc_freq,
        "total_docs": len(cards),
    }


def build_skill_index(
    skills_root: str,
    *,
    out_path: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """扫描 skills_root，构建技能索引并（可选）写入 JSON 文件。

    返回结构:
    {
      "skills": [SkillCard...],
      "bm25": {"doc_freq": {...}, "total_docs": N}
    }
    """
    if verbose:
        print(f"[skill_index] skills_root={skills_root}")
    cards: List[SkillCard] = []
    for root, _dirs, files in os.walk(skills_root):
        for fname in files:
            if not fname.endswith(".json"):
                continue
            if not fname.startswith("Skill_"):
                continue
            path = os.path.join(root, fname)
            try:
                skill = _read_json(path)
            except Exception as e:
                if verbose:
                    print(f"[skill_index] skip {path}: read error {e}")
                continue
            sid = str(skill.get("id") or os.path.splitext(fname)[0])
            meta = skill.get("meta") or {}
            name = str(skill.get("name") or meta.get("name") or sid)
            # 描述优先级：顶层 description > meta.description > 空串
            desc = str(
                (skill.get("description") or meta.get("description") or "")
            )
            domain = str(skill.get("domain") or meta.get("domain") or "")
            pre = skill.get("preconditions") or {}
            url_matches = _safe_list_str(pre.get("url_matches"))
            loc = skill.get("locators") or {}
            selectors: List[str] = []
            if isinstance(loc, dict):
                primary = loc.get("selector")
                if primary:
                    selectors.append(str(primary))
                for alt in _safe_list_str(loc.get("selector_alt")):
                    if alt not in selectors:
                        selectors.append(alt)
            args = _extract_args_schema(skill)
            card = SkillCard(
                id=sid,
                name=name,
                description=desc,
                domain=domain,
                selectors=selectors,
                url_matches=url_matches,
                args=args,
                skill_path=os.path.abspath(path),
            )
            cards.append(card)
    if verbose:
        print(f"[skill_index] collected skills: {len(cards)}")
    bm25 = _build_bm25_corpus(cards) if cards else {"doc_freq": {}, "total_docs": 0}
    index = {
        "skills": [asdict(c) for c in cards],
        "bm25": bm25,
    }
    if out_path is None:
        out_path = os.path.join(skills_root, "skills_index.json")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"[skill_index] wrote {out_path}")
    except Exception as e:
        if verbose:
            print(f"[skill_index] write error: {e}")
    return index


def _cli() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Build skill index (JSON + BM25 corpus) from skills directory")
    p.add_argument("--skills-root", required=True, help="Root directory of Skill_* folders")
    p.add_argument("--out-path", type=str, default=None, help="Optional output path for index JSON")
    p.add_argument("--no-verbose", dest="verbose", action="store_false")
    args = p.parse_args()
    build_skill_index(
        args.skills_root,
        out_path=args.out_path,
        verbose=getattr(args, "verbose", True),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
