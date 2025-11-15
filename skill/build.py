"""
Skill skeleton builder (no external deps).

CLI:
  python -m skill.build --run-dir <detect_run_dir> --out <out_dir> [--domain example.com]

Generates one skill per interactive control in controls_tree.json with:
  - locators: primary selector + fallbacks (by_role/name, by_text, by_dom_index, selector_alt)
  - preconditions: url_matches, exists, optional not_exists, optional viewport
  - args_schema: inferred from action
  - evidence/meta: from detect artifacts

Notes:
  - This is a minimal, deterministic implementation per README rules.
  - No LLM; prefers stable attributes; avoids overfitting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------- I/O helpers -----------------------------


def _read_json(path: str) -> Dict[str, Any] | List[Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ----------------------------- Text utils -----------------------------


_OVERLAY_KEYWORDS = (
    "modal",
    "mask",
    "backdrop",
    "overlay",
    "dialog",
    "drawer",
    "popup",
    "toast",
    "tooltip",
    "snackbar",
    "loading",
    "spinner",
    "progress",
    "skeleton",
)


def _norm_text(s: Optional[str], *, max_len: int = 64) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len]
    # Drop if too numeric/symbolic
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters == 0 and digits > 0:
        return ""
    return s


_RE_DATE_PATTERNS = (
    r"\d{4}[\-/\.](?:0?[1-9]|1[0-2])[\-/\.](?:0?[1-9]|[12]\d|3[01])",  # YYYY-MM-DD/.
    r"(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日(?:\s*[()（）][^()（）]{0,6}[()（）])?",  # 11月12日(今天)
    r"\d+\s*晚",
    r"\d+\s*间",
    r"\d+\s*位",
)


def _strip_dynamic_tokens(s: Optional[str]) -> str:
    """去除明显的动态词片（日期/人数/晚数等），压缩空白。

    仅用于构建 by_text 与 by_role.name，降低非普适内容的干扰。
    """
    if not s:
        return ""
    import re as _re
    txt = str(s)
    # 移除括号内的"今天/明天/后天/周X/星期X"等提示
    txt = _re.sub(r"[()（）](?:今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天])[()（）]", "", txt)
    # 日期/数量模式
    for pat in _RE_DATE_PATTERNS:
        txt = _re.sub(pat, " ", txt)
    # 多余空白
    txt = _re.sub(r"\s+", " ", txt).strip()
    # 过长裁剪
    if len(txt) > 48:
        txt = txt[:48]
    return txt


def _stable_classes(class_str: Optional[str]) -> List[str]:
    if not class_str:
        return []
    parts = str(class_str).split()
    good: List[str] = []
    for c in parts:
        if len(c) > 30:
            continue
        letters = sum(ch.isalpha() for ch in c)
        digits = sum(ch.isdigit() for ch in c)
        if digits > letters and digits > 3:
            continue
        good.append(c)
        if len(good) >= 2:
            break
    return good


def _infer_role(tag: str, input_type: Optional[str]) -> Optional[str]:
    tag = (tag or "").lower()
    it = (input_type or "").lower() if input_type else ""
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag == "textarea":
        return "textbox"
    if tag == "select":
        return "combobox"
    if tag == "input":
        if it in ("checkbox",):
            return "checkbox"
        if it in ("radio",):
            return "radio"
        return "textbox"
    return None


# ----------------------------- Builders -----------------------------


@dataclass
class Inputs:
    run_dir: str
    controls_tree: Dict[str, Any]
    dom_summary: Dict[str, Any]
    ax: Dict[str, Any]
    meta: Dict[str, Any]
    snippets: Dict[str, str]  # id -> absolute file path


def _domain_from_meta(meta: Dict[str, Any]) -> str:
    d = (meta.get("domain") or meta.get("domain_sanitized") or "").strip()
    if d:
        return d
    url = (meta.get("url") or "").strip()
    m = re.match(r"^[a-zA-Z]+://([^/]+)/?", url)
    return m.group(1) if m else ""


def _url_regex_from_meta(meta: Dict[str, Any], override_domain: Optional[str] = None) -> str:
    domain = (override_domain or meta.get("domain") or meta.get("domain_sanitized") or "").strip()
    if not domain:
        # fallback to url parse
        url = (meta.get("url") or "").strip()
        m = re.match(r"^[a-zA-Z]+://([^/]+)/?", url)
        domain = m.group(1) if m else "example.com"
    domain = re.sub(r"\.", r"\\.", domain)
    return rf"^https?://([^/]*\.)?{domain}/"


def _element_from_index(dom_summary: Dict[str, Any], index: int) -> Dict[str, Any]:
    els = dom_summary.get("elements") or []
    if not isinstance(els, list):
        return {}
    if 0 <= index < len(els):
        return els[index] or {}
    return {}


def _derive_selector_alts(el: Dict[str, Any]) -> List[str]:
    tag = (el.get("tag") or "").lower() or "*"
    idv = el.get("id")
    name = el.get("name")
    role = el.get("role")
    cls = _stable_classes(el.get("class"))
    cands: List[str] = []
    if idv:
        cands.append(f"#{idv}")
    if name:
        cands.append(f"{tag}[name='{name}']")
    if role:
        cands.append(f"{tag}[role='{role}']")
        cands.append(f"[role='{role}']")
    if cls:
        cands.append(f"{tag}.{'.'.join(cls)}")
    # de-dup while keeping order
    seen: set[str] = set()
    alts: List[str] = []
    for s in cands:
        if s not in seen:
            seen.add(s)
            alts.append(s)
    return alts[:3]


def _build_by_role_name(el: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    role = (el.get("role") or "").strip()
    if not role:
        role = _infer_role(el.get("tag") or "", el.get("input_type") or el.get("type")) or ""
    if not role:
        return None
    aria = el.get("aria") or {}
    raw_name = aria.get("label") or aria.get("name") or el.get("placeholder") or el.get("title") or _norm_text(el.get("text") or el.get("innerText"))
    obj: Dict[str, Any] = {"role": role}
    if raw_name:
        name = _strip_dynamic_tokens(_norm_text(raw_name))
        if name:
            obj["name"] = name
            # 动态性越强，越不应精确匹配；没有数字且较短则 exact=True
            try:
                has_digit = any(ch.isdigit() for ch in name)
            except Exception:
                has_digit = False
            obj["exact"] = (not has_digit) and (len(name) <= 24)
    return obj


def _build_by_text(el: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    for key in ("text", "innerText"):
        t = _strip_dynamic_tokens(_norm_text(el.get(key)))
        if t:
            texts.append(t)
    aria = el.get("aria") or {}
    t = _strip_dynamic_tokens(_norm_text(aria.get("label") or aria.get("name")))
    if t:
        texts.append(t)
    # 过滤仍包含大量数字/符号的文本
    def _ok(s: str) -> bool:
        if not s:
            return False
        # 过短/过长过滤
        if len(s) < 2 or len(s) > 36:
            return False
        # 数字比例过高过滤
        digits = sum(ch.isdigit() for ch in s)
        return digits <= max(1, len(s) // 6)
    texts = [s for s in texts if _ok(s)]
    # dedup and trim to 3
    seen: set[str] = set()
    out: List[str] = []
    for s in texts:
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= 3:
            break
    return out


def _build_not_exists(dom_summary: Dict[str, Any]) -> List[str]:
    els = dom_summary.get("elements") or []
    hits: set[str] = set()
    for e in els if isinstance(els, list) else []:
        cls = (e.get("class") or "").lower()
        if not cls:
            continue
        for k in _OVERLAY_KEYWORDS:
            if k in cls:
                # map keyword to a generic selector token
                if k in ("modal",):
                    hits.add(".modal,.modal-mask,.ant-modal-wrap")
                elif k in ("mask", "backdrop"):
                    hits.add(".mask,.backdrop,.MuiBackdrop-root")
                elif k in ("overlay",):
                    hits.add(".overlay")
                elif k in ("dialog", "drawer"):
                    hits.add(".dialog,.drawer")
                elif k in ("toast", "snackbar"):
                    hits.add(".toast,.snackbar")
                elif k in ("loading", "spinner", "progress", "skeleton"):
                    hits.add(".loading,.spinner,.progress,.skeleton")
    return sorted(hits)


def _args_schema_for_action(action: str) -> Dict[str, Any]:
    action = (action or "").lower()
    if action == "type":
        return {"type": "object", "properties": {"text": {"type": "string"}}, "required": []}
    if action == "select":
        return {"type": "object", "properties": {"value": {"type": "string"}}, "required": []}
    return {"type": "object", "properties": {}}


def _make_skill(
    node: Dict[str, Any],
    el: Dict[str, Any],
    dom_summary: Dict[str, Any],
    meta: Dict[str, Any],
    run_dir: str,
    *,
    override_domain: Optional[str] = None,
) -> Dict[str, Any]:
    # ids
    node_id = str(node.get("id") or "")
    m = re.match(r"^d(\d+)$", node_id)
    dom_index = int(m.group(1)) if m else -1
    skill_id = f"d{dom_index}" if dom_index >= 0 else node_id or "unknown"

    # action
    action = (node.get("action") or "click").lower()

    # locators
    primary = node.get("selector") or ""
    by_role = _build_by_role_name(el)
    by_text = _build_by_text(el)
    selector_alt = _derive_selector_alts(el)
    locators: Dict[str, Any] = {"selector": primary}
    if selector_alt:
        locators["selector_alt"] = selector_alt
    if by_role:
        locators["by_role"] = by_role
    if by_text:
        locators["by_text"] = by_text
    if dom_index >= 0:
        locators["by_dom_index"] = dom_index
    bbox = el.get("bbox") or None
    if isinstance(bbox, list) and len(bbox) == 4:
        locators["bbox"] = bbox

    # preconditions
    url_pat = _url_regex_from_meta(meta, override_domain)
    pre: Dict[str, Any] = {
        "url_matches": [url_pat],
        "exists": [primary] if primary else [],
    }
    not_exists = _build_not_exists(dom_summary)
    if not_exists:
        pre["not_exists"] = not_exists

    # viewport minimal (only width for robustness)
    # default desktop baseline 960 if unknown
    pre["viewport"] = {"min_width": 960}

    # args schema
    args_schema = _args_schema_for_action(action)

    # evidence/meta
    evidence = {
        "tag": (el.get("tag") or "").lower() or None,
        "role": (el.get("role") or "").lower() or None,
        "name": (el.get("aria", {}) or {}).get("label") or (el.get("aria", {}) or {}).get("name") or _norm_text(el.get("text") or el.get("innerText")) or None,
        "from": "controls_tree+dom_summary",
    }
    evidence = {k: v for k, v in evidence.items() if v}

    meta_out = {
        "schema_version": "skill_schema_v1",
        "source_dir": run_dir,
    }

    return {
        "id": skill_id,
        "domain": override_domain or _domain_from_meta(meta) or "",
        "label": None,
        "slug": None,
        "action": action,
        "preconditions": pre,
        "locators": locators,
        "args_schema": args_schema,
        "program": {
            "language": "python",
            "entry": f"program__{skill_id}__auto",
            "code": "",
        },
        "evidence": evidence,
        "meta": meta_out,
    }


def _is_control(node: Dict[str, Any]) -> bool:
    return (node.get("type") or "") == "control"


def load_inputs(run_dir: str, *, verbose: bool = False) -> Inputs:
    p_ct = os.path.join(run_dir, "controls_tree.json")
    p_ds = os.path.join(run_dir, "dom_summary.json")
    p_ax = os.path.join(run_dir, "ax.json")
    p_meta = os.path.join(run_dir, "meta.json")
    if verbose:
        print(f"[skill.build] load controls_tree: {p_ct}")
    controls_tree = _read_json(p_ct)  # type: ignore[assignment]
    if verbose:
        print(f"[skill.build] load dom_summary: {p_ds}")
    dom_summary = _read_json(p_ds)  # type: ignore[assignment]
    ax = _read_json(p_ax) if os.path.exists(p_ax) else {}  # type: ignore[assignment]
    meta = _read_json(p_meta) if os.path.exists(p_meta) else {}
    # load snippets index if present
    p_sn = os.path.join(run_dir, "snippets", "index.json")
    sn_map: Dict[str, str] = {}
    if os.path.exists(p_sn):
        try:
            idx = _read_json(p_sn)  # type: ignore[assignment]
            for it in (idx.get("items") or []):
                if not isinstance(it, dict):
                    continue
                sid = str(it.get("id") or "").strip()
                rel = str(it.get("file") or "").strip()
                if sid and rel:
                    sn_map[sid] = os.path.join(run_dir, rel)
        except Exception:
            sn_map = {}
    if verbose:
        nn = len((controls_tree or {}).get("nodes") or [])
        print(f"[skill.build] nodes={nn}, snippets={len(sn_map)}")
    return Inputs(run_dir=run_dir, controls_tree=controls_tree, dom_summary=dom_summary, ax=ax, meta=meta, snippets=sn_map)


# ----------------------------- Snippet parsing -----------------------------


class _TopTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found = False
        self.tag: str | None = None
        self.attrs: Dict[str, str] = {}
        self.texts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if not self.found:
            self.found = True
            self.tag = tag
            for k, v in attrs:
                if k and v is not None:
                    self.attrs[k] = v

    def handle_data(self, data: str) -> None:
        t = _norm_text(data)
        if t:
            self.texts.append(t)


def _parse_snippet_features(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        return {}
    p = _TopTagParser()
    try:
        p.feed(html)
    except Exception:
        pass
    tag = (p.tag or "").lower()
    a = {k.lower(): v for k, v in p.attrs.items()}
    classes = a.get("class", "")
    role = a.get("role")
    aria_label = a.get("aria-label") or a.get("aria_name") or a.get("aria-labelledby")
    input_type = a.get("type")
    name = a.get("name")
    idv = a.get("id")
    href = a.get("href")
    placeholder = a.get("placeholder")
    title = a.get("title")
    data_testid = a.get("data-testid") or a.get("data-qa") or a.get("data-cy")
    text = _norm_text(" ".join(p.texts))
    return {
        "tag": tag,
        "attrs": a,
        "class": classes,
        "role": role,
        "aria_label": aria_label,
        "input_type": input_type,
        "name": name,
        "id": idv,
        "href": href,
        "placeholder": placeholder,
        "title": title,
        "data_testid": data_testid,
        "text": text,
    }


def _locators_from_snippet(feat: Dict[str, Any]) -> Tuple[Optional[str], List[str], Optional[Dict[str, Any]], List[str]]:
    """Return (primary, selector_alt, by_role, by_text) derived from snippet features."""
    if not feat:
        return None, [], None, []
    tag = feat.get("tag") or "*"
    idv = feat.get("id")
    name = feat.get("name")
    role = feat.get("role")
    aria = feat.get("aria_label")
    classes = _stable_classes(feat.get("class"))
    data_testid = feat.get("data_testid")

    primary: Optional[str] = None
    alts: List[str] = []
    if idv:
        primary = f"#{idv}"
    elif name:
        primary = f"{tag}[name='{name}']"
    elif data_testid:
        primary = f"[data-testid='{data_testid}']"
    elif role and aria:
        primary = f"{tag}[role='{role}'][aria-label='{aria}']"
    elif classes:
        primary = f"{tag}.{'.'.join(classes)}"

    # build alts (avoid duplicating primary)
    cand: List[str] = []
    if idv:
        cand.append(f"#{idv}")
    if name:
        cand.append(f"{tag}[name='{name}']")
    if data_testid:
        cand.append(f"[data-testid='{data_testid}']")
    if role:
        cand.append(f"{tag}[role='{role}']")
    if aria and role:
        cand.append(f"{tag}[role='{role}'][aria-label='{aria}']")
    if classes:
        cand.append(f"{tag}.{'.'.join(classes)}")
    seen: set[str] = set()
    for s in cand:
        if s and s != primary and s not in seen:
            seen.add(s)
            alts.append(s)
        if len(alts) >= 3:
            break

    # by_role/name
    by_role = None
    if role:
        by_role = {"role": role}
        if aria:
            t = _norm_text(aria)
            if t:
                by_role["name"] = t
                by_role["exact"] = True

    # by_text
    texts: List[str] = []
    for key in ("text", "placeholder", "title"):
        t = _norm_text(feat.get(key))
        if t:
            texts.append(t)
    # de-dup to <=3
    out: List[str] = []
    seen2: set[str] = set()
    for s in texts:
        if s not in seen2:
            seen2.add(s)
            out.append(s)
        if len(out) >= 3:
            break

    return primary, alts, by_role, out


def _infer_action_from_snippet(feat: Dict[str, Any], current: str) -> str:
    if not feat:
        return current
    tag = (feat.get("tag") or "").lower()
    it = (feat.get("input_type") or "").lower()
    href = feat.get("href")
    role = (feat.get("role") or "").lower()
    if tag == "a" and href:
        return "navigate"
    if tag == "button" or role == "button":
        return "click"
    if tag == "textarea":
        return "type"
    if tag == "select":
        return "select"
    if tag == "input":
        if it in ("checkbox", "radio", "switch"):
            return "toggle"
        if it in ("submit",):
            return "submit"
        return "type"
    return current


def build_skills(inp: Inputs, *, domain: Optional[str] = None, use_snippets: bool = True, prefer_snippet: bool = True, verbose: bool = False) -> List[Dict[str, Any]]:
    if verbose:
        print("[skill.build] build skills …")
    skills: List[Dict[str, Any]] = []
    nodes = inp.controls_tree.get("nodes") or []
    for n in nodes if isinstance(nodes, list) else []:
        if not _is_control(n):
            continue
        node_id = str(n.get("id") or "")
        m = re.match(r"^d(\d+)$", node_id)
        idx = int(m.group(1)) if m else -1
        el = _element_from_index(inp.dom_summary, idx) if idx >= 0 else {}
        skill = _make_skill(n, el, inp.dom_summary, inp.meta, inp.run_dir, override_domain=domain)
        # optional refinement from snippet
        if use_snippets and node_id in inp.snippets:
            if verbose:
                print(f"[skill.build] refine from snippet: {node_id}")
            feat = _parse_snippet_features(inp.snippets[node_id])
            # action inference if missing/none/unknown
            cur_action = (skill.get("action") or "").lower()
            if not cur_action or cur_action in ("none", "unknown"):
                skill["action"] = _infer_action_from_snippet(feat, cur_action or "click")
                skill["args_schema"] = _args_schema_for_action(skill["action"])  # refresh
            # locators merge
            p_sn, alts_sn, by_role_sn, by_text_sn = _locators_from_snippet(feat)
            locs = skill.get("locators") or {}
            if p_sn and prefer_snippet:
                # replace primary and add old as alt (front)
                old = locs.get("selector")
                if old and old != p_sn:
                    locs.setdefault("selector_alt", [])
                    locs["selector_alt"] = [old] + [x for x in (locs.get("selector_alt") or []) if x != old]
                locs["selector"] = p_sn
            # merge alts
            if alts_sn:
                merged = []
                seen = set()
                for s in (alts_sn + (locs.get("selector_alt") or [])):
                    if s not in seen and s != locs.get("selector"):
                        seen.add(s)
                        merged.append(s)
                if merged:
                    locs["selector_alt"] = merged[:3]
            # merge by_role/by_text
            if by_role_sn:
                locs["by_role"] = by_role_sn
            if by_text_sn:
                cur_bt = locs.get("by_text") or []
                dd = []
                seen2 = set()
                for s in (by_text_sn + cur_bt):
                    if s and s not in seen2:
                        seen2.add(s)
                        dd.append(s)
                locs["by_text"] = dd[:3]
            skill["locators"] = locs
            # refresh preconditions.exists to include primary
            pre = skill.get("preconditions") or {}
            sel = (skill.get("locators") or {}).get("selector")
            if sel:
                exists = pre.get("exists") or []
                if sel not in exists:
                    pre["exists"] = [sel] + [x for x in exists if x != sel]
            skill["preconditions"] = pre
        skills.append(skill)
    if verbose:
        print(f"[skill.build] built skills: {len(skills)}")
    return skills


def save_skills(skills: List[Dict[str, Any]], out_dir: str, domain: Optional[str]) -> List[str]:
    written: List[str] = []
    # organize into skill/skill_library/<domain>/<id>.json
    for s in skills:
        d = domain or s.get("domain") or "unknown"
        sid = s.get("id") or "unknown"
        path = os.path.join(out_dir, d, f"{sid}.json")
        _write_json(path, s)
        written.append(path)
    return written


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build skill skeletons from detect artifacts")
    ap.add_argument("--run-dir", required=True, help="Path to a detect run dir containing controls_tree.json, dom_summary.json, meta.json")
    ap.add_argument("--out", required=True, help="Output directory for skill_library")
    ap.add_argument("--domain", default=None, help="Override domain for output grouping (optional)")
    ap.add_argument("--no-use-snippets", dest="use_snippets", action="store_false", help="Do not use snippets to refine locators/action")
    ap.add_argument("--no-prefer-snippet", dest="prefer_snippet", action="store_false", help="Do not prefer snippet-derived selector as primary")
    ap.add_argument("--no-verbose", dest="verbose", action="store_false", help="Disable verbose logs (default: on)")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    inp = load_inputs(args.run_dir, verbose=getattr(args, "verbose", True))
    skills = build_skills(inp, domain=args.domain, use_snippets=getattr(args, "use_snippets", True), prefer_snippet=getattr(args, "prefer_snippet", True), verbose=getattr(args, "verbose", True))
    save_skills(skills, args.out, args.domain)
    # No prints; return 0 for success
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
