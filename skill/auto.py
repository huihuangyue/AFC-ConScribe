from __future__ import annotations

"""
skill.auto

根据 detect 产物自动选主控件块并批量生成技能：
- 优先使用严格规则分块 blocks.json（已默认开启）；
- 逐个块调用 skill.generate.generate_for_selector 产出技能 JSON；
- 可选联动 NL2Code（with-codegen）。

用法：
  python -m skill.auto --run-dir workspace/data/<domain>/<ts> \
    --top-k 3 --with-codegen
"""

import argparse
import json
import os
from typing import Any, Dict, List, Optional

try:
    from .generate import generate_for_selector  # type: ignore
except Exception:
    from generate import generate_for_selector  # type: ignore


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pick_block_selectors(run_dir: str, top_k: int) -> List[str]:
    p = os.path.join(run_dir, "blocks.json")
    sels: List[str] = []
    if os.path.exists(p):
        try:
            doc = _read_json(p)
            blocks = [b for b in (doc.get("blocks") or []) if isinstance(b, dict)]
            for b in blocks:
                sel = (b.get("selector") or "").strip()
                if sel:
                    sels.append(sel)
                if len(sels) >= int(max(1, top_k)):
                    break
        except Exception:
            pass
    # 回退：controls_tree roots 的前若干个 selector
    if not sels:
        try:
            ct = _read_json(os.path.join(run_dir, "controls_tree.json"))
            nodes = [n for n in (ct.get("nodes") or []) if isinstance(n, dict)]
            roots = set(str(x) for x in (ct.get("roots") or []))
            for n in nodes:
                if str(n.get("id")) in roots:
                    s = (n.get("selector") or "").strip()
                    if s:
                        sels.append(s)
                    if len(sels) >= int(max(1, top_k)):
                        break
        except Exception:
            pass
    return sels


def auto_generate(
    run_dir: str,
    *,
    top_k: int = 3,
    domain: Optional[str] = None,
    with_codegen: bool = False,
    verbose: bool = True,
    use_tree_filter: bool = False,
    require_submit: bool = True,
    require_inner_kw: bool = True,
    min_children: int = 2,
    min_w: int = 96,
    min_h: int = 80,
    min_area: int = 20000,
    max_area_ratio: float = 0.6,
) -> List[str]:
    if use_tree_filter or any([
        require_submit is not True,
        require_inner_kw is not True,
        min_children != 2,
        min_w != 96,
        min_h != 80,
        min_area != 20000,
        max_area_ratio != 0.6,
    ]):
        # 使用 controls_tree 过滤选择器
        try:
            from .select import filter_selectors  # type: ignore
        except Exception:
            from select import filter_selectors  # type: ignore
        selectors = filter_selectors(
            run_dir,
            top_k=int(top_k or 1),
            types=["content", "control"],
            min_w=min_w,
            min_h=min_h,
            min_area=min_area,
            max_area_ratio=max_area_ratio,
            min_children=min_children,
            require_submit_in_subtree=require_submit,
            require_inner_kw=require_inner_kw,
        )
    else:
        selectors = _pick_block_selectors(run_dir, top_k=int(top_k or 1))
    if verbose:
        print(f"[skill.auto] picked selectors from blocks/roots: {selectors}")
    out_paths: List[str] = []
    for sel in selectors:
        try:
            p = generate_for_selector(
                run_dir,
                sel,
                domain=domain,
                use_snippets=True,
                prefer_snippet=True,
                verbose=verbose,
                with_codegen=with_codegen,
            )
            out_paths.append(p)
        except Exception as e:
            if verbose:
                print(f"[skill.auto] generate failed for {sel}: {type(e).__name__}: {e}")
    return out_paths


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Auto-generate skills from detect blocks/roots")
    ap.add_argument("--run-dir", required=True, help="Detect run dir")
    ap.add_argument("--top-k", type=int, default=3, help="How many blocks to generate (default: 3)")
    ap.add_argument("--domain", type=str, default=None)
    ap.add_argument("--with-codegen", action="store_true")
    ap.add_argument("--no-verbose", dest="verbose", action="store_false")
    # 选择器筛选（可选）：使用 controls_tree.json 而不是 blocks.json
    ap.add_argument("--use-tree-filter", action="store_true", help="Select selectors from controls_tree with strict-like filters")
    ap.add_argument("--no-require-submit", dest="require_submit", action="store_false", help="Do not require submit button in subtree")
    ap.add_argument("--no-require-inner-kw", dest="require_inner_kw", action="store_false", help="Do not require inner container class keywords")
    ap.add_argument("--min-children", type=int, default=2)
    ap.add_argument("--min-w", type=int, default=96)
    ap.add_argument("--min-h", type=int, default=80)
    ap.add_argument("--min-area", type=int, default=20000)
    ap.add_argument("--max-area-ratio", type=float, default=0.6)
    args = ap.parse_args()
    outs = auto_generate(
        args.run_dir,
        top_k=args.top_k,
        domain=args.domain,
        with_codegen=getattr(args, "with_codegen", False),
        verbose=getattr(args, "verbose", True),
        use_tree_filter=getattr(args, "use_tree_filter", False),
        require_submit=getattr(args, "require_submit", True),
        require_inner_kw=getattr(args, "require_inner_kw", True),
        min_children=args.min_children,
        min_w=args.min_w,
        min_h=args.min_h,
        min_area=args.min_area,
        max_area_ratio=args.max_area_ratio,
    )
    for p in outs:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
