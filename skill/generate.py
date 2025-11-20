"""
生成单个控件的技能 JSON（总入口），并可选集成 NL2Code（调用 LLM 生成 program.code）。

输入：一次采集目录（run_dir）与控件选择器（来自 controls_tree.json 的 selector 字段）。
输出：将该控件对应的技能写入 run_dir/skill/Skill_(选择器)_(控件ID).json。

CLI:
  python -m skill.generate --run-dir <path/to/run_dir> --selector "<css>" [--domain example.com]
  可选：--no-use-snippets / --no-prefer-snippet / --with-codegen (或 --nl2code) / --no-verbose
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from typing import Any, Dict, List

try:
    from utils.skill_export import export_program_py, _detect_main_func_name  # type: ignore
except Exception:  # pragma: no cover
    export_program_py = None  # type: ignore
    _detect_main_func_name = None  # type: ignore

from . import build as builder
from . import codegen as _codegen
from .args_schema import attach_args_schema_from_program
from .description import attach_description_from_program


def _pick_node_by_selector(controls_tree: Dict[str, Any], selector: str) -> Dict[str, Any] | None:
    nodes = [n for n in (controls_tree.get("nodes") or []) if isinstance(n, dict)]
    # 放宽：允许 content/container 等非 control 节点，以便对容器生成技能骨架
    cand = [n for n in nodes if str(n.get("selector") or "") == selector]
    if not cand:
        return None
    # 选取 bbox.y 最小（最靠上）的那个
    def _y(n: Dict[str, Any]) -> int:
        try:
            return int((n.get("geom") or {}).get("bbox", [0, 0, 0, 0])[1] or 0)
        except Exception:
            return 0
    cand.sort(key=_y)
    return cand[0]


def _sanitize_filename_piece(s: str, *, max_len: int = 64) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "sel"


def generate_for_selector(
    run_dir: str,
    selector: str,
    *,
    domain: str | None = None,
    use_snippets: bool = True,
    prefer_snippet: bool = True,
    verbose: bool = True,
    with_codegen: bool = False,
) -> str:
    """生成单个控件的技能 JSON 并写入 run_dir/skill/Skill_(selector)_(id).json。

    返回写入的文件绝对路径。
    """
    inp = builder.load_inputs(run_dir, verbose=verbose)
    node = _pick_node_by_selector(inp.controls_tree, selector)
    if node is None:
        raise RuntimeError(f"未在 controls_tree.json 中找到选择器: {selector}")

    # 先批量构建（仅对 control 节点）；若未命中，则回退为“直接构建单个节点技能”（支持 content/container）
    sid = str(node.get("id") or "")
    skills = builder.build_skills(inp, domain=domain, use_snippets=use_snippets, prefer_snippet=prefer_snippet, verbose=verbose)
    skill = next((s for s in skills if str(s.get("id") or "") == sid), None)
    if skill is None:
        # 回退路径：根据 dom_index 抽取 element，直接用内部 _make_skill 生成骨架
        m = re.match(r"^d(\d+)$", sid)
        dom_index = int(m.group(1)) if m else -1
        if dom_index < 0:
            raise RuntimeError(f"未能从节点ID推导 dom_index：{sid}")
        el = builder._element_from_index(inp.dom_summary, dom_index)  # type: ignore[attr-defined]
        if not el:
            raise RuntimeError(f"未在 dom_summary.json 中找到索引 {dom_index} 的元素（节点 {sid}）")
        # 使用内部构建器生成最小可用技能（action 缺省为 click）
        skill = builder._make_skill(  # type: ignore[attr-defined]
            node,
            el,
            inp.dom_summary,
            inp.meta,
            inp.run_dir,
            override_domain=domain,
        )

    # 目标输出路径：以 JSON 文件名（去扩展名）创建同名子目录，JSON 与导出的 .py 存于该目录下
    safe_sel = _sanitize_filename_piece(selector)
    skill_root = os.path.join(run_dir, "skill")
    os.makedirs(skill_root, exist_ok=True)
    out_name = f"Skill_{safe_sel}_{sid}.json"
    skill_dir = os.path.join(skill_root, os.path.splitext(out_name)[0])
    os.makedirs(skill_dir, exist_ok=True)
    out_path = os.path.join(skill_dir, out_name)

    # 可选：集成 NL2Code，调用 LLM 生成 program.code
    if with_codegen:
        try:
            if verbose:
                print("[skill.generate] run NL2Code via skill.codegen …")
            _t0 = time.perf_counter()
            code, usage = _codegen.generate_program_with_metrics(skill, run_dir=run_dir, verbose=verbose)
            _dt = time.perf_counter() - _t0
            # 应用代码到技能（保持既有 entry）
            prog = skill.get("program") or {}
            prog["language"] = prog.get("language") or "python"
            prog["entry"] = prog.get("entry") or f"program__{skill.get('id','unknown')}__auto"
            prog["code"] = code
            # 若可用，则从代码中推断主函数名，写入 main_func 便于后续调度/调用
            try:
                if _detect_main_func_name is not None and not prog.get("main_func"):
                    main_name = _detect_main_func_name(code)  # type: ignore[operator]
                    if main_name:
                        prog["main_func"] = main_name
            except Exception:
                if verbose:
                    print("[skill.generate] main_func inference failed")
            skill["program"] = prog
            # 从 program.code 推断 args_schema（仅在原 schema 为空时生效）
            try:
                attach_args_schema_from_program(skill, overwrite=False)
            except Exception as _ae:
                if verbose:
                    print(f"[skill.generate] args_schema inference failed: {type(_ae).__name__}: {_ae}")
            # 从主函数 docstring 推断技能描述（仅在原描述为空时生效）
            try:
                attach_description_from_program(skill, overwrite=False)
            except Exception as _de:
                if verbose:
                    print(f"[skill.generate] description inference failed: {type(_de).__name__}: {_de}")
            # 记录元数据
            meta = skill.get("meta") or {}
            meta["codegen"] = {
                "provider": usage.get("provider"),
                "model": usage.get("model"),
                "tokens": usage.get("total_tokens", usage.get("estimated_tokens")),
                "ttf_sec": round(_dt, 3),
                "ok": True,
            }
            skill["meta"] = meta
            if verbose:
                print(f"[skill.generate] NL2Code done tokens={usage.get('total_tokens', usage.get('estimated_tokens'))} ttf={_dt:.2f}s")
            # 自动导出 .py 文件（与 JSON 同一目录）
            if export_program_py is not None:
                try:
                    py_out = export_program_py(skill, out_path, out_dir=skill_dir, overwrite=True)
                    if verbose:
                        print(f"[skill.generate] exported program to {py_out}")
                except Exception as _ee:
                    if verbose:
                        print(f"[skill.generate] export .py failed: {type(_ee).__name__}: {_ee}")
        except Exception as e:  # 不中断，仍写骨架
            if verbose:
                print(f"[skill.generate] NL2Code failed: {type(e).__name__}: {e}")
            meta = skill.get("meta") or {}
            meta["codegen"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            skill["meta"] = meta

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(skill, f, ensure_ascii=False, indent=2)
    if verbose:
        print(f"[skill.generate] wrote {out_path}")
    # 记录 codegen 详细日志（与 skill.codegen CLI 对齐）
    if with_codegen:
        try:
            # 若上面 with_codegen 成功执行，则 code/usage/_dt 均存在于局部作用域
            # 失败分支不会进入此处
            _codegen._write_codegen_log(out_path, usage=usage, ttf_sec=_dt, run_dir=run_dir, code_text=code)  # type: ignore[attr-defined]
        except Exception:
            pass
    return out_path


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate a single skill JSON for a specific control selector")
    ap.add_argument("--run-dir", required=True, help="Detect run dir, e.g., workspace/data/csdn_net/20251110155610")
    ap.add_argument("--selector", required=True, help="Control CSS selector from controls_tree.json")
    ap.add_argument("--domain", default=None, help="Override domain for output grouping (optional)")
    ap.add_argument("--no-use-snippets", dest="use_snippets", action="store_false", help="Do not use snippets to refine locators/action")
    ap.add_argument("--no-prefer-snippet", dest="prefer_snippet", action="store_false", help="Do not prefer snippet-derived selector as primary")
    ap.add_argument("--with-codegen", dest="with_codegen", action="store_true", help="Call LLM (nl2code) to generate program.code and write back")
    ap.add_argument("--nl2code", dest="with_codegen", action="store_true", help="Alias of --with-codegen")
    ap.add_argument("--no-verbose", dest="verbose", action="store_false", help="Disable verbose logs (default: on)")
    ap.set_defaults(use_snippets=True, prefer_snippet=True)
    return ap.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    path = generate_for_selector(
        args.run_dir,
        args.selector,
        domain=args.domain,
        use_snippets=getattr(args, "use_snippets", True),
        prefer_snippet=getattr(args, "prefer_snippet", True),
        verbose=getattr(args, "verbose", True),
        with_codegen=getattr(args, "with_codegen", False),
    )
    print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
