"""
Patch operations for Skill JSON (deterministic, no LLM).

Supports a minimal JSON-Patch-like format with ops:
  - add    {"op":"add", "path":"/a/b/-", "value": X}
  - replace{"op":"replace", "path":"/a/b",   "value": X}
  - remove {"op":"remove", "path":"/a/b/0"}

Notes:
  - Paths use JSON Pointer style; array indices are integers; '-' means append.
  - Missing intermediate dicts will be created for 'add'.
"""

from __future__ import annotations

from typing import Any, Dict, List


class PatchError(Exception):
    pass


def _split_path(path: str) -> List[str]:
    if not path.startswith('/'):
        raise PatchError(f"invalid path: {path}")
    parts = path.split('/')[1:]
    # unescape ~1 -> /, ~0 -> ~
    parts = [p.replace('~1', '/').replace('~0', '~') for p in parts]
    return parts


def _ensure_parent(doc: Any, parts: List[str]) -> (Any, str):
    cur = doc
    for p in parts[:-1]:
        if isinstance(cur, list):
            try:
                i = int(p)
            except Exception as e:
                raise PatchError(f"expected array index, got '{p}'") from e
            if i < 0 or i >= len(cur):
                raise PatchError(f"index out of range: {i}")
            cur = cur[i]
        else:
            if p not in cur or not isinstance(cur[p], (dict, list)):
                cur[p] = {}
            cur = cur[p]
    return cur, parts[-1] if parts else ''


def apply_patch(doc: Dict[str, Any], ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    for op in ops:
        typ = op.get('op')
        path = op.get('path')
        if not isinstance(path, str):
            raise PatchError('path required')
        parts = _split_path(path)
        parent, key = _ensure_parent(doc, parts)

        if typ == 'add':
            val = op.get('value')
            if isinstance(parent, list):
                if key == '-':
                    parent.append(val)
                else:
                    i = int(key)
                    parent.insert(i, val)
            else:
                parent[key] = val

        elif typ == 'replace':
            val = op.get('value')
            if isinstance(parent, list):
                i = int(key)
                if i < 0 or i >= len(parent):
                    raise PatchError(f"index out of range: {i}")
                parent[i] = val
            else:
                parent[key] = val

        elif typ == 'remove':
            if isinstance(parent, list):
                i = int(key)
                if i < 0 or i >= len(parent):
                    raise PatchError(f"index out of range: {i}")
                parent.pop(i)
            else:
                parent.pop(key, None)

        else:
            raise PatchError(f"unsupported op: {typ}")
    return doc


__all__ = ["apply_patch", "PatchError"]

