"""
LLM 调用与模板渲染（基于 OpenAI 兼容接口）。

依赖：skill.llm_client（读取环境变量 AFC_LLM_*）。本模块不在此处发起网络访问；
实际运行需在具备网络权限且安装 openai 的环境中执行。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from skill.llm_client import complete_text, complete_text_with_usage, LLMConfig


def render_template(path: str, mapping: Dict[str, Any]) -> str:
    with open(path, "r", encoding="utf-8") as f:
        tpl = f.read()

    # 将复杂对象先 JSON 化（占位符以 *_json 结尾的字段自行预编码传入）
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        val = mapping.get(key, m.group(0))
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    return re.sub(r"\{([A-Za-z0-9_\.]+)\}", repl, tpl)


def call_llm(prompt: str, *, temperature: float = 0.2, max_tokens: Optional[int] = None, verbose: bool = True) -> str:
    cfg = LLMConfig()
    if verbose:
        print(f"[aid.llm] call (chars={len(prompt)})")
    return complete_text(prompt, config=cfg, temperature=temperature, max_tokens=max_tokens, verbose=verbose)


def call_llm_with_usage(prompt: str, *, temperature: float = 0.2, max_tokens: Optional[int] = None, verbose: bool = True):
    cfg = LLMConfig()
    if verbose:
        print(f"[aid.llm] call+usage (chars={len(prompt)})")
    return complete_text_with_usage(prompt, config=cfg, temperature=temperature, max_tokens=max_tokens, verbose=verbose)


def safe_json(text: str) -> Any:
    """Best-effort JSON 解析：截取首个 { 开始到末尾 } 的片段。
    失败返回 None。
    """
    try:
        # 直接尝试
        return json.loads(text)
    except Exception:
        pass
    try:
        i = text.find('{')
        j = text.rfind('}')
        if i >= 0 and j > i:
            return json.loads(text[i:j+1])
    except Exception:
        return None
    return None


__all__ = ["render_template", "call_llm", "call_llm_with_usage", "safe_json"]
