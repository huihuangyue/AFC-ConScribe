FROM selenium/standalone-chrome:latest

# 自定义浏览器容器：
#   - 继承官方 selenium/standalone-chrome:latest（自带 Chrome + VNC + noVNC + Selenium Server）
#   - 额外安装 socat，用于在容器内转发 Chrome 的 CDP 端口，便于宿主机通过 9223 控制 VNC 中的浏览器。

USER root

# 安装 socat（用于在容器内转发 CDP 端口）
RUN apt-get update && \
    apt-get install -y socat && \
    rm -rf /var/lib/apt/lists/*

# 复制自定义 entrypoint（注意此时构建上下文是 front/docker/）
COPY entrypoint.sh /opt/afc/entrypoint.sh
RUN chmod +x /opt/afc/entrypoint.sh

# selenium/standalone-chrome 默认用户是 seluser，这里切回去保持权限一致
USER seluser

# 使用自定义 entrypoint 包一层：内部会启动原始的 Selenium+VNC，再启动一只带 CDP 的 Chrome 和 socat 端口转发
ENTRYPOINT ["/opt/afc/entrypoint.sh"]
