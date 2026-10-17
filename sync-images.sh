#!/usr/bin/env bash
# ============================================================================
# sync-images.sh
# 把本地 wuenci514/ysp-live 的全部（或最新）标签，re-tag 并推送至：
#   - GitHub Container Registry : ghcr.io/<GHCR_NS>/ysp-live
#   - CNB Docker 制品库          : docker.cnb.cool/<CNB_NS>/ysp-live-docker
# Docker Hub 本就登录着 wuenci514，无需再登。
#
# 用法：
#   ./sync-images.sh                 # 同步本地全部标签（首次全量）
#   ONLY_LATEST=1 ./sync-images.sh   # 只同步 latest + v9.5-G（日常推荐）
#
# 前置（只需在各自机器做一次）：
#   docker login ghcr.io -u <github-user> -p <GITHUB_PAT>
#   docker login docker.cnb.cool -u cnb -p <CNB_TOKEN>
# ============================================================================
set -euo pipefail

SRC="wuenci514/ysp-live"

# —— 目标命名空间（已按实际账号修正）——
# GitHub 账号：wuenci666  -> ghcr.io/wuenci666/ysp-live
# CNB   账号：wuenciteam  -> docker.cnb.cool/wuenciteam/ysp-live-docker
#   注意：cnb.cool 的镜像地址必须与仓库名一致，故此处为 ysp-live-docker；
#        ghcr / Docker Hub 沿用产品名 ysp-live，三者仅 cnb 段名不同。
GHCR_NS="wuenci666"
CNB_NS="wuenciteam"
GHCR_REPO="ghcr.io/${GHCR_NS}/ysp-live"
CNB_REPO="docker.cnb.cool/${CNB_NS}/ysp-live-docker"

if [[ "${ONLY_LATEST:-0}" == "1" ]]; then
  TAGS="latest v9.5-G v9.4-G"
else
  # 取本地该仓库所有去重标签
  TAGS=$(docker images --format '{{.Tag}}' "$SRC" | grep -v '^<none>$' | sort -u)
fi

if [[ -z "${TAGS// /}" ]]; then
  echo "❌ 本地没有 $SRC 的镜像，请先 docker pull 或本地构建。"
  exit 1
fi

echo "将同步以下标签到 ghcr.io 与 docker.cnb.cool："
echo "$TAGS" | sed 's/^/  - /'

for TAG in $TAGS; do
  echo "==> [$TAG] tag & push"
  docker tag "$SRC:$TAG" "$GHCR_REPO:$TAG"
  docker push "$GHCR_REPO:$TAG"
  docker tag "$SRC:$TAG" "$CNB_REPO:$TAG"
  docker push "$CNB_REPO:$TAG"
done

echo "✅ 同步完成"
echo "  ghcr.io : $GHCR_REPO"
echo "  cnb.cool: $CNB_REPO"
