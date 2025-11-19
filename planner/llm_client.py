from __future__ import annotations

"""
planner.llm_client

最小 LLM 调用封装（OpenAI 兼容聊天接口），用于“规划/填参”阶段。

特点：
- 只依赖 planner.config.LLMConfig 读取 base_url/api_key/model 等；
- 提供 complete_json(prompt, system, max_tokens)，尽量约束返回为 JSON；
- 不做复杂重试与流式，只返回一次完整响应；
- 本模块本身不主动在仓库测试中调用网络，真实运行需在联网环境执行。
"""

import json
import time
from typing import Any, Dict, List, Optional

from .config import LLMConfig, get_llm_config


_USAGE_STATS: List[Dict[str, Any]] = []


def _ensure_openai() -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 openai 依赖，请先安装: pip install openai") from e
    return OpenAI


def _safe_json(text: str) -> Any:
    """尽力从文本中提取 JSON 对象。

    优先直接 json.loads；失败则截取首个 '{' 到最后一个 '}' 尝试解析。
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            return json.loads(text[i : j + 1])
    except Exception:
        return None
    return None


def drain_usage_stats() -> List[Dict[str, Any]]:
    """返回并清空当前进程内记录的 LLM usage 统计列表。"""
    global _USAGE_STATS
    stats = _USAGE_STATS
    _USAGE_STATS = []
    return stats


def complete_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    config: Optional[LLMConfig] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """调用 LLM 并尝试将返回解析为 JSON 对象。

    参数：
    - prompt: 用户提示词（通常包含任务描述 + 上下文片段）；
    - system: 可选 system 提示，用于要求“只输出 JSON”等；
    - max_tokens: 补全最大 token 数（不传则用 config.max_tokens）；
    - temperature: 采样温度（不传则用 config.temperature）；
    - config: 可选 LLMConfig；为空时从环境 get_llm_config()；
    - verbose: 是否打印少量日志。

    返回：
    - 解析后的 JSON 对象（dict/list 等）；若无法解析，将抛出 ValueError。
    """
    cfg = config or get_llm_config()
    if not cfg.api_key:
        raise RuntimeError("AFC_LLM_API_KEY 未配置，请在环境变量或 .env 中设置。")

    OpenAI = _ensure_openai()
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)

    sys_msg = system or (
        "You are a JSON API.\n"
        "Always respond with a single valid JSON value (object or array).\n"
        "Do not include any extra text, comments, or markdown."
    )
    temp = float(cfg.temperature if temperature is None else temperature)
    mx = int(cfg.max_tokens if max_tokens is None else max_tokens)

    if verbose:
        print(f"[planner.llm] model={cfg.model} base_url={cfg.base_url or 'openai-default'}")
        print(f"[planner.llm] prompt_chars={len(prompt)} temperature={temp} max_tokens={mx}")

    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ],
        temperature=temp,
        max_tokens=mx,
    )
    t1 = time.perf_counter()
    elapsed_ms = (t1 - t0) * 1000.0
    text = (resp.choices[0].message.content or "").strip()
    usage_info: Dict[str, Any] = {
        "model": cfg.model,
        "elapsed_ms": elapsed_ms,
    }
    if verbose:
        try:
            usage = getattr(resp, "usage", None)
            if usage:
                pt = getattr(usage, "prompt_tokens", None)
                ct = getattr(usage, "completion_tokens", None)
                tt = getattr(usage, "total_tokens", None)
                usage_info.update(
                    {
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "total_tokens": tt,
                    }
                )
                print(
                    f"[planner.llm] completion_len={len(text)} "
                    f"prompt_tokens={pt} "
                    f"completion_tokens={ct}"
                )
            else:
                print(f"[planner.llm] completion_len={len(text)} (usage unavailable)")
        except Exception:
            print(f"[planner.llm] completion_len={len(text)}")
        print(f"[planner.llm] elapsed_ms={elapsed_ms:.1f}")

    _USAGE_STATS.append(usage_info)

    obj = _safe_json(text)
    if obj is None:
        raise ValueError("LLM 响应无法解析为 JSON，对应文本为：\n" + text)
    return obj


__all__ = ["complete_json", "drain_usage_stats"]
