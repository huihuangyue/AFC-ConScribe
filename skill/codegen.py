"""
基于提示词模板生成 program.code（调用外部 LLM）。

用法（CLI）:
  python -m skill.codegen --skill <skill.json> --run-dir <detect_run_dir> --out <out.json> [--append-notes]
  # 或原地覆盖：
  python -m skill.codegen --skill <skill.json> --in-place --run-dir <detect_run_dir>

行为：
- 读取技能 JSON（需符合 skill/schema.json），可选从 run_dir 读取 snippets 与 meta 提升上下文。
- 渲染提示词（skill/prompt/nl2code.md），将占位符替换为真实数据。
- 通过 LLM 客户端获取代码字符串，写入 skill["program"]["code"]。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, Optional
from datetime import datetime

from .llm_client import complete_text_with_usage, LLMConfig


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _write_codegen_log(skill_path: str, *, usage: Dict[str, Any], ttf_sec: float, run_dir: Optional[str], code_text: str) -> None:
    """Persist codegen metrics log next to the skill JSON (new_skill 下可见)."""
    try:
        base_dir = os.path.dirname(os.path.abspath(skill_path))
        log_dir = os.path.join(base_dir, "_gen_logs")
        os.makedirs(log_dir, exist_ok=True)
        skill = _read_json(skill_path)
        sid = str(skill.get("id") or "unknown")
        meta = _get_run_meta(run_dir)
        log = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "skill_path": skill_path,
            "skill_id": sid,
            "domain": meta.get("domain") or (skill.get("domain") or ""),
            "url": meta.get("url", ""),
            "codegen": {
                "provider": usage.get("provider"),
                "model": usage.get("model"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens") or usage.get("estimated_tokens"),
                "ttf_sec": round(float(ttf_sec), 3),
                "code_chars": len(code_text or ""),
                "code_lines": len((code_text or "").splitlines()),
            },
        }
        out = os.path.join(log_dir, f"codegen_{sid}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json")
        _write_json(out, log)
    except Exception:
        pass


def _get_run_meta(run_dir: Optional[str]) -> Dict[str, Any]:
    if not run_dir:
        return {}
    p = os.path.join(run_dir, "meta.json")
    if os.path.exists(p):
        try:
            return _read_json(p)
        except Exception:
            return {}
    return {}


def _get_snippet_html(run_dir: Optional[str], skill_id: str) -> str:
    """优先从 tips/index.json 取指定 id 的 outerHTML；找不到则回退 snippets。"""
    if not run_dir:
        return ""
    # 1) tips 优先（每节点一段 outerHTML）
    tips_idx = os.path.join(run_dir, "tips", "index.json")
    try:
        if os.path.exists(tips_idx):
            idx = _read_json(tips_idx)
            for it in (idx.get("items") or []):
                if str(it.get("id")) == str(skill_id):
                    f = os.path.join(run_dir, str(it.get("file") or ""))
                    if os.path.exists(f):
                        return _read_text(f)
    except Exception:
        pass
    # 2) 回退到 snippets（第一层控件片段）
    sn_idx = os.path.join(run_dir, "snippets", "index.json")
    try:
        if os.path.exists(sn_idx):
            idx = _read_json(sn_idx)
            for it in (idx.get("items") or []):
                if str(it.get("id")) == str(skill_id):
                    f = os.path.join(run_dir, str(it.get("file") or ""))
                    if os.path.exists(f):
                        return _read_text(f)
    except Exception:
        pass
    return ""


def _get(mapping: Dict[str, Any], path: str, default: Any = "") -> Any:
    cur: Any = mapping
    for key in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _render_nl2code_prompt(skill: Dict[str, Any], *, run_dir: Optional[str]) -> str:
    tpl_path = os.path.join(os.path.dirname(__file__), "prompt", "nl2code.md")
    tpl = _read_text(tpl_path)

    meta = _get_run_meta(run_dir)
    locs = skill.get("locators") or {}
    pre = skill.get("preconditions") or {}
    args_schema = skill.get("args_schema") or {}
    bbox = locs.get("bbox") or [0, 0, 0, 0]
    sid = str(skill.get("id") or "")
    snippet_html = _get_snippet_html(run_dir, sid)

    # 组装占位符映射
    mapping: Dict[str, str] = {
        "html": snippet_html or "",
        "meta.domain": str(_get(meta, "domain", "")),
        "meta.url": str(_get(meta, "url", "")),
        "meta.viewport.width": str(_get(meta, "viewport.width", "")),
        "meta.viewport.height": str(_get(meta, "viewport.height", "")),
        "ct.id": sid,
        "ct.action": str(skill.get("action") or ""),
        "ct.selector": str(locs.get("selector") or ""),
        "ct.bbox.x": str(int(bbox[0] or 0) if isinstance(bbox, list) and len(bbox) >= 4 else ""),
        "ct.bbox.y": str(int(bbox[1] or 0) if isinstance(bbox, list) and len(bbox) >= 4 else ""),
        "ct.bbox.w": str(int(bbox[2] or 0) if isinstance(bbox, list) and len(bbox) >= 4 else ""),
        "ct.bbox.h": str(int(bbox[3] or 0) if isinstance(bbox, list) and len(bbox) >= 4 else ""),
        "locators.selector": json.dumps(locs.get("selector") or "" , ensure_ascii=False),
        "locators.selector_alt_json": json.dumps(locs.get("selector_alt") or [], ensure_ascii=False),
        "locators.by_role_json": json.dumps(locs.get("by_role") or {}, ensure_ascii=False),
        "locators.by_text_json": json.dumps(locs.get("by_text") or [], ensure_ascii=False),
        "locators.by_dom_index": json.dumps(locs.get("by_dom_index") if "by_dom_index" in locs else None, ensure_ascii=False),
        "preconditions_json": json.dumps(pre, ensure_ascii=False),
        "args_schema_json": json.dumps(args_schema, ensure_ascii=False),
        "post.expect_url_change_bool": "false",
        "post.expect_appear_selectors_json": "[]",
        "post.expect_disappear_selectors_json": "[]",
    }

    # 用正则仅替换我们识别的占位符 {token}
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        return mapping.get(key, m.group(0))

    rendered = re.sub(r"\{([A-Za-z0-9_\.]+)\}", repl, tpl)
    return rendered


def _extract_code(text: str) -> str:
    """从 LLM 返回文本中抽取首个 ``` 代码块；若无，则返回原文本。
    去除 ```python/``` 等围栏标记。
    """
    import re as _re
    m = _re.search(r"```[a-zA-Z]*\n([\s\S]*?)\n```", text)
    if m:
        return m.group(1).strip()
    return text.strip()


def generate_program_with_metrics(skill: Dict[str, Any], *, run_dir: Optional[str], verbose: bool = True) -> tuple[str, dict]:
    """生成 Python 程序代码，并返回 (code, usage) 便于打印指标。"""
    prompt = _render_nl2code_prompt(skill, run_dir=run_dir)
    if verbose:
        print(f"[skill.codegen] Prompt prepared ({len(prompt)} chars)")
    cfg = LLMConfig()
    try:
        text, usage = complete_text_with_usage(prompt, config=cfg, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"[skill.codegen] LLM error: {type(e).__name__}: {e}")
        # 失败时返回一个可追踪的占位代码以避免阻断流程
        fallback = (
            "# NL2Code failed; please retry.\n"
            f"# error: {type(e).__name__}: {str(e)}\n\n"
            "def program(page, args=None):\n"
            "    raise RuntimeError('NL2Code failed; see logs')\n"
        )
        return fallback, {"error": str(e), "provider": cfg.provider, "model": cfg.model, "estimated_tokens": max(1, len(prompt)//4)}
    code = _extract_code(text)
    if not code:
        # 若抽取后仍空，则用原始文本（有些模型不带围栏）
        code = text
    if verbose:
        prov = usage.get("provider") or cfg.provider
        mdl = usage.get("model") or cfg.model
        print(f"[skill.codegen] LLM provider={prov} model={mdl} tokens={usage.get('total_tokens', usage.get('estimated_tokens'))}")
    return code, usage


def _apply_code(skill: Dict[str, Any], code: str, *, entry_fallback: Optional[str] = None) -> Dict[str, Any]:
    prog = skill.get("program") or {}
    if not prog.get("language"):
        prog["language"] = "python"
    if not prog.get("entry"):
        prog["entry"] = entry_fallback or f"program__{skill.get('id','unknown')}__auto"
    prog["code"] = code
    skill["program"] = prog
    return skill


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate program.code from prompt template via LLM")
    ap.add_argument("--skill", required=True, help="Path to skill JSON (input)")
    ap.add_argument("--out", default=None, help="Path to write updated skill JSON (default: <input> if --in-place)")
    ap.add_argument("--in-place", action="store_true", help="Overwrite input skill JSON in place")
    ap.add_argument("--run-dir", default=None, help="Detect run dir to provide meta/snippets (optional)")
    ap.add_argument("--no-verbose", dest="verbose", action="store_false", help="Disable verbose logs (default: on)")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    import time as _t
    skill = _read_json(args.skill)
    t0 = _t.perf_counter()
    code, usage = generate_program_with_metrics(skill, run_dir=args.run_dir, verbose=getattr(args, "verbose", True))
    dt = _t.perf_counter() - t0
    print(f"[METRIC] TTF(s)={dt:.2f} Token(total)={usage.get('total_tokens', 'NA')}")
    # 写入 meta.codegen
    meta = skill.get("meta") or {}
    meta["codegen"] = {
        "provider": usage.get("provider"),
        "model": usage.get("model"),
        "total_tokens": usage.get("total_tokens", usage.get("estimated_tokens")),
        "ttf_sec": round(dt, 3),
    }
    skill["meta"] = meta
    skill = _apply_code(skill, code)
    out_path = args.out or (args.skill if args.in_place else None)
    if not out_path:
        # 默认输出到旁边的 .gen.json
        base, ext = os.path.splitext(args.skill)
        out_path = base + ".gen.json"
    _write_json(out_path, skill)
    # 记录日志
    try:
        _write_codegen_log(out_path, usage=usage, ttf_sec=dt, run_dir=args.run_dir, code_text=code)
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
