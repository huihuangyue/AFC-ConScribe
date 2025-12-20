"""
AFC 页面级快照构建模块（单 run_dir 版）。

本模块对应 workspace/AFCdatabase/README.md 中的实现部分：
- 对单个 run_dir（workspace/data/<domain>/<ts>/）抽取 AfcPageSnapshot；
- 暂时只提供函数骨架与类型定义，具体逻辑按 README 中的步骤逐步填充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from skill.llm_client import LLMConfig, complete_text
from .afc_llm_prompts import build_refine_text_prompt


@dataclass
class RawControl:
    """原始控件视图：直接由 controls_tree + dom_summary + ax + skills 聚合而来。"""

    control_id: str
    type: str
    selector: Optional[str]
    bbox: Optional[List[float]]
    action: Optional[str]
    parent: Optional[str]
    children: List[str] = field(default_factory=list)
    visible: Optional[bool] = None

    # 文本 & 角色
    dom_texts: List[str] = field(default_factory=list)
    ax_texts: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    tag_name: Optional[str] = None

# 关联技能（粗粒度）
    skills: List[Dict[str, Any]] = field(default_factory=list)


def _load_run_dir(
    run_dir: Path,
    verbose: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    读取单个 run_dir 下的基础 JSON 产物。

    返回：
        meta, controls_tree, dom_summary, ax, cookies, skills
    """
    if verbose:
        print(f"[afcdb] [_load_run_dir] run_dir={run_dir}")
    if not run_dir.exists():
        raise FileNotFoundError(f"run_dir not found: {run_dir}")

    def _load_json(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    # 基本必需文件：meta / controls_tree
    meta_path = run_dir / "meta.json"
    controls_tree_path = run_dir / "controls_tree.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"meta.json not found in run_dir: {meta_path}")
    if not controls_tree_path.is_file():
        raise FileNotFoundError(f"controls_tree.json not found in run_dir: {controls_tree_path}")

    meta: Dict[str, Any] = _load_json(meta_path)
    controls_tree: Dict[str, Any] = _load_json(controls_tree_path)

    # dom_summary：优先 dom_summary.json，其次 dom_summary_scrolled.json，最后任意 dom_summary*.json
    dom_summary: Dict[str, Any] = {}
    dom_primary = run_dir / "dom_summary.json"
    dom_scrolled = run_dir / "dom_summary_scrolled.json"
    if dom_primary.is_file():
        dom_summary = _load_json(dom_primary)
    elif dom_scrolled.is_file():
        dom_summary = _load_json(dom_scrolled)
    else:
        # 最后尝试匹配其它 dom_summary*.json（如 dom_summary_scrolled_new.json）
        for name in os.listdir(run_dir):
            if name.startswith("dom_summary") and name.endswith(".json"):
                dom_summary = _load_json(run_dir / name)
                break
    if verbose:
        if dom_summary:
            print("[afcdb] [_load_run_dir] dom_summary loaded")
        else:
            print("[afcdb] [_load_run_dir] dom_summary missing or empty")

    # ax.json（可选）
    ax_path = run_dir / "ax.json"
    ax: Dict[str, Any] = _load_json(ax_path) if ax_path.is_file() else {}

    # cookies.json（可选）
    cookies_path = run_dir / "cookies.json"
    cookies: Optional[Dict[str, Any]] = _load_json(cookies_path) if cookies_path.is_file() else None

    # skill/Skill_*.json（位于 skill 子目录中，每个 Skill_*/ 目录下有对应 JSON）
    skills_dir = run_dir / "skill"
    skills: List[Dict[str, Any]] = []
    if skills_dir.is_dir():
        for entry in skills_dir.iterdir():
            if entry.is_dir():
                # 目录形如 Skill_xxx，内部有同名 JSON
                json_path = entry / f"{entry.name}.json"
                if json_path.is_file():
                    try:
                        skills.append(_load_json(json_path))
                    except Exception:
                        # 解析失败时先跳过该技能，后续可以在上层做日志记录
                        continue
            elif entry.is_file() and entry.name.startswith("Skill_") and entry.suffix == ".json":
                # 兼容直接平铺 JSON 的情况
                try:
                    skills.append(_load_json(entry))
                except Exception:
                    continue

    if verbose:
        print(
            f"[afcdb] [_load_run_dir] meta_ok=True, controls_nodes={len(controls_tree.get('nodes') or [])}, "
            f"skills={len(skills)}, cookies={'yes' if cookies is not None else 'no'}"
        )

    return meta, controls_tree, dom_summary, ax, cookies, skills


def _build_raw_controls(
    meta: Dict[str, Any],
    controls_tree: Dict[str, Any],
    dom_summary: Dict[str, Any],
    ax: Dict[str, Any],
    cookies: Optional[Dict[str, Any]],
    skills: List[Dict[str, Any]],
    verbose: bool = False,
) -> List[RawControl]:
    """
    从原始 JSON 结构构建 RawControl 列表。

    纯规则聚合：
    - 遍历 controls_tree.nodes 中 type="control" 的节点；
    - 为每个控件填充基本几何信息；
    - 根据 bbox 与 dom_summary.elements 的重叠，抽取候选文本与 role；
    - 采用简单启发式，把明显命中该控件的技能附加在 RawControl.skills 上。
    """
    nodes = controls_tree.get("nodes") or []
    dom_elements = dom_summary.get("elements") or []

    if verbose:
        print(
            f"[afcdb] [_build_raw_controls] start: control_nodes={len(nodes)}, "
            f"dom_elements={len(dom_elements)}, skills={len(skills)}"
        )

    # 预处理 dom 元素：提取 page_bbox / 文本 / role，便于后面做几何匹配。
    processed_dom: List[Dict[str, Any]] = []
    for el in dom_elements:
        page_bbox = el.get("page_bbox") or el.get("bbox")
        if not (isinstance(page_bbox, list) and len(page_bbox) == 4):
            continue
        processed_dom.append(
            {
                "bbox": page_bbox,
                "text": el.get("text") or "",
                "role": el.get("role"),
                "tag": el.get("tag"),
                "visible": el.get("visible"),
                "in_viewport": el.get("in_viewport"),
            }
        )

    def _bbox_intersection_area(b1: List[float], b2: List[float]) -> float:
        """计算两个 bbox（x, y, w, h）的交集面积。"""
        x1, y1, w1, h1 = b1
        x2, y2, w2, h2 = b2
        x1b, y1b = x1 + w1, y1 + h1
        x2b, y2b = x2 + w2, y2 + h2
        ix1, iy1 = max(x1, x2), max(y1, y2)
        ix2, iy2 = min(x1b, x2b), min(y1b, y2b)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        return float(ix2 - ix1) * float(iy2 - iy1)

    def _match_dom_for_control(ctrl_bbox: Optional[List[float]]) -> Tuple[List[str], List[str], Optional[str], Optional[bool]]:
        """根据 bbox 在 dom_summary 中寻找最相关的几个元素，返回文本列表/角色列表/主 tag/visible。"""
        if not (isinstance(ctrl_bbox, list) and len(ctrl_bbox) == 4):
            return [], [], None, None
        cx, cy, cw, ch = ctrl_bbox
        ctrl_area = max(cw * ch, 1.0)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for el in processed_dom:
            area = _bbox_intersection_area(ctrl_bbox, el["bbox"])
            if area <= 0:
                continue
            # 交集面积占控件面积的比例作为粗略得分
            score = area / ctrl_area
            if score <= 0:
                continue
            scored.append((score, el))
        scored.sort(key=lambda x: x[0], reverse=True)
        # 取前几条作为候选
        top = [el for _, el in scored[:5]]
        texts = [el["text"] for el in top if (el.get("text") or "").strip()]
        roles = [el["role"] for el in top if el.get("role")]
        tag = top[0]["tag"] if top else None
        # 可见性：取任一候选的 visible/in_viewport 为 True 即视为 True
        visible = None
        for el in top:
            if el.get("visible") or el.get("in_viewport"):
                visible = True
                break
        return texts, roles, tag, visible

    def _skill_matches_control(skill: Dict[str, Any], selector: Optional[str], bbox: Optional[List[float]]) -> bool:
        """根据 selector / bbox 做一个非常宽松的匹配，用来挂载相关技能。"""
        loc = skill.get("locators") or {}
        sel = loc.get("selector")
        # selector 字符串粗匹配
        if selector and isinstance(sel, str):
            if selector in sel or sel in selector:
                return True
        # bbox 粗匹配
        skill_bbox = loc.get("bbox")
        if isinstance(skill_bbox, list) and isinstance(bbox, list) and len(skill_bbox) == 4 and len(bbox) == 4:
            if _bbox_intersection_area(skill_bbox, bbox) > 0:
                return True
        return False

    raw_controls: List[RawControl] = []

    for node in nodes:
        if node.get("type") != "control":
            continue
        cid = node.get("id")
        selector = node.get("selector")
        geom = node.get("geom") or {}
        bbox = geom.get("page_bbox") or geom.get("bbox")
        action = node.get("action")
        parent = node.get("parent")
        children = node.get("children") or []

        dom_texts, roles, tag_name, visible = _match_dom_for_control(bbox)

        # 关联命中该控件的技能（粗粒度）
        matched_skills: List[Dict[str, Any]] = []
        for sk in skills:
            try:
                if _skill_matches_control(sk, selector, bbox):
                    matched_skills.append(sk)
            except Exception:
                continue

        raw_controls.append(
            RawControl(
                control_id=str(cid),
                type=str(node.get("type")),
                selector=selector,
                bbox=bbox,
                action=action,
                parent=parent,
                children=list(children),
                visible=visible,
                dom_texts=dom_texts,
                ax_texts=[],
                roles=roles,
                tag_name=tag_name,
                skills=matched_skills,
            )
        )

    if verbose:
        print(f"[afcdb] [_build_raw_controls] built RawControl count={len(raw_controls)}")

    return raw_controls


def _build_afc_control(
    raw: RawControl,
    meta: Dict[str, Any],
    cookies: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    将单个 RawControl 转换为 AfcControl 结构（字典形式）。

    目标结构参考 workspace/AFCdatabase/README.md 中 AfcPageSnapshot/AfcControl 的定义。
    默认会在内部调用“规则 + LLM”组合：
    - 规则层先构建一个粗略的 AfcControl；
    - 然后通过 LLM 对文本/标签等做 refine，得到更稳定的功能签名。
    """
    # 规则层：聚合文本候选
    all_text_chunks: List[str] = []
    for t in raw.dom_texts + raw.ax_texts:
        t = (t or "").strip()
        if t:
            all_text_chunks.append(t)
    raw_text = "\n".join(all_text_chunks)

    def _clean_text_basic(text: str) -> List[str]:
        """非常粗的文本清洗：去掉明显的数字/日期/标点，仅保留可能是功能词的短 token。"""
        if not text:
            return []
        # 替换换行与逗号为空格
        s = re.sub(r"[\n,，]", " ", text)
        # 去掉连续数字/日期样式
        s = re.sub(r"[0-9０-９年月日号天晚间位人次]+", " ", s)
        # 切分并过滤
        tokens: List[str] = []
        for tok in s.split():
            tok = tok.strip()
            if not tok:
                continue
            # 过滤纯数字/长度极短的符号
            if re.fullmatch(r"[0-9.]+", tok):
                continue
            if len(tok) == 1 and not re.match(r"[a-zA-Z0-9一-龥]", tok):
                continue
            tokens.append(tok)
        # 去重但保持顺序
        seen: set[str] = set()
        uniq: List[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq

    clean_text_tokens = _clean_text_basic(raw_text)

    # 规则层：交互类型
    action = raw.action or "none"
    roles = list(raw.roles)
    # 方便后续规则/LLM 使用的派生信息
    roles_lower = {r.lower() for r in roles}
    tag = (raw.tag_name or "").lower()

    # 规则层：结构信息（tree_path 目前留空，后续可以根据 parent 链推导）
    url = str(meta.get("url") or "")
    # 去掉协议与 query，只保留路径部分
    url_no_scheme = url.split("://", 1)[-1] if "://" in url else url
    path_with_query = "/" + url_no_scheme.split("/", 1)[-1] if "/" in url_no_scheme else "/"
    url_path = path_with_query.split("?", 1)[0]
    # 简单 url_pattern：域名 + 首段路径
    domain = str(meta.get("domain") or meta.get("domain_sanitized") or "")
    path_parts = [p for p in url_path.split("/") if p]
    if path_parts:
        url_pattern = f"^https://{domain}/" + path_parts[0] + ".*"
    else:
        url_pattern = f"^https://{domain}/.*"

    # 规则层：环境（从关联技能中粗略抽取）
    login_state: Optional[str] = None
    cookies_required: List[str] = []
    viewport_min: Dict[str, int] = {}
    for sk in raw.skills:
        prec = sk.get("preconditions") or {}
        if not login_state and prec.get("login_state"):
            login_state = str(prec["login_state"])
        cookies_prec = prec.get("cookies") or {}
        names = cookies_prec.get("required_names") or []
        for n in names:
            if n not in cookies_required:
                cookies_required.append(n)
        vp = prec.get("viewport") or {}
        for k in ("min_width", "min_height"):
            if k in vp and k not in viewport_min:
                try:
                    viewport_min[k] = int(vp[k])
                except Exception:
                    continue

    # 规则层：技能链接（只记录 skill id + preconditions 的子集，避免结构过重）
    skill_links: List[Dict[str, Any]] = []
    for sk in raw.skills:
        link: Dict[str, Any] = {}
        if "id" in sk:
            link["skill_id"] = sk["id"]
        if "action" in sk:
            link["skill_action"] = sk["action"]
        prec = sk.get("preconditions") or {}
        if prec:
            link["preconditions_used"] = {
                k: v
                for k, v in prec.items()
                if k in ("url_matches", "login_state", "cookies")
            }
        if link:
            skill_links.append(link)

    # 初步 AfcControl（LLM refine 前的粗版本）
    afc: Dict[str, Any] = {
        "control_id": raw.control_id,
        "type": raw.type,
        "action": action,
        "semantic_signature": {
            "raw_text": raw_text,
            "clean_text": clean_text_tokens,
            "role": roles,
            "form_context": [],
            "url_path": url_path,
            "url_pattern": url_pattern,
            "login_state": login_state or "unknown",
            "cookies_required": cookies_required,
            "viewport_min": viewport_min,
        },
        "structural_signature": {
            "selector_candidates": [raw.selector] if raw.selector else [],
            "tree_path": [],
            "bbox": raw.bbox,
            "visibility": {
                "visible": raw.visible,
            },
        },
        "skill_links": skill_links,
    }

    # LLM refine：文本/标签（可配置仅对候选控件调用）
    def _is_llm_candidate() -> bool:
        """启发式判断该控件是否需要调用 LLM 做精细归类。

        目前策略：明显的按钮/输入/链接才作为 LLM 重点处理对象。
        """
        # 根据 action 粗判
        act = (action or "").lower()
        if act in ("click", "navigate", "type", "input", "change", "submit"):
            return True
        # 根据 tag/role 粗判
        if tag in ("button", "a", "input", "select", "textarea"):
            return True
        if roles_lower & {"button", "link", "textbox", "searchbox"}:
            return True
        # 根据文本关键词粗判
        tokens = [t.lower() for t in clean_text_tokens]
        kw_click = ("搜索", "查找", "submit", "search", "查询", "确认", "确定", "下一步", "登录", "登錄", "login")
        if any(k.lower() in "".join(tokens) for k in kw_click):
            return True
        return False

    def _is_marketing_control(text_all: str, text_lower: str) -> bool:
        """粗略识别“营销卡片/促销链接”类控件，用于细分 norm_label。

        目标是把明显的营销/推荐卡片从 Clickable_Submit / Link_Navigate 中剥离出来，
        归为 Clickable_MarketingCard，而不是影响真正的提交按钮。
        """
        # 结构上通常是可点击的链接或按钮
        if not (tag in ("a", "button") or roles_lower & {"link", "button"}):
            return False

        # 文本关键词：站点无关 + ctrip 特有的一些词
        kw_zh = ("携程旅行保障", "放心住", "放心飞", "广告", "推荐", "优惠", "特价", "促销")
        kw_en = ("promotion", "promo", "deal", "discount", "offer", "sale")
        if any(k in text_all for k in kw_zh):
            return True
        if any(k in text_lower for k in kw_en):
            return True

        # 选择器中常见的营销/广告类命名（尽量保守，只加少数已确认的模式）
        sel = (raw.selector or "").lower()
        if any(k in sel for k in ("psf_item_link", "banner", "promo", "ad_")):
            return True

        return False

    data = {}
    # 环境变量 AFC_AFCDB_LLM_ONLY_CANDIDATES 控制是否只对候选控件调用 LLM
    # 默认改为 False：为了提高归类率，默认对所有控件尝试 LLM（你可以在本地设为 1 以减少调用量）。
    only_cand_env = os.getenv("AFC_AFCDB_LLM_ONLY_CANDIDATES", "0").strip().lower()
    only_candidates = only_cand_env in ("1", "true", "yes", "on")
    need_llm = (not only_candidates) or _is_llm_candidate()

    if need_llm:
        try:
            cfg = LLMConfig()
            prompt = build_refine_text_prompt(raw, afc)
            resp = complete_text(prompt, config=cfg, temperature=0.0, max_tokens=512, verbose=False)
            # debug：可选记录原始 LLM 输出，方便后续调试 prompt
            debug_dir = os.getenv("AFC_AFCDB_LLM_DEBUG_DIR", "").strip()
            if debug_dir:
                try:
                    dbg_path = Path(debug_dir)
                    dbg_path.mkdir(parents=True, exist_ok=True)
                    log_obj = {
                        "control_id": raw.control_id,
                        "selector": raw.selector,
                        "raw_text_snippet": raw_text[:500],
                        "clean_text_draft": clean_text_tokens,
                        "llm_response": resp,
                    }
                    (dbg_path / f"control_{raw.control_id}.json").write_text(
                        json.dumps(log_obj, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            # 约定 LLM 返回 JSON 字符串
            data = json.loads(resp)
        except Exception:
            data = {}

    if isinstance(data, dict):
        sig = afc["semantic_signature"]
        logical_name = data.get("logical_name")
        if isinstance(logical_name, str) and logical_name.strip():
            afc["logical_name"] = logical_name.strip()
        norm_label = data.get("norm_label")
        if isinstance(norm_label, str) and norm_label.strip():
            sig["norm_label"] = norm_label.strip()
        clean_tokens = data.get("clean_text") or data.get("clean_text_tokens")
        if isinstance(clean_tokens, list):
            sig["clean_text"] = [str(t).strip() for t in clean_tokens if str(t).strip()]
        semantic_text = data.get("semantic_text")
        if isinstance(semantic_text, str) and semantic_text.strip():
            sig["semantic_text"] = semantic_text.strip()

    # 若 LLM 将明显的营销卡片误归为 Submit/普通链接，则做一次纠偏
    sig = afc["semantic_signature"]
    norm_after_llm = (sig.get("norm_label") or "").strip()
    if norm_after_llm:
        text_all_llm = "".join(sig.get("clean_text") or clean_text_tokens)
        text_lower_llm = text_all_llm.lower()
        if norm_after_llm in ("Clickable_Submit", "Link_Navigate") and _is_marketing_control(
            text_all_llm, text_lower_llm
        ):
            sig["norm_label"] = "Clickable_MarketingCard"

    # 规则 fallback：若 LLM 未给出 norm_label，则根据关键词/角色补一个粗标签
    sig = afc["semantic_signature"]
    if not sig.get("norm_label"):
        text_all = "".join(clean_text_tokens)
        text_lower = text_all.lower()
        # 先识别明显的营销卡片类控件
        if _is_marketing_control(text_all, text_lower):
            sig["norm_label"] = "Clickable_MarketingCard"
        # 登录相关
        elif any(k in text_all for k in ("登录", "登錄", "登入")) or "login" in text_lower:
            sig["norm_label"] = "Clickable_Login"
        # 搜索/提交相关
        elif any(k in text_all for k in ("搜索", "查找", "查询")) or any(k in text_lower for k in ("search", "submit", "go")):
            if roles_lower & {"textbox", "searchbox"} or tag in ("input", "textarea"):
                sig["norm_label"] = "Editable_SearchBox"
            else:
                sig["norm_label"] = "Clickable_Submit"
        # 明显输入框
        elif tag in ("input", "textarea") or roles_lower & {"textbox"}:
            sig["norm_label"] = "Editable_Textfield"
        # 明显链接
        elif tag == "a" or roles_lower & {"link"}:
            sig["norm_label"] = "Link_Navigate"
        else:
            # 保留 UnknownLabel：确实看不出功能类别的控件，作为“暂未理解”的标记。
            # 通过前面的规则和 LLM，我们尽量让 UnknownLabel 的比例下降，但不会用改名来隐藏它。
            sig["norm_label"] = "UnknownLabel"

    # 规则层：根据 norm_label + 文本，为后续抽象技能聚合提供一个初步的 task_group/task_role 提示。
    # 注意：
    #   - 这里只做“弱标签”，只在 semantic_signature 中尚未设置 task_group/task_role 时才填入；
    #   - LLM 抽象层（LLM_abstract_skill）仍然可以在此基础上做进一步 refine。
    tg = (sig.get("task_group") or "").strip()
    tr = (sig.get("task_role") or "").strip()
    if not tg or not tr:
        norm = (sig.get("norm_label") or "").strip()
        text_all = "".join(sig.get("clean_text") or clean_text_tokens)
        text_lower = text_all.lower()

        # 登录 / 退出 / 注册等认证相关控件
        if norm == "Clickable_Login" or any(k in text_all for k in ("登录", "登錄", "登入")) or "login" in text_lower:
            tg = tg or "Auth"
            tr = tr or "Login"
        # 搜索框 / 搜索提交按钮
        elif norm in ("Editable_SearchBox", "Clickable_Submit") and (
            any(k in text_all for k in ("搜索", "查找", "查询"))
            or "search" in text_lower
        ):
            tg = tg or "Search"
            if norm == "Editable_SearchBox":
                tr = tr or "EnterQuery"
            else:
                tr = tr or "Submit"
        # 明显的营销/推荐卡片
        elif norm == "Clickable_MarketingCard":
            tg = tg or "Marketing"
            tr = tr or "ViewCard"
        # 一般导航链接
        elif norm == "Link_Navigate":
            tg = tg or "Navigation"
            tr = tr or "Navigate"

        if tg:
            sig["task_group"] = tg
        if tr:
            sig["task_role"] = tr

    return afc


def _save_snapshot(
    run_dir: Path,
    meta: Dict[str, Any],
    afc_controls: List[Dict[str, Any]],
    verbose: bool = False,
) -> Path:
    """
    将 AfcPageSnapshot 写入 run_dir/afc/afc_page_snapshot.json。

    顶层结构参考 workspace/AFCdatabase/README.md：
    {
      "run_dir": str,
      "domain": str,
      "url": str,
      "viewport": {...},
      "generated_at": str,
      "controls": [...]
    }
    """
    afc_dir = run_dir / "afc"
    afc_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = afc_dir / "afc_page_snapshot.json"
    # 如果之前已经存在同名快照，先删除，保证当前结果完全由本次运行生成
    if snapshot_path.exists():
        if verbose:
            print(f"[afcdb] [_save_snapshot] remove existing snapshot {snapshot_path}")
        try:
            snapshot_path.unlink()
        except Exception:
            # 若删除失败，不影响后续覆盖写入
            pass

    # 基本元信息
    url = meta.get("url") or ""
    domain = meta.get("domain") or meta.get("domain_sanitized") or ""
    viewport = meta.get("viewport") or {}
    # 生成时间：优先使用 meta.timestamp，其次当前时间
    generated_at = meta.get("timestamp")
    if not generated_at:
        from datetime import datetime

        generated_at = datetime.utcnow().isoformat() + "Z"

    snapshot: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "domain": domain,
        "url": url,
        "viewport": viewport,
        "generated_at": generated_at,
        "controls": afc_controls,
    }

    # 写盘
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if verbose:
        print(f"[afcdb] [_save_snapshot] wrote snapshot to {snapshot_path}")

    # 额外写一份到“网址文件夹”（run_dir 的父目录）下，便于按站点聚合：
    # workspace/data/<domain>/afc/<ts>__afc_page_snapshot.json
    try:
        domain_dir = run_dir.parent
        domain_afc_dir = domain_dir / "afc"
        domain_afc_dir.mkdir(parents=True, exist_ok=True)
        domain_snapshot_path = domain_afc_dir / f"{run_dir.name}__afc_page_snapshot.json"
        domain_snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if verbose:
            print(f"[afcdb] [_save_snapshot] wrote domain-level snapshot to {domain_snapshot_path}")
    except Exception as e:  # pragma: no cover - 写额外副本失败不算致命
        if verbose:
            print(f"[afcdb] [_save_snapshot] WARN: failed to write domain-level snapshot: {e}")

    return snapshot_path


def _clean_afc_outputs(run_dir: Path, *, verbose: bool = False) -> None:
    """针对已运行过的 run_dir，先清理旧的 AFC 输出，再重新构建。

    当前只删除我们在 AFCdatabaseBuild 中生成的标准产物，不动 detect/skill 的原始文件：
    - afc/afc_page_snapshot.json
    - afc/afc_skill_snapshot.json（预留给后续技能抽象）
    """
    afc_dir = run_dir / "afc"
    if not afc_dir.is_dir():
        return
    targets = ["afc_page_snapshot.json", "afc_skill_snapshot.json"]
    removed = []
    for name in targets:
        p = afc_dir / name
        if p.is_file():
            try:
                p.unlink()
                removed.append(name)
            except Exception:
                continue
    if verbose and removed:
        print(f"[afcdb] [_clean_afc_outputs] removed old files in {afc_dir}: {', '.join(removed)}")


def build_page_snapshot(run_dir: str | Path, verbose: bool | None = None) -> Path:
    """
    给定一个 run_dir（workspace/data/<domain>/<ts>/），读取 Detect + Skill 产物，构建 AfcPageSnapshot，
    将 JSON 写为 run_dir/afc/afc_page_snapshot.json，并返回该路径。

    默认行为（force=False）：
    - 若已存在完整的 afc_page_snapshot.json（所有 type=\"control\" 的节点都有对应 AfcControl），则直接复用并给出提示；
    - 若存在部分结果（只覆盖到了部分控件），则在此基础上“接着跑”：复用已有 AfcControl，仅对缺失的控件补齐，然后写回完整快照。

    仅当 force=True 时：
    - 会先清理旧的 AFC 输出文件，再从头计算所有控件的 AfcControl。

    verbose:
        - None：通过环境变量 AFC_AFCDB_VERBOSE 控制（1/true/yes/on 为 True），默认 False；
        - True/False：显式开启/关闭控制台打印。
    """
    run_dir = Path(run_dir)

    if verbose is None:
        v = os.getenv("AFC_AFCDB_VERBOSE", "").strip().lower()
        verbose_flag = v in ("1", "true", "yes", "on")
    else:
        verbose_flag = bool(verbose)

    # 可选强制重建标志：AFC_AFCDB_FORCE_REBUILD 或调用方显式传参（暂保留环境变量入口）
    # 为了兼容旧调用，这里从环境变量读取；后续可以在函数签名中加入 force 参数。
    force_env = os.getenv("AFC_AFCDB_FORCE_REBUILD", "").strip().lower()
    force_rebuild = force_env in ("1", "true", "yes", "on")

    if verbose_flag:
        print(f"[afcdb] [build_page_snapshot] start run_dir={run_dir}")

    # 读取原始 run_dir 产物（meta / controls_tree / dom / skills 等）
    meta, controls_tree, dom, ax, cookies, skills = _load_run_dir(run_dir, verbose=verbose_flag)
    raw_controls = _build_raw_controls(meta, controls_tree, dom, ax, cookies, skills, verbose=verbose_flag)
    total = len(raw_controls)

    # 预加载已有快照（如存在），用于“完整即跳过 / 部分则复用”
    afc_dir = run_dir / "afc"
    snapshot_path = afc_dir / "afc_page_snapshot.json"
    existing_controls_by_id: Dict[str, Dict[str, Any]] = {}
    existing_complete = False

    # 计算预期的 control_id 集合
    expected_ids: set[str] = set()
    for rc in raw_controls:
        expected_ids.add(rc.control_id)

    if snapshot_path.is_file() and not force_rebuild:
        try:
            snap_obj = json.loads(snapshot_path.read_text("utf-8"))
            existing_controls: List[Dict[str, Any]] = snap_obj.get("controls") or []
            for c in existing_controls:
                cid = c.get("control_id")
                if isinstance(cid, str):
                    existing_controls_by_id[cid] = c
            existing_ids = set(existing_controls_by_id.keys())
            if expected_ids.issubset(existing_ids):
                existing_complete = True
        except Exception:
            existing_controls_by_id = {}
            existing_complete = False

    if existing_complete and not force_rebuild:
        if verbose_flag:
            print(
                f"[afcdb] [build_page_snapshot] existing snapshot is complete "
                f"(controls={len(existing_controls_by_id)}), skip rebuild."
            )
        return snapshot_path

    # 若需要强制重建，则清理旧产物
    if force_rebuild:
        _clean_afc_outputs(run_dir, verbose=verbose_flag)
        existing_controls_by_id = {}

    # 构建/复用 AfcControls（支持并行计算缺失部分）
    # 1) 先准备一个按 control_id 排序的 RawControl 列表，方便稳定输出顺序
    rc_by_id: Dict[str, RawControl] = {rc.control_id: rc for rc in raw_controls}
    ordered_ids: List[str] = [rc.control_id for rc in raw_controls]

    # 2) 找出需要新计算的控件
    to_compute_ids: List[str] = []
    for cid in ordered_ids:
        if cid not in existing_controls_by_id or force_rebuild:
            to_compute_ids.append(cid)

    if verbose_flag:
        print(
            f"[afcdb] [build_page_snapshot] building AfcControls: "
            f"total={total}, reuse_existing={len(existing_controls_by_id)}, to_compute={len(to_compute_ids)}"
        )

    # 3) 并行计算缺失的 AfcControl
    computed_by_id: Dict[str, Dict[str, Any]] = {}
    # 并发度从环境变量 AFC_AFCDB_MAX_WORKERS 控制，默认最多 5 个并发 worker
    try:
        max_workers_env = int(os.getenv("AFC_AFCDB_MAX_WORKERS", "0") or "0")
    except Exception:
        max_workers_env = 0
    if max_workers_env <= 0:
        # 默认：不配置时，使用不超过 5 个并发 worker
        cpu_cnt = os.cpu_count() or 1
        max_workers = min(5, max(1, cpu_cnt))
    else:
        max_workers = max(1, max_workers_env)

    if to_compute_ids:
        if verbose_flag:
            print(f"[afcdb] [build_page_snapshot] using max_workers={max_workers} for LLM/refine tasks")

        def _worker(rc: RawControl) -> Tuple[str, Dict[str, Any]]:
            afc = _build_afc_control(rc, meta, cookies)
            return rc.control_id, afc

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(_worker, rc_by_id[cid]): cid
                for cid in to_compute_ids
            }
            completed = 0
            for fut in as_completed(future_to_id):
                cid = future_to_id[fut]
                try:
                    cid2, afc = fut.result()
                    computed_by_id[cid2] = afc
                except Exception as e:  # pragma: no cover
                    if verbose_flag:
                        print(f"[afcdb] [build_page_snapshot] ERROR computing control {cid}: {e}")
                    # 出错时，该控件就留空（后续可以根据需要决定是否抛出）
                    continue
                completed += 1
                if verbose_flag:
                    print(
                        f"[afcdb] [build_page_snapshot] built AfcControl {completed}/{len(to_compute_ids)} "
                        f"(parallel batch)"
                    )
                # 可选：每完成一批就增量写盘，这里先省略，最终一次性写完整列表

    # 4) 组装最终 AfcControls，优先复用 existing，其次用并行结果
    afc_controls: List[Dict[str, Any]] = []
    for cid in ordered_ids:
        if cid in existing_controls_by_id and not force_rebuild:
            afc = existing_controls_by_id[cid]
        else:
            afc = computed_by_id.get(cid)
            if afc is None:
                # 理论上不应发生；为安全起见，跳过或构造最小结构
                afc = {
                    "control_id": cid,
                    "type": rc_by_id[cid].type,
                    "action": rc_by_id[cid].action or "none",
                }
        afc_controls.append(afc)

    # 最终写盘
    snapshot_path = _save_snapshot(run_dir, meta, afc_controls, verbose=verbose_flag)
    if verbose_flag:
        print(f"[afcdb] [build_page_snapshot] done, snapshot_path={snapshot_path}")
    return snapshot_path
