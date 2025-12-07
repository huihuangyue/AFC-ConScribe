"""
Diagnostic core (no LLM): decide mismatch vs damage by checking preconditions
against new run artifacts.

Heuristic:
  - If any preconditions.exists selector cannot be found in new dom_summary -> mismatch
  - Else if primary selector appears to change (role/text changed) -> damage
  - Else -> damage (default)
"""

from __future__ import annotations

from typing import Any, Dict, List

from .diff_analyzer import analyze, _find_element_by_selector


def _exists(dom_summary: Dict[str, Any], selector: str) -> bool:
    els = dom_summary.get("elements") or []
    if selector.startswith('#'):
        idv = selector[1:]
        return any((e.get("id") or "") == idv for e in els)
    if "[name=" in selector:
        try:
            name = selector.split("[name=")[1].split("]")[0].strip("'\"")
            return any((e.get("name") or "") == name for e in els)
        except Exception:
            return False
    if "[role=" in selector:
        try:
            role = selector.split("[role=")[1].split("]")[0].strip("'\"")
            return any((e.get("role") or "") == role for e in els)
        except Exception:
            return False
    # naive class chain
    if "." in selector:
        classes = [c for c in selector.split('.') if c and ('[' not in c)]
        for e in els:
            cls = str(e.get("class") or "").split()
            if all(c in cls for c in classes[1:]):
                return True
    return False


def _visible_ok(el: Dict[str, Any]) -> bool:
    """Approximate visibility check aligned with skill.build._make_skill."""
    if not isinstance(el, dict):
        return False
    vis = el.get("visible_adv")
    if vis is None:
        vis = el.get("visible")
    if not bool(vis):
        return False
    occl = el.get("occlusion_ratio")
    try:
        occ_val = float(occl) if occl is not None else None
    except Exception:
        occ_val = None
    if occ_val is not None and occ_val >= 0.9:
        return False
    in_vp = el.get("in_viewport")
    if in_vp is not None and not bool(in_vp):
        return False
    opacity = el.get("opacity")
    if isinstance(opacity, str) and opacity.strip() == "0":
        return False
    pointer = el.get("pointer_events")
    if isinstance(pointer, str) and pointer.strip().lower() == "none":
        return False
    return True


def _cookie_names_from_artifacts(run: Dict[str, Any]) -> List[str]:
    """Extract cookie names from new_run['cookies'] (cookies.json)."""
    ck = run.get("cookies") or {}
    # 支持 {"cookies":[...]}, {"set":[...]}, 或直接 list[dict]
    if isinstance(ck, dict):
        raw_list = ck.get("cookies") or ck.get("set") or []
    else:
        raw_list = ck
    names: List[str] = []
    seen: set[str] = set()
    for c in raw_list if isinstance(raw_list, list) else []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _infer_login_state_from_cookies(run: Dict[str, Any]) -> str | None:
    """Heuristic login_state from cookies in run artifacts."""
    names = [n.lower() for n in _cookie_names_from_artifacts(run)]
    if not names:
        return None
    hints = ("session", "sess", "sid", "auth", "token", "login", "user", "uid")
    for n in names:
        if any(h in n for h in hints):
            return "logged_in"
    return None


def diagnose(skill: Dict[str, Any], old_run: Dict[str, Any], new_run: Dict[str, Any]) -> Dict[str, Any]:
    locs = skill.get("locators") or {}
    pre = skill.get("preconditions") or {}
    ds_new = new_run.get("dom_summary") or {}
    # check preconditions: exists / visible / enabled / cookies.required_names / login_state
    missing_exists: List[str] = []
    for sel in list(pre.get("exists") or []):
        if not _exists(ds_new, sel):
            missing_exists.append(sel)

    # visible: 对每个 selector 检查新快照下是否满足可见性
    missing_visible: List[str] = []
    for sel in list(pre.get("visible") or []):
        if not sel:
            continue
        el = _find_element_by_selector(ds_new, sel)
        if not el or not _visible_ok(el):
            missing_visible.append(sel)

    # enabled: 当前数据不足以精细判断“禁用”，仅在元素缺失时标记为未满足
    missing_enabled: List[str] = []
    for sel in list(pre.get("enabled") or []):
        if not sel:
            continue
        el = _find_element_by_selector(ds_new, sel)
        if not el:
            missing_enabled.append(sel)

    # cookies.required_names: 基于 cookies.json 中的名称集合判断
    cookies_required: List[str] = []
    try:
        cookies_obj = pre.get("cookies") or {}
        if isinstance(cookies_obj, dict):
            cookies_required = list(cookies_obj.get("required_names") or [])
    except Exception:
        cookies_required = []
    cookies_missing: List[str] = []
    if cookies_required:
        present = set(_cookie_names_from_artifacts(new_run))
        for name in cookies_required:
            if name and name not in present:
                cookies_missing.append(name)

    # login_state: 与新快照推断出的登录态不一致时标记
    login_state_req = pre.get("login_state")
    login_state_new = _infer_login_state_from_cookies(new_run)
    login_state_mismatch = None
    if isinstance(login_state_req, str) and login_state_req:
        # 目前仅在要求 logged_in 且新快照非 logged_in 时视为 mismatch
        if login_state_req == "logged_in" and login_state_new != "logged_in":
            login_state_mismatch = {
                "expected": login_state_req,
                "observed": login_state_new or "unknown",
            }

    prelim_violations: Dict[str, Any] = {}
    if missing_exists:
        prelim_violations["missing_exists"] = missing_exists
    if missing_visible:
        prelim_violations["missing_visible"] = missing_visible
    if missing_enabled:
        prelim_violations["missing_enabled"] = missing_enabled
    if cookies_missing:
        prelim_violations["cookies_missing"] = cookies_missing
    if login_state_mismatch is not None:
        prelim_violations["login_state_mismatch"] = login_state_mismatch

    diff = analyze(skill, old_run, new_run)
    if prelim_violations:
        res = {
            "root_cause": "mismatch",
            "signals": {**prelim_violations, **diff},
            "notes": "preconditions not satisfied on new snapshot",
        }
    else:
        res = {
            "root_cause": "damage",
            "signals": diff,
            "notes": "preconditions satisfied; locators/program may require repair",
        }
    return res


__all__ = ["diagnose"]
