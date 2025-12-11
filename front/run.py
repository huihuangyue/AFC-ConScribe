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
  - 暴露 JSON API `/api/plan_task`，用于根据 task + run_dir 调用 planner 生成技能调用计划。
"""

import argparse
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request


ROOT = Path(__file__).resolve().parent

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


# 当前会话使用的 run_dir（技能库所在的 Detect 产物目录），默认可按需修改
# 注意：这里先给出一个相对仓库根目录的路径，启动时会统一解析为绝对路径。
APP_RUN_DIR: str = "workspace/data/ctrip_com/20251116234238"

# 左侧 VNC 浏览器视图的 URL（通常由 selenium/standalone-chrome 提供 noVNC 界面）
# 默认指向本机 7777 端口；如需自定义可设置环境变量 AFC_VNC_URL。
VNC_URL: str = os.getenv(
    "AFC_VNC_URL",
    "http://localhost:7777/?autoconnect=1&resize=scale&password=secret",
)


@app.route("/")
def index() -> str:
    """渲染主界面：左侧 VNC 画面 + 右侧聊天框，并注入当前 run_dir。"""
    return render_template("index.html", run_dir=APP_RUN_DIR, vnc_url=VNC_URL)


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


def main(argv: list[str] | None = None) -> None:
    """解析命令行参数并启动开发服务器。"""
    global APP_RUN_DIR

    parser = argparse.ArgumentParser(description="前端开发服务器（端口 7776）")
    parser.add_argument(
        "--run-dir",
            help="绑定的 Detect run_dir / 技能库根目录，例如 workspace/data/taobao_com/20251207184449（相对仓库根目录）",
            default=APP_RUN_DIR,
    )
    args = parser.parse_args(argv)
    APP_RUN_DIR = _resolve_run_dir(args.run_dir)

    print(f"[front.run] 使用 run_dir = {APP_RUN_DIR}")
    # 仅用于本地开发调试；生产环境请使用 gunicorn/uwsgi 等 WSGI 容器。
    app.run(host="127.0.0.1", port=7776, debug=True)


if __name__ == "__main__":  # pragma: no cover
    main()
