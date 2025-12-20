#!/usr/bin/env bash
set -e

##
## 自定义入口脚本：
##   - 先启动 selenium/standalone-chrome 自带的入口（Selenium + VNC + noVNC）
##   - 再在同一个容器内启动一只带 CDP 的 Chrome + socat 端口转发
##
## 约定：
##   - 官方镜像的入口脚本路径为 /opt/bin/entry_point.sh
##

ORIGINAL_ENTRYPOINT="/opt/bin/entry_point.sh"

if [ ! -x "$ORIGINAL_ENTRYPOINT" ]; then
  echo "[entrypoint] ERROR: original selenium entrypoint not found at ${ORIGINAL_ENTRYPOINT}"
  echo "[entrypoint] 请检查 selenium/standalone-chrome 镜像版本，或更新此脚本中的路径。"
  exit 1
fi

echo "[entrypoint] 启动 Selenium + VNC + noVNC ..."
"${ORIGINAL_ENTRYPOINT}" &
SELENIUM_PID=$!

# 稍等几秒，确保 VNC / noVNC / Chrome 已经起来
sleep 5

echo "[entrypoint] 启动用于 CDP 的可见 Chrome (remote-debugging-port=9222, address=0.0.0.0) ..."
# 这里显式启动一只挂在当前 DISPLAY 上的 Chrome，开启 CDP 端口 9222，并监听 0.0.0.0。
# 注意：如果只设置 --remote-debugging-port 而不设置 address，Chrome 可能只绑定在 127.0.0.1，
# 这会导致容器内部可以访问 http://localhost:9222，但宿主机通过端口映射访问时连接会被重置。
google-chrome \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --user-data-dir=/tmp/afc_cdp_profile \
  about:blank &
CDP_CHROME_PID=$!

echo "[entrypoint] 启动 socat 端口转发 (0.0.0.0:9223 -> 127.0.0.1:9222) ..."
# 容器内部 curl localhost:9222 是通的，但宿主机通过 9222 访问时连接被重置。
# 在这里额外启动一个 socat，将 0.0.0.0:9223 上的连接转发到 127.0.0.1:9222。
# 宿主机只需连接 http://localhost:9223，即可复用容器内 CDP 服务。
socat TCP-LISTEN:9223,fork,reuseaddr TCP:127.0.0.1:9222 &
SOCAT_PID=$!
echo "[entrypoint] Selenium PID=${SELENIUM_PID}, CDP Chrome PID=${CDP_CHROME_PID}, socat PID=${SOCAT_PID}"

# 等待 Selenium 进程退出（容器主生命周期）
wait "${SELENIUM_PID}"
RET=$?

echo "[entrypoint] Selenium 进程退出，返回码=${RET}，准备结束容器。"
exit "${RET}"
