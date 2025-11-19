from __future__ import annotations

"""
planner.config

集中管理与 LLM/调度相关的基础配置。
当前阶段只定义结构与环境变量读取，不实际发起请求。
"""

import os
from dataclasses import dataclass
from typing import Optional


def _load_dotenv_if_needed() -> None:
    """尽力从 .env 文件加载 AFC_* 相关环境变量。

    行为与 skill.llm_client 中的加载逻辑保持一致：
    - AFC_ENV_FILE 指定路径优先；
    - 其次是 CWD/.env；
    - 再其次是仓库根目录的 .env。
    不覆盖已经存在于 os.environ 的变量。
    """

    def _try(path: str) -> None:
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

    af = os.getenv("AFC_ENV_FILE", "").strip()
    if af:
        _try(af)
    _try(os.path.join(os.getcwd(), ".env"))
    here = os.path.dirname(__file__)
    repo_env = os.path.abspath(os.path.join(here, os.pardir, ".env"))
    _try(repo_env)


@dataclass
class LLMConfig:
    """LLM 基础配置（从环境变量读取，后续由 planner.llm_client 使用）。"""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """从环境变量构造配置，给出合理缺省值。"""
        _load_dotenv_if_needed()
        base_url = os.getenv("AFC_LLM_BASE_URL", "").strip()
        api_key = os.getenv("AFC_LLM_API_KEY", "").strip()
        model = os.getenv("AFC_LLM_MODEL", "").strip() or "gpt-4.1-mini"
        try:
            temperature = float(os.getenv("AFC_LLM_TEMPERATURE", "0.2"))
        except ValueError:
            temperature = 0.2
        try:
            max_tokens = int(os.getenv("AFC_LLM_MAX_TOKENS", "1024"))
        except ValueError:
            max_tokens = 1024
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def get_llm_config() -> LLMConfig:
    """便捷函数：获取当前环境下的 LLMConfig。"""
    return LLMConfig.from_env()
