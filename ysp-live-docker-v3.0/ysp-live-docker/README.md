# ysp-live v3.0 · Docker 部署版

央视频全频道直播（63 路，含 5 路真 4K / 高码率），一条命令跑起来。

## 准备

1. 装好 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Mac / Windows）或 Docker（Linux）。
2. **必须在家庭宽带下运行**——4K 后端的设备注册在机房/公司网络会被央视拒绝。

## 启动（三步）

```bash
cd ysp-live-docker
docker compose up -d --build
```

等半分钟，看到日志里出现 `设备协议就绪` 就是 4K 好了：

```bash
docker logs -f ysp-live
```

## 使用

- 首页：http://localhost:8767/
- 聚合订阅（63 路一次导入）：http://localhost:8767/all.m3u
- 诊断页：http://localhost:8767/diag
- 局域网其他设备：把 `localhost` 换成这台机器的局域网 IP（如 `http://192.168.1.10:8767/all.m3u`）

## 端口

| 端口 | 用途 |
|------|------|
| 8767 | 主服务：首页、订阅、63 路直播 |
| 8766 | 4K 后端：CCTV-5 / 5+ / 4K / 8K / 16-4K（局域网看真 4K 必须放行） |

## 不同机器的说明

- **x86 的 Mac / Windows / Linux**：完整功能，真 4K 直接可用。
- **Apple Silicon 的 Mac**：默认 4K 五路会回落 1080p（其余 58 路正常）。想用真 4K：把 `docker-compose.yml` 里 `platform: linux/amd64` 前面的 `#` 去掉，重新 `docker compose up -d --build`。Docker Desktop 会自动模拟运行 amd64 镜像（新版 Docker Desktop 已无 Rosetta 开关，不影响使用）。
- **ARM Linux（如树莓派）**：4K 五路回落 1080p，其余正常。

## 数据持久化

4K 后端的设备注册信息保存在 Docker 卷 `ysp-data` 里。重建、升级容器不会丢失，不用重新注册。

## 常用命令

```bash
docker compose up -d --build   # 启动 / 更新
docker logs -f ysp-live        # 看日志
docker compose restart         # 重启
docker compose down            # 停止（数据卷保留）
docker compose down -v         # 停止并删除数据（4K 需重新注册，慎用）
```

## 不用 4K 后端

只想要 63 路 1080p、不跑 4K 中继流量（看 4K 时约 2GB/小时），在 `docker-compose.yml` 里给 service 加：

```yaml
command: ["--no-4k"]
```

## 排错

- 4K 一直没就绪：先确认是家庭宽带，再看 `http://localhost:8767/diag` 里 4K 后端的状态。
- 端口被占用：改 `docker-compose.yml` 里端口映射的冒号左边（如 `"18767:8767"`），再 `docker compose up -d`。
