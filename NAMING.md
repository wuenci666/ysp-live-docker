# YSP 直播镜像命名规范 · YSP Image Naming Convention v1.0

> **权威单一事实来源 (Single Source of Truth)。**
> 所有 YSP 央视频直播 Docker 系列的 **镜像仓库名、镜像标签、容器名、Compose project 名**
> 都必须严格遵循本规范。新增任何版本时，直接套用本文公式即可，避免格式/规则异常。
>
> 适用范围：v3.0 ~ v9.5-G 全系列（含各 `-G` 卫视完整版）及今后发布的所有新版本。

---

## 0. 给 AI / Agent 的 30 秒摘要（机器可读）

```yaml
convention:
  name: "YSP Image Naming Convention"
  version: "1.0"
  repo_local: "ysp-live"                     # 本地镜像仓库名（固定）
  hub_user: "wuenci514"                       # Docker Hub 用户名（固定，小写）
  repo_remote: "wuenci514/ysp-live"           # = {hub_user}/ysp-live
  version_pattern: "vMAJOR.MINOR[-G]"         # 例：v9.4、v9.4-G
  tag_pattern: "vMAJOR.MINOR[-G] | latest"    # 镜像标签 = 版本号；latest 为浮动标签
  latest_points_to: "v9.5-G"                  # 当前最新稳定版（发布新版本时更新此值）
  container_name: "ysp-live-<slug>"           # slug 规则见下
  compose_project_name: "ysp-live-<slug>"     # 必须等于 container_name（显式声明 name:）
  slug_rule: "v + version.without('v','.','-')"   # 去前缀 v、去点、去连字符后连写
```

**任意版本 → 全名 套用公式：**

| 输入版本 | slug | 本地镜像 | 远程镜像 | 容器名 |
|---------|------|---------|---------|--------|
| `v9.5-G` | `v95g` | `ysp-live:v9.5-G` | `wuenci514/ysp-live:v9.5-G` | `ysp-live-v95g` |
| `v9.4-G` | `v94g` | `ysp-live:v9.4-G` | `wuenci514/ysp-live:v9.4-G` | `ysp-live-v94g` |
| `v9.0-G` | `v90g` | `ysp-live:v9.0-G` | `wuenci514/ysp-live:v9.0-G` | `ysp-live-v90g` |
| `v3.0`   | `v30`  | `ysp-live:v3.0`   | `wuenci514/ysp-live:v3.0`   | `ysp-live-v30`  |

> slug 生成（伪代码）：`"v" + version.lower().removeprefix("v").replace(".","").replace("-","")`
> 例：`v9.4-G` → `9.4-G` → `94G` → `v94g`

---

## 1. 命名总览（全版本对照表）

| 版本 | slug | 本地镜像 | 远程镜像 (Docker Hub) | 容器名 |
| --- | --- | --- | --- | --- |
| v3.0 | v30 | `ysp-live:v3.0` | `wuenci514/ysp-live:v3.0` | `ysp-live-v30` |
| v5.1 | v51 | `ysp-live:v5.1` | `wuenci514/ysp-live:v5.1` | `ysp-live-v51` |
| v6.2 | v62 | `ysp-live:v6.2` | `wuenci514/ysp-live:v6.2` | `ysp-live-v62` |
| v7.0 | v70 | `ysp-live:v7.0` | `wuenci514/ysp-live:v7.0` | `ysp-live-v70` |
| v7.2 | v72 | `ysp-live:v7.2` | `wuenci514/ysp-live:v7.2` | `ysp-live-v72` |
| v7.3 | v73 | `ysp-live:v7.3` | `wuenci514/ysp-live:v7.3` | `ysp-live-v73` |
| v7.4 | v74 | `ysp-live:v7.4` | `wuenci514/ysp-live:v7.4` | `ysp-live-v74` |
| v8.0 | v80 | `ysp-live:v8.0` | `wuenci514/ysp-live:v8.0` | `ysp-live-v80` |
| v8.1 | v81 | `ysp-live:v8.1` | `wuenci514/ysp-live:v8.1` | `ysp-live-v81` |
| v9.0 | v90 | `ysp-live:v9.0` | `wuenci514/ysp-live:v9.0` | `ysp-live-v90` |
| v9.4-G | v94g | `ysp-live:v9.4-G` | `wuenci514/ysp-live:v9.4-G` | `ysp-live-v94g` |
| **v9.5-G** ⭐ | **v95g** | `ysp-live:v9.5-G` | `wuenci514/ysp-live:v9.5-G` | `ysp-live-v95g` |
| v7.4-G | v74g | `ysp-live:v7.4-G` | `wuenci514/ysp-live:v7.4-G` | `ysp-live-v74g` |
| v8.0-G | v80g | `ysp-live:v8.0-G` | `wuenci514/ysp-live:v8.0-G` | `ysp-live-v80g` |
| v8.1-G | v81g | `ysp-live:v8.1-G` | `wuenci514/ysp-live:v8.1-G` | `ysp-live-v81g` |
| v9.0-G | v90g | `ysp-live:v9.0-G` | `wuenci514/ysp-live:v9.0-G` | `ysp-live-v90g` |

> `latest` 浮动标签同时存在于本地与远程，始终指向 `latest_points_to`（当前 `v9.5-G`），
> 其 digest 必须与该版本标签完全一致。

---

## 2. 详细规则

### 2.1 仓库名 Repository
- **本地**：`ysp-live`（固定，全小写）
- **Docker Hub**：`wuenci514/ysp-live`（格式 `{hub_user}/ysp-live`）
- ❌ 禁止：`library/ysp-live`、`docker.io/ysp-live`、大写、下划线开头的仓库名
- 原因：无用户名前缀的 push 会被解析到官方 `library/` 命名空间，必然 `insufficient_scope` 失败

### 2.2 版本号 Version
- 格式：`v<MAJOR>.<MINOR>`（例 `v9.4`）
- `-G` 后缀 = 「卫视完整版」补丁：在内核不变的前提下，把被官方版砍掉的卫视频道恢复为全量 64 路
- 示例：`v9.4`、`v9.4-G`、`v7.4-G`
- 当前旗舰版版本号即 `v9.4-G`（带 G），不要省略 G

### 2.3 镜像标签 Tag
- **版本标签** = 版本号原样：`v9.4-G`（保留点 `.` 与连字符 `-`，tag 允许）
- **`latest`** = 浮动标签，始终指向"当前推荐用户使用的最新稳定版"（现 = `v9.4-G`）
- 推送时：`latest` 必须与对应版本标签指向**同一镜像 ID（digest 一致）**
- ❌ 禁止：无 `v` 前缀的标签（如 `9.4`）、随机字符串标签、日期以外的临时标签

### 2.4 容器名 / Compose project 名
- **容器名** = `ysp-live-` + slug（slug 见第 0 节规则）
- **Compose `name:` 字段必须显式声明** = 容器名
  - 若不声明，Docker 会用**目录名**自动生成 project 名（如目录 `ysp-live-docker-v9.4-G`
    会被规范化为 `ysp-live-docker-v94-g`），导致 Docker Desktop 列表显示怪异、与容器名不一致
- 端口统一 `8767:8767`（所有版本共用，靠"一次只跑一个"避免冲突）

### 2.5 `latest` 浮动标签策略
- `latest` 永远指向"当前推荐版本"，不是"刚构建的版本"
- 发布新版本时按顺序：① 打新版本标签 → ② 把 `latest` 重新指向新版本 → ③ 更新本规范的 `latest_points_to`
- 远程删除旧版本标签请在 Docker Hub 网页手动操作（CLI 无法删远程 tag）

---

## 3. 新版本套用模板（复制即用）

```bash
# ===== 变量（按新版本填写）=====
VER="vX.Y-G"          # 版本号，例 v9.5-G
SLUG="vXYg"           # slug，例 v95g（去 v/./- 连写）
HUB="wuenci514/ysp-live"

# 1. 构建（在对应版本目录，compose 已含 name:/container_name:/image:）
docker compose up -d --build

# 2. 打本地 + 远程标签
docker tag ysp-live:$VER ysp-live:$VER
docker tag ysp-live:$VER $HUB:$VER
docker tag ysp-live:$VER $HUB:latest

# 3. 推送
docker push $HUB:$VER
docker push $HUB:latest

# 4. 远程校验（digest 应一致）
docker manifest inspect $HUB:$VER   | grep '"digest"'
docker manifest inspect $HUB:latest | grep '"digest"'
```

```yaml
# 每个版本目录的 docker-compose.yml 固定头部（务必显式 name:）
name: ysp-live-vXYg                 # = 容器名，避免 Docker 用目录名生成 project 名
services:
  ysp-live:
    image: ysp-live:vX.Y-G          # 本地仓库:版本标签
    container_name: ysp-live-vXYg   # = name:
    restart: unless-stopped
    ports:
      - "8767:8767"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
```

> 切换脚本 `ysp-switch.sh` 中 `LATEST_VERSION` 也应同步改为新版本（如 `vX.Y-G`），
> 使 `bash ysp-switch.sh latest` 指向新旗舰版。

---

## 4. 反例（明确禁止）

| 错误写法 | 原因 |
| --- | --- |
| `docker push ysp-live:v9.4-G` | 无 `hub_user/` 前缀 → 解析到 `library/`，无权限 |
| 容器名 `ysp-live` | 多版本冲突，且 project 名会被目录名污染 |
| 标签 `9.4`（无 `v`） | 不符合版本号格式 `vMAJOR.MINOR` |
| 容器名 `ysp-live-docker-v94-g` | 这是目录名规范化的 project 名，不是规范容器名 |
| `latest` 与版本标签 digest 不一致 | 违反 2.5 浮动标签策略 |
| 远程保留旧 `9.4` tag | 与 `v9.4-G` 重复且不规范，应手动删除 |

---

## 5. Docker 名称技术限制（必须遵守）

- **容器名**：`[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}`，长度 ≤ 128
- **镜像标签**：`[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`，惯例小写
- **仓库名每段**：`[a-z0-9][a-z0-9._-]`，Docker Hub 用户名强制小写
- 我们的 slug 规则（去 `.`/`-`）天然满足上述约束，且避免 Docker Desktop 对 `.` 的规范化歧义

---

## 6. 历史迁移备注

- 早期容器 `ysp-live-v3.0` 等**保留了点号**（非 slug 形式）。新规要求去点，但因历史容器已存在，
  **仅对新版本严格执行 slug**；重命名旧容器非必须（播放器订阅地址不受影响）。
- Docker Hub 网页上残留的旧 `9.4` tag（非 `v9.4-G`）需手动在 Hub 仓库 Tags 页删除，才能真正干净。
- 本规范自 **v9.4-G**（容器 `ysp-live-v94g`）起作为权威来源执行；后续版本一律照此生成。当前 `latest` 指向 **v9.5-G**（容器 `ysp-live-v95g`）。

---

## 7. 维护者更新 `latest` 的步骤（发布新版本时）

1. 构建并打标签（见第 3 节）
2. `docker push <hub>/<new-ver>` 与 `docker push <hub>/latest`
3. 编辑本文件：`latest_points_to` 改为新版本号
4. 同步更新 `README.md` 与 `使用说明书.html` 中"命名规范"章节的 `latest` 指向与对照表
5. 同步 `ysp-switch.sh` 的 `LATEST_VERSION`
