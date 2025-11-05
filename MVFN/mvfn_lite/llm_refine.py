"""可选：LLM 纠偏模块。

作用：当规则+打分的置信度较低时，将候选摘要送入外接 LLM 做轻量校正。
输入：候选摘要、当前判定。
输出：修正后的 label/action 或置信度调整。
依赖：python-dotenv, tenacity, 可选 openai 或 httpx。
"""

from typing import Dict, Any
import os


def refine_if_needed(summary: str, current: Dict[str, Any]) -> Dict[str, Any]:
    provider = os.getenv("AFC_LLM_PROVIDER")
    if not provider:
        return current
    # 占位：保留原值。接入后可用 OpenAI 或自建 API 做轻量判断修正。
    return current

