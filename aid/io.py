"""
I/O helpers for AID pipeline (no network, no LLM).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_run_artifacts(run_dir: str) -> Dict[str, Any]:
    def try_load(name: str) -> Any:
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            try:
                return read_json(p)
            except Exception:
                return {}
        return {}

    return {
        "controls_tree": try_load("controls_tree.json"),
        "dom_summary": try_load("dom_summary.json"),
        "ax": try_load("ax.json"),
        "meta": try_load("meta.json"),
        # 可选 cookies.json（由 detect 层在 export_cookies=True 时写入）
        "cookies": try_load("cookies.json"),
        "snippets_index": try_load(os.path.join("snippets", "index.json")) or {},
    }


__all__ = ["read_json", "write_json", "load_run_artifacts"]
