# ysp-live v3.0：央视频全频道直播（63 路 + 真 4K）

一次启动，全部都有。

## 里面有什么

- `ysp-live.py`：主程序（Python 3，标准库，无第三方依赖）
- `ysptp-project-arm64`：真 4K 后端（Apple Silicon Mac）
- `ysptp-project-x86_64`：真 4K 后端（Intel Mac）
- `ysptp-project-windows-x64.exe`：真 4K 后端（Windows 64 位）
- `ysptp-project-linux-x64`：真 4K 后端（Linux 64 位）
- `README.md`：本说明

主程序会自动按系统选择对应的后端，不用手动挑。

63 路频道：央视 21 + CGTN 6 + 剧场 3 + 卫视 31 + CETV-1 + 国学。
其中 5 路（CCTV-5、CCTV-5+、CCTV-4K、CCTV-8K、CCTV-16 4K）优先走设备协议
（码率更高，4K 三路为真 4K 3840×2160）；只有当设备协议失效时，
这 5 路才回落到 1080p。其余 58 路走 1080p。

## 怎么跑

1. 解压，把里面的文件放到同一个目录。
2. 去隔离属性（macOS 未签名程序会被拦截，Windows/Linux 跳过这步）：
   ```
   xattr -d com.apple.quarantine ysptp-project-arm64
   ```
   （Intel Mac 则对 `ysptp-project-x86_64` 执行；
   Linux 下给后端加执行权限：`chmod +x ysptp-project-linux-x64`）
3. 启动（只需这一次）：
   ```
   python3 ysp-live.py
   ```
   Windows 下用 `py ysp-live.py` 或 `python ysp-live.py`。
   默认 8767 端口。4K 后端会自动拉起（8766 端口），首次设备注册约半分钟，
   看到 `设备协议就绪` 就是好了。
4. 播放器打开首页 `http://localhost:8767/` 或聚合订阅：
   ```
   http://localhost:8767/all.m3u
   ```
   63 路一次导入。5 路设备协议频道在首页标着 [真4K] / [高码率]。

## 注意

- **必须在家庭宽带下运行**，4K 后端的设备注册在机房/公司网络会被央视拒绝。
  注册信息保存在 `device-state-rs.json`（4K 后端旁边），以后直接复用。
- 如果 4K 后端不在/没起来，4K 三路自动回落 1080p，不影响其他 60 路。
- 看 4K 时后端会中继视频流量（每小时约 2GB），看的时候保持运行；
  退出主程序时后端会自动关闭（`--no-4k` 可跳过启动后端）。
- 诊断页：`http://localhost:8767/diag`（看每路状态和 4K 后端是否就绪）。
