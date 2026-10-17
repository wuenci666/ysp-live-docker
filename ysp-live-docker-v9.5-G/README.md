# 📺 央视频全频道直播 v9.5 旗舰版

## 📢 首发地址
社区：[Gary's Club](https://t.me/garysclub)  
频道：[Gary's Club Channel](https://t.me/garysclubchannel)  
⚠️ 其他任何分发渠道均非作者本人！

---

## ✨ 核心亮点
1. 独家双引擎四层全自动兜底机制，多重故障平滑回退不断流
2. 真 4K 超高清，内置全部 64 路独立频道（央视 30 路 + 卫视 34 路）
3. 原生 7 天时移回看，完美适配 TiviMate / APTV 等主流播放器
4. SWR 毫秒秒开 + 边缘防抖 + 换台防抖，彻底消除黑屏转圈
5. 全链路统一 8767 单端口，多协议内部静默协同、按需调度
6. 开箱即用 Docker 镜像，内置数据持久化与 400 故障自动愈合

---

## 🛠 核心更新与修复
1. **（修复）状态持久化与 400 自愈落盘修复**：补齐内部队列状态初始化，修复 `proxy-cache-state-rs.json` 元数据无法落盘及 400 自动换设备身份时触发异常导致自愈中断的问题
2. **（优化）换台防抖精准化与防风控减负**：修复 `--zap-debounce` 参数传递；按频道独立追踪防抖状态，稳定观看的高码率频道在 60 秒续期时不再误触发降级；切走或关闭播放器后自动取消后台高码率预取，避免无效请求消耗设备配额与触发风控
3. **（优化）1080p 通道冷启动与时间线防倒退**：消除冷启动瞬间重复拉取同一频道的冗余请求；修复并发首播等待逻辑避免瞬时 503；新增切片历史去重队列，彻底解决无时间戳流因滑动窗口推进导致的画面倒退回放问题
4. **（修复）HLS 协议规范与重定向兼容**：跨源切换时的 `#EXT-X-DISCONTINUITY` 严格按 RFC 8216 规范注入于首个媒体切片标签前；内置播放列表拉取新增 HTTPS 与 301/302/307/308 重定向支持，`/proxy.ts` 补齐 `Location` 头透传与 `Range` 分片请求兼容
5. **（优化）内存缓存 LRU 淘汰与并发性能**：修复 `TsSegmentCache` 重复键入队导致的提前误淘汰问题；预计算复用 AES-256-GCM 轮密钥，降低控制面 CPU 开销；修复 WASM `W.list` 数组写入截断问题
6. **（增强）反代协议识别、回看时移与安全加固**：支持反向代理 `X-Forwarded-Proto` 自动识别 HTTPS 协议；增强 `catchup` 回看时间解析（支持毫秒级时间戳与 ISO 8601 UTC 格式）；修复 `/engine_proxy` 本地文件读取与 SSRF 风险，优化 `/diag` 实时配额诊断展示

---

## 💻 电脑版使用方法（PC / Mac / Linux）
1. 下载地址：https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp-live-v9.5.zip
2. Node.js 安装（非强制，建议安装，开启四层兜底）：官网下载 https://nodejs.org/zh-cn
3. 解压进入目录直接运行：
   ```bash
   python3 ysp-live-v9.5.py
   ```
   （可选）后台常驻运行：
   ```bash
   nohup python3 ysp-live-v9.5.py > ysp.log 2>&1 &
   ```
4. 播放器添加订阅地址：`http://localhost:8767/all.m3u`

---

## 🐳 Docker 版使用与升级方法
1. 下载地址：https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp-live-docker-v9.5.zip
2. 解压后进入目录，一行命令直接全新构建并启动（已内置国内镜像加速与权限自愈）：
   ```bash
   docker compose up -d --build
   ```
3. 播放器添加订阅地址：
   - 电脑本机播放：`http://localhost:8767/all.m3u`
   - 局域网播放：`http://你的局域网IP:8767/all.m3u`

---

## 📱 APTV 使用方法
1. 点击 “配置” ➔ “服务” ➔ “添加服务”
2. 服务名称：`央视频直播`
3. 服务类型：`Python`
4. 代码来源选择 “远程链接”，地址填写：`https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp-live-v9.5.py`
5. 点击 “加载代码” ➔ 划到底部点击 “添加服务” ➔ 打开服务开关
6. 前往 APTV “添加订阅”，地址填写：`http://127.0.0.1:8767/all.m3u`

---

## 🚀 翻墙/代理工具分流配置
1. **小火箭（Shadowrocket）分流规则**：  
   打开小火箭，点击底部 「配置」 -> 「模块」。  
   点击右上角 「+」，粘贴模块链接： `https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp.module`
2. **通用规则集（Surge / Loon / Clash 等）**：  
   规则集地址：`https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp.list`

---

## ⚠️ 友情提醒
1. **流量消耗**：观看真 4K 视频流量约 15GB/小时，1080p 约 5GB/小时。
2. **网络要求**：建议在家庭宽带直连运行。
3. **状态诊断报告**（NAS 或远程设备请将 localhost 替换为对应 IP）：
   - 健康检查：`http://localhost:8767/health`
   - 诊断日志：`http://localhost:8767/diag`

---

## 🙏 特别感谢
1. 感谢 **IPTV 直播源总部** 分享的算法源码 
2. 感谢 **Gary's Club** 群里小伙伴 **ll** 提供大量频道的 live_id 
3. 感谢 **APTV 群主** 提供的技术帮助
