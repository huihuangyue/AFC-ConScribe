from __future__ import annotations

"""
简单开发服务器：

在仓库根目录运行：

    python front/run.py --run-dir workspace/data/taobao_com/20251207184449

然后在浏览器打开：http://localhost:7776

该服务会：
  - 使用 Flask 提供一个根路由 `/` 渲染 `templates/index.html`；
  - 通过 `/static/*` 提供 `front/static/` 下的静态资源；
  - 接收命令行参数 `--run-dir`，将其作为当前会话绑定的技能库/运行目录，
    注入到页面中（前端可通过 `window.CURRENT_RUN_DIR` 使用）；
  - 暴露 JSON API `/api/plan_task`，用于根据 task + run_dir 调用 planner 生成技能调用计划；
  - 在左侧通过 `<iframe>` 直接嵌入一个真实网页（由浏览器自身渲染），而不是 VNC 或截图。
"""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode
import sys

from flask import Flask, jsonify, render_template, request


ROOT = Path(__file__).resolve().parent

# 可选：将 front/browser-front 加入 sys.path，便于导入 browser_service
_BROWSER_FRONT_DIR = ROOT / "browser-front"
if _BROWSER_FRONT_DIR.exists():
    _p = str(_BROWSER_FRONT_DIR)
    if _p not in sys.path:
        sys.path.insert(0, _p)

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
    static_url_path="/static",
)


def _resolve_run_dir(p: str) -> str:
    """将 run_dir 解析为绝对路径。

    规则：
      - 若 p 已是绝对路径，直接返回；
      - 否则视为“相对于仓库根目录（front 的上一级）”。
    """
    if os.path.isabs(p):
        return p
    repo_root = ROOT.parent  # /mnt/.../AFC-ConScribe
    return os.path.abspath(repo_root / p)


def _derive_browser_url_from_run_dir(run_dir: str) -> str:
    """从 run_dir 下的 meta.json 中推导一个合适的起始 URL。"""
    meta_path = Path(run_dir) / "meta.json"
    try:
        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f) or {}
    except Exception:
        meta = {}

    url = meta.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url

    domain = (meta.get("domain") or meta.get("domain_sanitized") or "").strip()
    if domain:
        return f"https://{domain}/"

    return "https://www.taobao.com/"


def _preload_start_url_via_cdp(start_url: str) -> None:
    """尝试通过 CDP 控制 VNC 中的 Chrome 预先打开起始页面。

    前提：
      - .env 中配置了：
          AFC_BROWSER_BACKEND=cdp
          AFC_PLAYWRIGHT_CDP_URL=http://localhost:9223  # 或其它 CDP 端点
      - front/docker-compose.yml + entrypoint 已经启动了带 remote-debugging 的 Chrome。

    若任一步失败（Playwright 未安装、CDP 不可达等），将仅打印日志而不抛出异常。
    """
    backend = os.getenv("AFC_BROWSER_BACKEND", "").strip().lower()
    cdp_url = os.getenv("AFC_PLAYWRIGHT_CDP_URL", "").strip()
    if not start_url or backend not in {"cdp", "remote_cdp"} or not cdp_url:
        return

    try:
        # 先解析 /json/version 拿到 webSocketDebuggerUrl
        from urllib.request import urlopen  # type: ignore
        import json as _json
        from playwright.sync_api import sync_playwright
    except Exception:
        print("[front.run] CDP 预加载起始页面失败：缺少依赖（可能未安装 playwright），已跳过。")
        return

    try:
        meta_url = cdp_url.rstrip("/") + "/json/version"
        with urlopen(meta_url, timeout=3.0) as resp:  # type: ignore[arg-type]
            raw = resp.read().decode("utf-8", errors="ignore")
        meta = _json.loads(raw) if raw else {}
        ws_endpoint = str(meta.get("webSocketDebuggerUrl") or cdp_url).strip()
    except Exception as e:  # pragma: no cover - 网络错误时直接跳过
        print(f"[front.run] CDP 预加载：解析 {cdp_url}/json/version 失败，已跳过。错误: {e}")
        return

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws_endpoint)
            # 优先复用现有 context；若不存在则新建一个
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            print(f"[front.run] CDP 预加载：在可视 Chrome 中打开起始页面: {start_url}")
            page.goto(start_url, wait_until="load")
            # 给一点时间渲染首屏，然后直接结束，本次连接关闭不会影响页面已打开的结果。
            page.wait_for_timeout(1000)
    except Exception as e:  # pragma: no cover
        print(f"[front.run] CDP 预加载：连接或导航失败，已跳过。错误: {e}")


# 当前会话使用的 run_dir（技能库所在的 Detect 产物目录），默认可按需修改。
# 注意：这里先给出一个相对仓库根目录的路径，启动时会统一解析为绝对路径。
APP_RUN_DIR: str = "workspace/data/github_com/20251216193438"
# 左侧嵌入浏览器视图的起始 URL（真实网页，由远程浏览器加载）。
# - 若环境变量 AFC_FRONT_BROWSER_URL 已设置，则优先使用；
# - 否则会在启动时尝试从 run_dir/meta.json 中推导：
#     1) meta.url；
#     2) meta.domain / meta.domain_sanitized → https://<domain>/；
# - 若仍然失败，则退回为 https://www.taobao.com/。
BROWSER_URL: str = os.getenv("AFC_FRONT_BROWSER_URL", "")

# 执行模式（直接在此处固定，如需切换可手动修改）：
#   - "planner"     : 使用原有 planner + browser.invoke 流程（/api/plan_task + /api/execute_task）
#   - "browser_use" : 使用 browser-use Agent（/api/browser_use_run），通过 CDP 操纵 VNC Chrome
EXEC_MODE: str = "browser_use"

# 是否在后端终端中显示 browser-use 的 INFO 日志（包括 [Agent] ... 这些输出）
BROWSER_USE_SHOW_AGENT_LOGS: bool = False

# noVNC 访问地址相关配置：
#   - AFC_VNC_BASE_URL: noVNC 页面基地址（默认 http://localhost:7777）
#   - AFC_VNC_PASSWORD: VNC 密码（与 Docker 容器配置保持一致，默认 secret）
VNC_BASE_URL: str = os.getenv("AFC_VNC_BASE_URL", "http://localhost:7777")
VNC_PASSWORD: str = os.getenv("AFC_VNC_PASSWORD", "secret")
VNC_URL: str = ""  # 运行时根据 BROWSER_URL 拼接查询参数生成


@app.route("/")
def index() -> str:
    """渲染主界面：左侧 noVNC 浏览器视图 + 右侧聊天框，并注入当前 run_dir。"""
    return render_template("index.html", run_dir=APP_RUN_DIR, vnc_url=VNC_URL, exec_mode=EXEC_MODE)


@app.post("/api/plan_task")
def api_plan_task():
    """JSON API：接收 {run_dir, task}，调用 llm_module.plan_task 返回规划结果。"""
    from llm_module import plan_task

    data = request.get_json(silent=True) or {}
    run_dir_raw = str(data.get("run_dir") or APP_RUN_DIR)
    run_dir = _resolve_run_dir(run_dir_raw)
    task = str(data.get("task") or "").strip()
    if not task:
        return jsonify({"ok": False, "error": "empty_task"}), 400

    result = plan_task(
        run_dir=run_dir,
        task=task,
        top_k=5,
        use_llm_plan=True,
        use_llm_args=True,
        verbose=True,
    )
    status = 200 if result.get("ok") else 500
    return jsonify(result), status


@app.post("/api/execute_task")
def api_execute_task():
    """JSON API：接收 {run_dir?, skill_path, call_str, ...}，委托 front.app.execute_task_impl 执行技能。

    这样前端只需与 front/run.py 同源通信，无需再直接访问 front/app.py 的端口，避免浏览器 CORS 问题。
    """
    from front.app import execute_task_impl  # type: ignore

    data = request.get_json(silent=True) or {}
    # 若未显式提供 run_dir，则使用当前会话绑定的 APP_RUN_DIR
    run_dir_raw = str(data.get("run_dir") or APP_RUN_DIR)
    payload = dict(data)
    payload["run_dir"] = run_dir_raw

    result, status = execute_task_impl(payload)
    return jsonify(result), status


@app.post("/api/browser_use_run")
def api_browser_use_run():
    """使用 browser-use Agent（复用 VNC Chrome）执行自然语言任务。

    请求 JSON：
      {
        "task": "自然语言指令",
        "max_steps": 10,          # 可选，默认 8
        "start_url": "https://…"  # 可选，默认使用当前 BROWSER_URL
      }

    返回示例：
      {
        "ok": true,
        "task": "...",
        "success": true,
        "final_result": "…",
        "n_steps": 4,
        "error": null
      }
    """
    try:
        import browser_service  # type: ignore[import]
    except Exception as e:  # pragma: no cover - 环境问题
        return jsonify({"ok": False, "error": f"import_browser_service_failed:{type(e).__name__}:{e}"}), 500

    data = request.get_json(silent=True) or {}
    task = str(data.get("task") or "").strip()
    if not task:
        return jsonify({"ok": False, "error": "empty_task"}), 400

    try:
        max_steps = int(data.get("max_steps") or 8)
    except Exception:
        max_steps = 8

    start_url = str(data.get("start_url") or "").strip() or BROWSER_URL

    # 统一使用仓库根目录 .env，除非外部显式设置 AFC_ENV_FILE
    repo_root = ROOT.parent
    env_path = os.environ.get("AFC_ENV_FILE") or os.path.join(str(repo_root), ".env")

    try:
        result = browser_service.run_task(
            task=task,
            max_steps=max_steps,
            start_url=start_url,
            env_file=env_path,
            verbose=True,
            show_agent_logs=BROWSER_USE_SHOW_AGENT_LOGS,
        )
    except Exception as e:  # pragma: no cover - 兜底错误
        return jsonify({"ok": False, "error": f"browser_use_run_failed:{type(e).__name__}:{e}"}), 500

    status = 200 if result.get("ok") else 500
    return jsonify(result), status


def main(argv: list[str] | None = None) -> None:
    """解析命令行参数并启动开发服务器。"""
    global APP_RUN_DIR, BROWSER_URL, VNC_URL

    parser = argparse.ArgumentParser(description="前端开发服务器（端口 7776）")
    parser.add_argument(
        "--run-dir",
            help="绑定的 Detect run_dir / 技能库根目录，例如 workspace/data/taobao_com/20251207184449（相对仓库根目录）",
            default=APP_RUN_DIR,
    )
    args = parser.parse_args(argv)
    APP_RUN_DIR = _resolve_run_dir(args.run_dir)

    # 若未通过环境变量显式指定浏览器起始 URL，则尝试从 run_dir/meta.json 中推导
    if not BROWSER_URL:
        BROWSER_URL = _derive_browser_url_from_run_dir(APP_RUN_DIR)

    # 在启动前尝试通过 CDP 预先在 VNC Chrome 中加载起始页面，便于左侧一打开就看到目标站点。
    try:
        _preload_start_url_via_cdp(BROWSER_URL)
    except Exception:
        # 任何预加载异常都不应阻止前端服务启动。
        pass

    # 根据 BROWSER_URL 构造 noVNC 的访问 URL，并通过查询参数携带 start_url 信息
    params = {
        "autoconnect": "1",
        "resize": "scale",
        "password": VNC_PASSWORD,
        # 约定 start_url 供容器内的 noVNC 页面读取，真正使用时可以在自定义 HTML 中解析此参数
        "start_url": BROWSER_URL,
    }
    VNC_URL = VNC_BASE_URL.rstrip("/") + "/?" + urlencode(params)

    print(f"[front.run] 使用 run_dir = {APP_RUN_DIR}")
    print(f"[front.run] 起始浏览器 URL = {BROWSER_URL}")
    print(f"[front.run] noVNC URL = {VNC_URL}")
    # 仅用于本地开发调试；生产环境请使用 gunicorn/uwsgi 等 WSGI 容器。
    app.run(host="127.0.0.1", port=7776, debug=True)


if __name__ == "__main__":  # pragma: no cover
    main()
