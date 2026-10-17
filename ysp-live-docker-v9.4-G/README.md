# 📺 央视频全频道直播 v9.4 旗舰版

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
1. **（优化）取消启动强制预热**：全频道纯按需解析，彻底杜绝开机并发拉取 3 路 4K 触发平台频控、CDN 403 限流与设备被封
2. **（新增）400 自动换身份自愈**：遇到设备风控（HTTP 400）自动废弃旧设备身份、重新生成新设备并重试，内置熔断保护（5分钟仅换一次、每日最多5次）
3. **（新增）换台防抖**：切台先秒出 1080p，停留超 10 秒才在后台请求设备协议高码率，下次 m3u8 轮询自动无缝升级，播放器无需重开
4. **（优化）单设备并发池**：26 路高码率频道共用 1 台设备，并发上限 6 路，空闲超 15 秒自动释放配额
5. **（优化）设备注册失败快速降级 1080p**，不再长时间阻塞；剧场频道备用通道提速
6. **（新增）翻墙 / 代理工具分流规则**：解决开启翻墙工具时因切片走代理导致的 403 播放失败

---

## 💻 电脑版使用方法（PC / Mac / Linux）
1. 下载地址：https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp-live-v9.4.zip
2. Node.js 安装（非强制，建议安装，开启四层兜底）：官网下载 https://nodejs.org/zh-cn
3. 解压进入目录直接运行：
   ```bash
   python3 ysp-live-v9.4.py
   ```
   （可选）后台常驻运行：
   ```bash
   nohup python3 ysp-live-v9.4.py > ysp.log 2>&1 &
   ```
4. 播放器添加订阅地址：`http://localhost:8767/all.m3u`

---

## 🐳 Docker 版使用与升级方法
1. 下载地址：https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp-live-docker-v9.4.zip
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
4. 代码来源选择 “远程链接”，地址填写：`https://garysclub.sharewithyou.dpdns.org/ysp-live/ysp-live-v9.4.py`
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
