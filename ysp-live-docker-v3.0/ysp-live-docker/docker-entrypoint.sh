#!/bin/sh
# ysp-live 容器入口：持久化 4K 设备注册信息 + 架构提示，然后启动主程序
set -e

mkdir -p /data

# device-state-rs.json 由 4K 后端写在程序目录；搬到 /data 卷里，重建容器不丢
if [ -f /app/device-state-rs.json ] && [ ! -L /app/device-state-rs.json ]; then
  mv -f /app/device-state-rs.json /data/device-state-rs.json
fi
ln -sf /data/device-state-rs.json /app/device-state-rs.json

if [ "$(uname -m)" != "x86_64" ]; then
  echo "[entrypoint] 当前架构 $(uname -m)：4K 后端只有 x86_64 版本，5 路频道将回落 1080p，其余 58 路正常。"
  echo "[entrypoint] Apple Silicon Mac 想用真 4K：在 docker-compose.yml 里取消 platform: linux/amd64 的注释后重新构建。"
fi

exec python3 /app/ysp-live.py "${YSP_PORT:-8767}" "$@"
