#!/usr/bin/env python3
"""
browser_service

在现有 VNC 可视浏览器（Chrome CDP）之上，通过 browser-use Agent 执行自然语言任务。

目标：
  - 复用 front/docker-compose.yml 提供的那只 Chrome（即 VNC 里看到的浏览器），而不是再起一只本地 Playwright；
  - 使用 .env 中配置的 OpenRouter（或其它 OpenAI 兼容）密钥与模型；
  - 提供一个同步函数 `run_task(...)`，方便被 front/run.py 的 Flask 路由直接调用。

注意：
  - 本模块默认通过环境变量 `AFC_PLAYWRIGHT_CDP_URL` 连接 CDP，通常为 `http://localhost:9223`；
  - 要求当前 Python 环境已安装：browser-use、langchain-openai、python-dotenv、httpx 等依赖；
  - 真正的网络 / 浏览器连通性，请在运行时环境中验证。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv


# 仓库根目录：front/browser-front/browser_service.py -> front -> repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]


def _mask(s: Optional[str]) -> str:
    if not s:
        return "<empty>"
    if len(s) <= 6:
        return "*" * len(s)
    return f"{s[:3]}***{s[-2:]} (len={len(s)})"


@dataclass
class BrowserUseRunResult:
    ok: bool
    task: str
    success: Optional[bool] = None
    final_result: Optional[str] = None
    n_steps: Optional[int] = None
    error: Optional[str] = None
    # 追加：每一步页面操作/结果的自然语言描述（由 browser-use 产生）
    steps: Optional[list[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _prepare_env(env_file: Optional[str] = None, verbose: bool = False) -> None:
    """加载 .env 并设置与 OpenRouter / browser-use 相关的环境变量映射。"""
    env_path = env_file or os.environ.get("AFC_ENV_FILE") or str(REPO_ROOT / ".env")
    if verbose:
        print("[browser_service] REPO_ROOT =", REPO_ROOT)
        print("[browser_service] env_file  =", env_path, "exists=", os.path.exists(env_path))
    load_dotenv(env_path)

    # 优先使用 OPENROUTER_API_KEY，其次回退到 AFC_LLM_API_KEY
    or_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AFC_LLM_API_KEY") or ""
    if not or_key:
        raise RuntimeError("未找到 OPENROUTER_API_KEY 或 AFC_LLM_API_KEY，请在 .env 中配置后重试。")

    # base_url 与 model：支持单独为 browser-use 指定，也支持复用 AFC_LLM_*。
    base_url = (
        os.environ.get("OPENROUTER_BASE_URL")
        or os.environ.get("AFB_BROWSER_USE_BASE_URL")
        or os.environ.get("AFC_LLM_BASE_URL")
        or "https://openrouter.ai/api/v1"
    )
    model = (
        os.environ.get("AFB_BROWSER_USE_MODEL")
        or os.environ.get("AFC_LLM_MODEL")
        or "deepseek/deepseek-chat"
    )

    # 将关键信息映射到常见变量名，方便 browser-use / langchain-openai 使用。
    os.environ.setdefault("OPENROUTER_API_KEY", or_key)
    os.environ.setdefault("OPENAI_API_KEY", or_key)
    os.environ.setdefault("OPENAI_BASE_URL", base_url)

    if verbose:
        print("[browser_service] OPENROUTER_API_KEY =", _mask(or_key))
        print("[browser_service] OPENAI_API_KEY    =", _mask(os.environ.get("OPENAI_API_KEY")))
        print("[browser_service] OPENAI_BASE_URL   =", os.environ.get("OPENAI_BASE_URL"))
        print("[browser_service] MODEL             =", model)

    # 将解析后的值放回环境，方便后续使用
    os.environ["BROWSER_USE_LLM_MODEL"] = model


async def _run_task_async(
    task: str,
    *,
    max_steps: int = 8,
    start_url: Optional[str] = None,
    env_file: Optional[str] = None,
    verbose: bool = False,
    show_agent_logs: bool = True,
) -> BrowserUseRunResult:
    # 1) 准备环境与 LLM 配置
    try:
        _prepare_env(env_file=env_file, verbose=verbose)
    except Exception as e:
        return BrowserUseRunResult(ok=False, task=task, error=f"env_error:{type(e).__name__}:{e}")

    # 2) 解析 CDP URL，复用 VNC 中的 Chrome
    cdp_url = os.environ.get("AFC_PLAYWRIGHT_CDP_URL", "").strip() or "http://localhost:9223"
    if verbose:
        print("[browser_service] CDP_URL =", cdp_url)

    # 3) 导入 browser-use 相关类
    try:
        from browser_use import Agent, BrowserSession, ChatOpenAI  # type: ignore[import]
    except Exception as e:  # pragma: no cover - 环境问题由调用者排查
        return BrowserUseRunResult(
            ok=False,
            task=task,
            error=f"import_error:{type(e).__name__}:{e}",
        )

    # 可选：关闭 browser-use 自己的 INFO 日志（包括 [Agent] 那一系列）
    if not show_agent_logs:
        import logging

        for name in (
            "browser_use",
            "browser_use.Agent",
            "browser_use.agent",
            "browser_use.agent.service",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)

    model = os.environ.get("BROWSER_USE_LLM_MODEL") or os.environ.get("AFB_BROWSER_USE_MODEL") or os.environ.get(
        "AFC_LLM_MODEL", "deepseek/deepseek-chat"
    )
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AFC_LLM_API_KEY") or ""
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("AFC_LLM_BASE_URL") or "https://openrouter.ai/api/v1"

    llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
    )

    # 4) 构造 BrowserSession，使用 CDP 连接到现有 Chrome（VNC 里那只）
    #    注意：不设置 is_local=True，避免本地再 launch；keep_alive=True，避免 kill 整个浏览器。
    browser_session = BrowserSession(
        cdp_url=cdp_url,
        is_local=False,
        keep_alive=True,
    )

    # 5) 构造 Agent
    agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        use_vision=False,  # 当前默认走纯文本模型，避免误用图片输入接口
        use_judge=False,  # 关闭 judge 阶段，避免额外的多模态调用
        source="afc-front-browser-use",
        step_timeout=240
    )

    if verbose:
        print("[browser_service] Agent created, max_steps =", max_steps)

    # 6) 运行 Agent（增加总超时，避免在浏览器/LLM 异常情况下长时间无响应）
    try:
        # 若在 step_timeout 之外整体执行超过 hard_timeout_s，则直接返回超时错误。
        # 这里先固定一个相对保守的上限，避免前端长期 pending 看不到任何反馈。
        hard_timeout_s = max(90, 20 * max_steps)  # 至少 90 秒，随步数线性放宽
        if verbose:
            print("[browser_service] hard_timeout_s =", hard_timeout_s)
        history = await asyncio.wait_for(agent.run(max_steps=max_steps), timeout=hard_timeout_s)
    except asyncio.TimeoutError:
        return BrowserUseRunResult(
            ok=False,
            task=task,
            error=f"agent_run_timeout:超过 {hard_timeout_s}s 仍未完成（可能卡在页面或 LLM 调用上）",
        )
    except Exception as e:
        return BrowserUseRunResult(
            ok=False,
            task=task,
            error=f"agent_run_error:{type(e).__name__}:{e}",
        )

    # 7) 提取最终结果与 success
    final_text: Optional[str] = None
    success: Optional[bool] = None
    n_steps: Optional[int] = None
    steps: list[str] = []

    try:
        # AgentHistoryList.final_result() 提供最终文本
        if hasattr(history, "final_result"):
            final_text = history.final_result()  # type: ignore[assignment]
    except Exception:
        final_text = None

    try:
        last = history.history[-1].result[-1]  # type: ignore[attr-defined]
        success = bool(getattr(last, "success", None))
    except Exception:
        success = None

    try:
        state = getattr(history, "state", None)
        if state is not None:
            n_steps = getattr(state, "n_steps", None)
    except Exception:
        n_steps = None

    # 8) 提取每一步的自然语言操作描述（尽量不依赖内部实现细节）
    try:
        for item in getattr(history, "history", []):  # type: ignore[attr-defined]
            for r in getattr(item, "result", []):
                parts: list[str] = []
                # done 标记（最后一步）
                if getattr(r, "is_done", False):
                    parts.append("[DONE]")
                # 错误消息
                err = getattr(r, "error", None)
                if err:
                    parts.append(f"ERROR: {err}")
                # 提取的内容（通常是“点击了什么 / 填写了什么”等描述）
                ec = getattr(r, "extracted_content", None)
                if ec:
                    parts.append(str(ec))
                # 长期记忆字段中也可能有更自然的描述
                mem = getattr(r, "long_term_memory", None)
                if mem:
                    parts.append(str(mem))
                if parts:
                    steps.append(" ".join(parts))
    except Exception:
        steps = []

    result = BrowserUseRunResult(
        ok=True if success is None else bool(success),
        task=task,
        success=success,
        final_result=final_text,
        n_steps=n_steps,
        error=None,
        steps=steps or None,
    )

    if verbose:
        # 轻量调试日志，帮助确认协程已经正常结束并生成结果
        print(
            "[browser_service] _run_task_async completed:",
            "ok=", result.ok,
            "success=", result.success,
            "n_steps=", result.n_steps,
            "final_result_preview=",
            (result.final_result[:60] + "…") if result.final_result else "<empty>",
        )

    return result


def run_task(
    task: str,
    *,
    max_steps: int = 8,
    start_url: Optional[str] = None,
    env_file: Optional[str] = None,
    verbose: bool = False,
    show_agent_logs: bool = True,
) -> Dict[str, Any]:
    """同步入口：供 Flask 路由调用。

    返回结构示例：
    {
      "ok": true,
      "task": "...",
      "success": true,
      "final_result": "The first result title is ...",
      "n_steps": 4,
      "error": null
    }
    """
    # start_url 当前主要由 browser-use 自身从 task 中解析 URL，不强制使用。
    # 预留参数方便后续扩展（例如：显式要求在当前页面继续操作）。
    _ = start_url
    # 注意：直接使用 asyncio.run(...) 在 browser-use 内部仍有后台任务存活时，
    # 会在其内部的 _cancel_all_tasks 阶段卡住，导致整个 Flask 请求迟迟不返回。
    # 这里手动管理事件循环，只等待 _run_task_async 本身完成，其它后台任务交由
    # loop.stop()/close 直接终止，避免前端长时间 pending。
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            _run_task_async(
                task,
                max_steps=max_steps,
                start_url=start_url,
                env_file=env_file,
                verbose=verbose,
                show_agent_logs=show_agent_logs,
            )
        )
    finally:
        try:
            loop.stop()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()

    out = result.to_dict()
    # 轻量日志：便于确认 /api/browser_use_run 是否顺利拿到结果
    try:
        print("[browser_service] run_task result.ok =", out.get("ok"), "success=", out.get("success"))
    except Exception:
        pass
    return out


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(description="手动调用 browser-use Agent（复用 VNC Chrome）")
    parser.add_argument("--task", required=True, help="自然语言任务描述")
    parser.add_argument("--max-steps", type=int, default=8, help="Agent 最大步数")
    parser.add_argument("--env-file", default=None, help="显式指定 .env 路径，默认使用仓库根目录 .env")
    parser.add_argument("--verbose", action="store_true", help="输出更详细日志")
    args = parser.parse_args()

    out = run_task(
        task=args.task,
        max_steps=args.max_steps,
        env_file=args.env_file,
        verbose=args.verbose,
    )
    print("[browser_service] result:\n", textwrap.indent(str(out), prefix="  "))
