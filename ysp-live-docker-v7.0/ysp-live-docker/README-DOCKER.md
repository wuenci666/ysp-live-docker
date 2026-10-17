# ysp-live v7.0 Docker 版（纯 Python，单端口极简部署，含 7 天回看）

## 准备

把这几个文件放在同一个目录：
- `Dockerfile`
- `docker-compose.yml`
- `ysp-live.py`
- `README-DOCKER.md`

## 构建

```bash
cd 这个目录
docker compose build
```

## 运行

```bash
docker compose up -d
```

看日志确认设备协议就绪：

```bash
docker logs -f ysp-live
```

看到 `设备协议就绪` 就是好了（首次约 5~10 秒）。

## 使用

- 首页：`http://localhost:8767/`
- 聚合订阅：`http://localhost:8767/all.m3u`（含 7 天回看与多源精准 EPG）
- 诊断信息：`http://localhost:8767/diag`

## 停止 / 重启

```bash
docker compose down      # 停止并删除容器
docker compose restart   # 重启
```

## 特性说明

- **原生 7 天时移与回看**：央视及 32 路省级卫视全线支持 7 天节目倒退快进与过往节目重温，播放器自动识别 `catchup`。
- **多源精准 EPG 节目单**：聚合三大权威节目单源，告别缺台少日期的困扰，全天候 100% 节目单自动对齐。
- **单端口部署**：仅需暴露 8767 端口即可承载 M3U8 清单及 TS 分片中继，无端口冲突。
- **后台自动保活防休眠**：心跳每 30 秒全天候发送，提前 5 分钟在后台静默换新 Session，长时间无播放请求也不会休眠。
- 必须在**家庭宽带**下跑，设备注册会被机房 IP 拒绝。
  Docker Desktop 默认 NAT 出去走的就是你家宽带，没问题；但不要把这个镜像放到 VPS/云服务器上跑，26 路设备协议频道会失效。
