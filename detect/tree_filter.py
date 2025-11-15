from __future__ import annotations

"""
tree_filter

对 controls_tree.json 做尺寸与数量层面的筛选（非破坏性，支持就地或写副本）：
 - 过大的节点（相对视口占比过高）剔除；
 - 过小的节点剔除；
 - 每个父节点下过多的小节点做上限裁剪（优先保留 control，且面积更大的优先）；
 - 例外保留：action=submit 的按钮与可填充控件（input/textarea 等）可豁免小尺寸剔除（可关）。

用法：
  python -m detect.tree_filter --dir workspace/data/<domain>/<ts> \
    --min-w 96 --min-h 80 --min-area 20000 --max-area-ratio 0.6 \
    --cap-small-per-parent 12 --keep-important --in-place

默认输出：controls_tree.filtered.json（若 --in-place 则覆盖原文件并写备份副本 .bak）。
"""

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple

try:  # 包内相对导入优先
    from .constants import ARTIFACTS, DEFAULT_VIEWPORT  # type: ignore
except Exception:  # 兼容脚本运行
    from constants import ARTIFACTS, DEFAULT_VIEWPORT  # type: ignore


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _viewport(out_dir: str) -> Tuple[int, int]:
    meta = _read_json(os.path.join(out_dir, ARTIFACTS["meta"]))
    vp = meta.get("viewport") or DEFAULT_VIEWPORT
    try:
        return int(vp.get("width", 1280)), int(vp.get("height", 800))
    except Exception:
        return 1280, 800


def _bbox(node: Dict[str, Any]) -> Tuple[int, int, int, int]:
    g = node.get("geom") or {}
    bb = g.get("bbox") or [0, 0, 0, 0]
    try:
        return int(bb[0] or 0), int(bb[1] or 0), int(bb[2] or 0), int(bb[3] or 0)
    except Exception:
        return 0, 0, 0, 0


def _is_important(node: Dict[str, Any]) -> bool:
    # 重要节点：action=submit 或 control 类型节点
    act = (node.get("action") or "").lower()
    if act == "submit":
        return True
    if node.get("type") == "control":
        return True
    return False


def _filter_by_size(nodes: List[Dict[str, Any]], *, vw: int, vh: int,
                    min_w: int, min_h: int, min_area: int, max_area_ratio: float,
                    keep_important: bool = True) -> List[Dict[str, Any]]:
    max_area = max(1, int(vw * vh * float(max_area_ratio)))
    out: List[Dict[str, Any]] = []
    for n in nodes:
        x, y, w, h = _bbox(n)
        area = w * h
        if keep_important and _is_important(n):
            out.append(n)
            continue
        if w <= 0 or h <= 0:
            continue
        if w < int(min_w) or h < int(min_h):
            continue
        if area < int(min_area):
            continue
        if area >= max_area:
            continue
        # 极端长宽比过滤（可选）
        ratio = w / max(1, h)
        if ratio > 10 or (1 / ratio) > 10:
            continue
        out.append(n)
    return out


def _cap_small_children(nodes: List[Dict[str, Any]], *, per_parent_cap: int, small_area_thresh: int) -> List[Dict[str, Any]]:
    # 按 parent 分组，小面积节点超过上限时做裁剪（保留 control 优先 + 面积大者优先）
    by_id = {str(n.get("id")): n for n in nodes}
    by_parent: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for n in nodes:
        pid = n.get("parent")
        by_parent.setdefault(pid, []).append(n)
    kept_ids = set()
    for pid, lst in by_parent.items():
        small = []
        large = []
        for n in lst:
            _, _, w, h = _bbox(n)
            if (w * h) < int(small_area_thresh):
                small.append(n)
            else:
                large.append(n)
        # 保留所有大节点
        for n in large:
            kept_ids.add(str(n.get("id")))
        # 小节点按优先级排序：control 优先，其次面积大
        small.sort(key=lambda nn: (0 if nn.get("type") == "control" else 1, -(_bbox(nn)[2] * _bbox(nn)[3])), reverse=False)
        cap = max(0, int(per_parent_cap))
        for n in small[:cap]:
            kept_ids.add(str(n.get("id")))
    # 重新收集
    return [n for n in nodes if str(n.get("id")) in kept_ids]


def _rebuild_tree(tree: Dict[str, Any], kept_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id_old = {str(n.get("id")): n for n in (tree.get("nodes") or [])}
    kept_ids = set(str(n.get("id")) for n in kept_nodes)
    # 重建 children
    by_id_new: Dict[str, Dict[str, Any]] = {}
    for n in kept_nodes:
        nid = str(n.get("id"))
        # 复制以免修改原对象
        nn = json.loads(json.dumps(n))
        ch = [cid for cid in (nn.get("children") or []) if str(cid) in kept_ids]
        nn["children"] = ch
        # 若 parent 不在 kept，置为 None
        pid = nn.get("parent")
        if pid is not None and str(pid) not in kept_ids:
            nn["parent"] = None
        by_id_new[nid] = nn
    # roots
    roots = [nid for nid, nn in by_id_new.items() if nn.get("parent") is None]
    new_nodes = [by_id_new[nid] for nid in by_id_new]
    # meta
    meta = tree.get("meta") or {}
    meta["count"] = len(new_nodes)
    meta["control_count"] = sum(1 for n in new_nodes if n.get("type") == "control")
    meta["content_count"] = sum(1 for n in new_nodes if n.get("type") == "content")
    res = {
        "meta": meta,
        "nodes": new_nodes,
        "roots": roots,
    }
    return res


def filter_controls_tree(out_dir: str, *,
                         min_w: int = 96, min_h: int = 80, min_area: int = 20000,
                         max_area_ratio: float = 0.6,
                         cap_small_per_parent: int = 12,
                         keep_important: bool = True,
                         in_place: bool = False) -> str:
    tree_path = os.path.join(out_dir, ARTIFACTS["controls_tree"])
    tree = _read_json(tree_path)
    nodes = [n for n in (tree.get("nodes") or []) if isinstance(n, dict)]
    if not nodes:
        raise RuntimeError("controls_tree.json nodes 为空或文件不存在")
    vw, vh = _viewport(out_dir)
    # 1) 尺寸筛选
    nodes_sz = _filter_by_size(nodes, vw=vw, vh=vh,
                               min_w=int(min_w), min_h=int(min_h), min_area=int(min_area),
                               max_area_ratio=float(max_area_ratio), keep_important=bool(keep_important))
    # 2) 每个父节点小节点上限裁剪
    nodes_cap = _cap_small_children(nodes_sz, per_parent_cap=int(cap_small_per_parent), small_area_thresh=int(min_area))
    # 3) 重建树并落盘
    new_tree = _rebuild_tree(tree, nodes_cap)
    if in_place:
        # 备份
        try:
            bk = tree_path + ".bak"
            if not os.path.exists(bk):
                _write_json(bk, tree)
        except Exception:
            pass
        _write_json(tree_path, new_tree)
        return tree_path
    else:
        out_path = os.path.join(out_dir, "controls_tree.filtered.json")
        _write_json(out_path, new_tree)
        return out_path


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Filter controls_tree.json by size and cap small children per parent")
    ap.add_argument("--dir", required=True, help="Run directory: workspace/data/<domain>/<ts>")
    ap.add_argument("--min-w", type=int, default=96)
    ap.add_argument("--min-h", type=int, default=80)
    ap.add_argument("--min-area", type=int, default=20000)
    ap.add_argument("--max-area-ratio", type=float, default=0.6)
    ap.add_argument("--cap-small-per-parent", type=int, default=12)
    ap.add_argument("--no-keep-important", dest="keep_important", action="store_false")
    ap.add_argument("--in-place", action="store_true")
    args = ap.parse_args()
    out = filter_controls_tree(
        args.dir,
        min_w=args.min_w,
        min_h=args.min_h,
        min_area=args.min_area,
        max_area_ratio=args.max_area_ratio,
        cap_small_per_parent=args.cap_small_per_parent,
        keep_important=getattr(args, "keep_important", True),
        in_place=args.in_place,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

