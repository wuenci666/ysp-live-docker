# ysp-live v5.1 Docker 版（纯 Python，零二进制）

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

看到 `设备协议就绪` 就是好了（首次约半分钟）。

## 使用

- 首页：`http://localhost:8767/`
- 聚合订阅：`http://localhost:8767/all.m3u`

## 停止 / 重启

```bash
docker compose down      # 停止并删除容器
docker compose restart   # 重启
```

## 注意

- 订阅自带台标、`tvg-id` 和 EPG 地址：播放器导入 `all.m3u` 后节目单自动匹配（61 路），不用手动填 EPG 源。
- 看真 4K 频道时容器会中继视频流量（约 15GB/小时，按 35Mbps 码率算）。
- 必须在**家庭宽带**下跑，设备注册会被机房 IP 拒绝。
  Docker Desktop 默认 NAT 出去走的就是你家宽带，没问题；
  但不要把这个镜像放到 VPS/云服务器上跑，26 路设备协议频道会失效。
- 容器删了重建后需要重新设备注册（约半分钟），这是正常的。
