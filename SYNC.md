# YSP-Live 镜像自动同步（Docker Hub → GitHub + CNB）

> 本仓库仅用于存放镜像同步自动化配置。Docker 镜像制品本身托管在：
> - Docker Hub：`wuenci514/ysp-live`
> - GitHub：`ghcr.io/wuenci666/ysp-live`
> - CNB：`docker.cnb.cool/wuenciteam/ysp-live-docker`

## 目标

每次更新 **Docker Hub** 后，自动把同一份镜像同步到 **GitHub Container Registry (ghcr)** 与 **CNB 制品库**。

## 已配置的工作流

`.github/workflows/sync-from-dockerhub.yml`

触发方式：
1. **手动**：GitHub → Actions → `sync-from-dockerhub` → **Run workflow**
2. **自动**：Docker Hub webhook → GitHub `repository_dispatch`

工作流做的事情：
1. 登录 ghcr（用 `GITHUB_TOKEN`，自带 `packages:write`）
2. 登录 cnb（用 `CNB_TOKEN`）
3. 从 Docker Hub API 拉取**该仓库全部标签**（v9.4-G / v9.5-G / latest …），逐一
4. 从 Docker Hub 拉取每个标签，re-tag 后推送到 `ghcr.io/wuenci666/ysp-live` 与 `docker.cnb.cool/wuenciteam/ysp-live-docker`

> 即「更新 Docker Hub 一次 → CNB 与 GitHub 全部版本自动同步」。

## 必须配置的 Secrets

打开 GitHub 仓库 `Settings → Secrets and variables → Actions → New repository secret`：

| Secret | 值 | 说明 |
|--------|-----|------|
| `DOCKERHUB_USERNAME` | `wuenci514` | 避免 GitHub runner 匿名拉 Docker Hub 被限流 |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌 | 同上 |
| `CNB_TOKEN` | cnb.cool 访问令牌 | 个人设置 → 访问令牌，勾选「制品库」 |

> `GITHUB_TOKEN` 由系统自动提供，无需手动配置。

## 全自动：Docker Hub webhook（可选）

想完全零点击？在 Docker Hub 仓库 → **Webhooks** 配置一个接收端：

1. 生成一个 GitHub PAT（仅需要 `repo` + `workflow` 权限，建议单独建一个，方便吊销）。
2. 部署一个 Cloudflare Worker（免费）作为接收端，代码见下方。
3. 把 Worker 公网地址填到 Docker Hub Webhook URL。

```js
// Cloudflare Worker：接收 Docker Hub webhook → 触发 GitHub repository_dispatch
const GH_OWNER = "wuenci666";
const GH_REPO  = "ysp-live-docker";
const GH_PAT   = "ghp_xxxxxxxx"; // 专用 PAT（repo + workflow）

export default {
  async fetch(request) {
    const body = await request.json().catch(() => ({}));
    const tag = body?.push_data?.tag || "latest";
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
  }
};
```

## 手动立刻同步

Secrets 配好后，点一次 Actions → `sync-from-dockerhub` → Run workflow 即可。

## 拉取地址

| 源 | 命令 |
|----|------|
| Docker Hub | `docker pull wuenci514/ysp-live:latest` |
| GitHub | `docker pull ghcr.io/wuenci666/ysp-live:latest` |
| CNB | `docker pull docker.cnb.cool/wuenciteam/ysp-live-docker:latest` |

国内优先用 CNB 或 ghcr。
