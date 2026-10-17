# YSP-Live 多平台同步手册（Docker Hub → GitHub → CNB 自动同步）

## 你的核心诉求

> **每次在 Docker Hub 更新镜像，CNB 与 GitHub 自动跟着同步。**

为此本仓库提供两条互补的自动化路径：

- **路径 A（推荐，一次构建三处）**：把 GitHub 作为唯一构建入口，用 `build-push.yml`
  一次多架构构建，**同时推送 Docker Hub + ghcr + cnb**。这样"更新"= 跑一次 workflow，
  三处天然一致，不需要事后镜像。
- **路径 B（镜像模式）**：你习惯在本地 `docker push` 到 Docker Hub，再让
  `sync-from-dockerhub.yml` 把刚刚推上去的镜像 **拉取 → re-tag → 推送到 ghcr + cnb**。
  支持手动点一次 Run，也支持 Docker Hub webhook 全自动触发。

文档 / 代码则通过 git 双推到 GitHub 与 CNB 两个仓库。

---

## 一、账户与地址（已按实际账号修正）

| 平台 | 代码仓库 | 容器镜像地址 |
|------|----------|--------------|
| Docker Hub | — | `wuenci514/ysp-live` |
| GitHub | `github.com/wuenci666/ysp-live-docker` | `ghcr.io/wuenci666/ysp-live` |
| CNB | `cnb.cool/wuenciteam/ysp-live-docker` | `docker.cnb.cool/wuenciteam/ysp-live-docker` |

> ⚠️ 命名注意：cnb.cool 的镜像地址**必须与仓库名完全一致**，故 cnb 段为 `ysp-live-docker`；
> Docker Hub / ghcr 沿用产品名 `ysp-live`。三者仅 cnb 段名不同，这是平台限制，非笔误。

---

## 二、前置（一次性）

### 1. GitHub 仓库 Secrets（Settings → Secrets and variables → Actions）
| Secret | 值 |
|--------|-----|
| `DOCKERHUB_USERNAME` | `wuenci514` |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌（拉取/推送都用到，避免 GitHub runner 被限流） |
| `CNB_TOKEN` | cnb.cool 访问令牌（个人设置 → 访问令牌，勾「制品库」） |
| `GITHUB_TOKEN` | 系统自动提供，**无需配置** |

> 没有 `CNB_TOKEN`，镜像就无法推到 cnb.cool；请务必在 CNB 后台生成后填入。

### 2. 本地一次性登录（仅本机手动跑脚本时需要）
```bash
docker login ghcr.io -u wuenci666 -p <GITHUB_PAT>
docker login docker.cnb.cool -u cnb -p <CNB_TOKEN>
```

---

## 三、路径 B：从 Docker Hub 自动镜像（你的主诉求）

工作流：`.github/workflows/sync-from-dockerhub.yml`

### 触发方式一：手动（最稳，推荐日常用）
1. 在 Docker Hub 完成 `docker push wuenci514/ysp-live:latest`（及版本标签）
2. 打开 GitHub → Actions → **sync-from-dockerhub** → **Run workflow**
3. 可选填 `tag`（默认 `latest`）；若填版本标签（如 `v9.5-G`），会额外同步 `latest`
4. 工作流自动把该镜像拉到 runner，re-tag 后推到 `ghcr.io/wuenci666/ysp-live` 与
   `docker.cnb.cool/wuenciteam/ysp-live-docker`

### 触发方式二：Docker Hub webhook 全自动（零点击）
让 Docker Hub 每次 push 自动调用 GitHub，彻底"更新一次、三处同步"：

1. 生成一个有 `repo` + `workflow` 权限的 GitHub PAT（不要用你日常那个，单独建一个
   `dockerhub-webhook` 用的 PAT，方便随时吊销）。
2. 在 Docker Hub 仓库 → **Webhooks** → **Add Webhook**：
   - URL：`https://api.github.com/repos/wuenci666/ysp-live-docker/dispatches`
   - Content Type：`application/json`
   - 触发事件：勾 **Repository push**（即镜像 push）
   - 该接口需要 Authorization 头，Docker Hub webhook 不支持自定义 header，
     因此需要一个**公开的接收端**转发。最简方案：部署一个 Cloudflare Worker
     （免费），把收到的 Docker Hub 请求转成带 PAT 的 `repository_dispatch`：

   ```js
   // Cloudflare Worker：接收 Docker Hub webhook → 触发 GitHub repository_dispatch
   const GH_OWNER = "wuenci666";
   const GH_REPO  = "ysp-live-docker";
   const GH_PAT   = "ghp_xxxx"; // 仅具 repo+workflow 权限的专用 PAT
   addEventListener("fetch", async (e) => {
     const body = await e.request.json().catch(() => ({}));
     const tag = (body?.push_data?.tag) || "latest";
     await fetch(`https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/dispatches`, {
       method: "POST",
       headers: {
         "Authorization": `Bearer ${GH_PAT}`,
         "Accept": "application/vnd.github+json",
         "Content-Type": "application/json",
       },
       body: JSON.stringify({ event_type: "dockerhub-sync", client_payload: { tag } }),
     });
     return new Response("ok");
   });
   ```
3. 把该 Worker 的公网地址填回 Docker Hub Webhook 的 URL。
4. 之后你在 Docker Hub 每 push 一次，GitHub 自动拉取并镜像到 ghcr + cnb。

> 嫌部署 Worker 麻烦？用方式一（手动 Run workflow）也只需点一下，效果等同。

---

## 四、路径 A：GitHub 一次构建、三处同发（替代方案）

工作流：`.github/workflows/build-push.yml`

- 触发：`push` 到 `main` / 打 `v*` tag / 手动 `workflow_dispatch` 填版本
- 多架构（amd64 + arm64）构建，**一次推送 Docker Hub + ghcr + cnb**
- 适合"以 GitHub 为发布源"的团队；构建上下文固定 `./ysp-live-docker-v9.5-G`，
  发新版时改成对应目录。

---

## 五、本地即时镜像脚本（无需等 CI）

脚本：`sync-images.sh` —— 直接把你本地 `wuenci514/ysp-live:*` re-tag 推到 ghcr + cnb。

```bash
./sync-images.sh                 # 同步本地全部标签
ONLY_LATEST=1 ./sync-images.sh   # 只同步 latest + v9.5-G + v9.4-G（日常推荐）
```

---

## 六、代码 / 文档同步（git 双推）

```bash
cd YSPDocker
git init
git add -A && git commit -m "chore: sync ysp-live docs & compose"
git remote add github https://github.com/wuenci666/ysp-live-docker.git
git remote add cnb    https://cnb.cool/wuenciteam/ysp-live-docker.git
git branch -M main
git push -u github main
git push -u cnb main          # 首次需配置 cnb 凭证助手：git config --global credential.https://cnb.cool.helper '!cnb git-credential'
```

以后：`git commit -am "..." && git push github main && git push cnb main`

> 🔒 安全提示：`使用说明书.html` 为**内部**文档，推到 public 仓库会公开。
> 若仓库设为 public 且不想外泄，建议 `git rm --cached 使用说明书.html` 并加入 `.gitignore`，
> 或把仓库改为 private。

---

## 七、拉取地址速查（给读者）

| 镜像源 | 命令 |
|--------|------|
| Docker Hub | `docker pull wuenci514/ysp-live:latest` |
| GitHub | `docker pull ghcr.io/wuenci666/ysp-live:latest` |
| CNB | `docker pull docker.cnb.cool/wuenciteam/ysp-live-docker:latest` |

国内优先 `docker.cnb.cool` 或 `ghcr.io`，速度通常优于 Docker Hub。
