"""
LLM 客户端封装（OpenAI 兼容）。

环境变量（参见 .env.example）：
- AFC_LLM_PROVIDER: openai|azure|other（OpenAI 兼容）
- AFC_LLM_MODEL: 模型名（如 gpt-4o-mini）
- AFC_LLM_API_KEY: API Key
- AFC_LLM_BASE_URL: 自定义 Base URL（可选，用于 OpenAI 兼容网关）
- AFC_LLM_API_VERSION: Azure OpenAI 版本（仅 provider=azure 时可用）

说明：本模块不在此处发起任何网络调用，调用方负责在上层使用时提供正确的环境并承载网络权限。
"""

from __future__ import annotations

import os
from typing import Optional, Tuple, Any, Dict
import time


def _load_dotenv_if_needed() -> None:
    """Best-effort 加载 .env 文件到进程环境（仅当变量尚未设置）。

    查找顺序：
    1) 环境变量 AFC_ENV_FILE 指定的路径
    2) CWD/.env
    3) 仓库根目录的 .env（推断为 <this file>/../.env）
    仅解析 KEY=VALUE 形式；忽略注释与空行；不覆盖已存在的环境变量。
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

    # 1) explicit
    af = os.environ.get("AFC_ENV_FILE", "").strip()
    if af:
        _try(af)
    # 2) cwd
    _try(os.path.join(os.getcwd(), ".env"))
    # 3) repo root (…/skill/ -> …/.env)
    here = os.path.dirname(__file__)
    repo_env = os.path.abspath(os.path.join(here, os.pardir, ".env"))
    _try(repo_env)


class LLMConfig:
    def __init__(self) -> None:
        # 首先尝试加载 .env
        _load_dotenv_if_needed()
        self.provider = os.environ.get("AFC_LLM_PROVIDER", "openai").strip() or "openai"
        self.model = os.environ.get("AFC_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        self.api_key = os.environ.get("AFC_LLM_API_KEY", "").strip()
        self.base_url = os.environ.get("AFC_LLM_BASE_URL", "").strip() or None
        self.api_version = os.environ.get("AFC_LLM_API_VERSION", "").strip() or None
        # 可调的健壮性参数
        try:
            self.max_retries = int(os.environ.get("AFC_LLM_MAX_RETRIES", "2"))
        except Exception:
            self.max_retries = 2
        try:
            self.retry_base_sec = float(os.environ.get("AFC_LLM_RETRY_BASE_SEC", "1.5"))
        except Exception:
            self.retry_base_sec = 1.5
        try:
            self.request_timeout = float(os.environ.get("AFC_LLM_REQUEST_TIMEOUT", "60"))
        except Exception:
            self.request_timeout = 60.0

    def validate(self) -> None:
        if not self.api_key:
            raise RuntimeError("AFC_LLM_API_KEY 未配置")


def complete_text(prompt: str, *, config: Optional[LLMConfig] = None, temperature: float = 0.2, max_tokens: int | None = None, verbose: bool = True) -> str:
    """使用 OpenAI 兼容接口完成文本到文本（返回字符串）。

    注意：调用者需保证运行环境已安装 openai 包，且具备网络权限。
    """
    cfg = config or LLMConfig()
    cfg.validate()

    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 openai 依赖，请先安装: pip install openai") from e

    if verbose:
        print(f"[llm] provider={cfg.provider} model={cfg.model} base_url={cfg.base_url or 'openai-default'}")
        print(f"[llm] prompt_chars={len(prompt)} temperature={temperature} max_tokens={max_tokens}")
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    err: Optional[Exception] = None
    for i in range(max(1, cfg.max_retries)):
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": "You are a helpful coding assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=cfg.request_timeout,
            )
            text = (resp.choices[0].message.content or "").strip()
            if verbose:
                print(f"[llm] completion_len={len(text)} attempt={i+1}")
            return text
        except Exception as e:
            err = e
            if i + 1 >= max(1, cfg.max_retries):
                break
            backoff = cfg.retry_base_sec * (2 ** i)
            if verbose:
                print(f"[llm] error={type(e).__name__}: {e}; retry in {backoff:.1f}s …")
            time.sleep(backoff)
    assert err is not None
    raise err


def complete_text_with_usage(prompt: str, *, config: Optional[LLMConfig] = None, temperature: float = 0.2, max_tokens: int | None = None, verbose: bool = True) -> Tuple[str, Dict[str, Any]]:
    """与 complete_text 类似，但返回 (文本, usage)；当服务端不提供 usage 时用粗略估计。
    usage 字段示例：{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    """
    cfg = config or LLMConfig()
    cfg.validate()
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 openai 依赖，请先安装: pip install openai") from e
    if verbose:
        print(f"[llm] provider={cfg.provider} model={cfg.model} base_url={cfg.base_url or 'openai-default'}")
        print(f"[llm] prompt_chars={len(prompt)} temperature={temperature} max_tokens={max_tokens}")
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    last_exc: Optional[Exception] = None
    resp = None
    for i in range(max(1, cfg.max_retries)):
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "system", "content": "You are a helpful coding assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=cfg.request_timeout,
            )
            break
        except Exception as e:
            last_exc = e
            if i + 1 >= max(1, cfg.max_retries):
                break
            backoff = cfg.retry_base_sec * (2 ** i)
            if verbose:
                print(f"[llm] error={type(e).__name__}: {e}; retry in {backoff:.1f}s …")
            time.sleep(backoff)
    if resp is None:
        assert last_exc is not None
        raise last_exc
    text = (resp.choices[0].message.content or "").strip()
    usage: Dict[str, Any] = {}
    try:
        u = getattr(resp, "usage", None)
        if u:
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", None),
                "completion_tokens": getattr(u, "completion_tokens", None),
                "total_tokens": getattr(u, "total_tokens", None),
            }
    except Exception:
        usage = {}
    # 估算
    if not usage or usage.get("total_tokens") is None:
        def _est(s: str) -> int:
            return max(1, int(len(s) / 4))
        pt = _est(prompt)
        ct = _est(text)
        usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
    # 补充 provider/model 便于上层记录
    usage.setdefault("provider", cfg.provider)
    usage.setdefault("model", cfg.model)
    if verbose:
        print(f"[llm] usage={usage}")
    return text, usage


__all__ = ["LLMConfig", "complete_text", "complete_text_with_usage"]
