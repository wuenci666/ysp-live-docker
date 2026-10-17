# ysp-live v3.0 — Docker 部署
# 基础镜像用 Debian slim：4K 后端二进制是 glibc 动态链接，Alpine(musl) 跑不起来
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    YSP_PORT=8767

WORKDIR /app

# 主程序（纯标准库，无需 pip）+ 4K 后端（Linux x86_64）
COPY app/ysp-live.py app/ysptp-project-linux-x64 ./
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /app/ysptp-project-linux-x64 /usr/local/bin/docker-entrypoint.sh

# /data 持久化 4K 后端设备注册信息（device-state-rs.json），重建容器不重新注册
VOLUME ["/data"]

# 8767: 主服务（首页 / all.m3u / 63 路直播）；8766: 4K 后端（5 路设备协议频道，局域网看真 4K 必须映射）
EXPOSE 8767 8766

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8767/health', timeout=8).status==200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
