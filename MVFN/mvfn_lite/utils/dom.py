"""DOM/AX 适配层（占位）。

提供读取/简化 DOM 与 AX 的工具函数（可按页面采集规范实现）。
"""

from typing import Any, Dict, List


def load_dom_simple(path: str) -> List[Dict[str, Any]]:
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ax_tree(path: str) -> List[Dict[str, Any]]:
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

