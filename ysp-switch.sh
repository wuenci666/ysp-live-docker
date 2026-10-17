#!/usr/bin/env bash
# ============================================================
#  ysp-switch.sh —— 央视频直播 Docker 版「多版本管理器」
#  14 个版本各自一个容器（ysp-live-v3.0 ~ ysp-live-v9.0-G），端口都映射到宿主 8767
#  一次只能跑一个（8767 只能被一个进程独占），切版本时先停掉其他所有容器
#
#  ★ 最新版 = v9.0-G（64 路：央视 30 + 卫视 34）
#    latest 标签（本地 ysp-live:latest 与 Hub wuenci514/ysp-live:latest）
#    恒定指向 v9.0-G，由下面的 LATEST_VERSION 控制，改这一行即可整体切换。
#
#  常用命令（Git Bash / 双击 ysp-switch.bat 均可）：
#    bash ysp-switch.sh list                 查看版本 & 本地镜像 & 容器状态
#    bash ysp-switch.sh latest               切到最新版 v9.0-G 并启动（推荐）
#    bash ysp-switch.sh build-all            构建全部 14 个版本镜像
#    bash ysp-switch.sh build v9.0           只构建某一个版本
#    bash ysp-switch.sh create-all           预创建 14 个容器（停止态，界面可点 ▶）
#    bash ysp-switch.sh v9.0-G               切换到 v9.0-G 并启动
#    bash ysp-switch.sh stop                 停止所有容器（容器保留，可再点 ▶）
#    bash ysp-switch.sh status               运行状态 + 订阅地址探测
#    bash ysp-switch.sh logs                 跟踪日志
#    bash ysp-switch.sh push <用户名>         推送全部镜像到 Docker Hub
#    bash ysp-switch.sh push <用户名> v9.0-G  只推送指定版本
# ============================================================

set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_PORT=8767
STATE_FILE="$BASE/.current-version"
CONTAINER_PREFIX="ysp-live-"       # 容器名前缀，形如 ysp-live-v9.0
IMAGE_PREFIX="ysp-live"
LATEST_VERSION="v9.0-G"            # ★ 最新版：latest 标签恒定指向它

# ---- 定位 docker 可执行文件（Docker Desktop 不一定在 PATH 里）----
if ! command -v docker >/dev/null 2>&1; then
  for p in \
    "/c/Users/Administrator/AppData/Local/Programs/DockerDesktop/resources/bin" \
    "/c/Program Files/Docker/Docker/resources/bin"
  do
    if [ -d "$p" ]; then export PATH="$p:$PATH"; break; fi
  done
fi

C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GRN=$'\033[32m'
C_YEL=$'\033[33m'; C_CYA=$'\033[36m'; C_DIM=$'\033[2m'
info() { printf '%s[·]%s %s\n' "$C_CYA" "$C_RESET" "$*"; }
ok()   { printf '%s[✓]%s %s\n' "$C_GRN" "$C_RESET" "$*"; }
warn() { printf '%s[!]%s %s\n' "$C_YEL" "$C_RESET" "$*"; }
err()  { printf '%s[✗]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; }

# ---- 可用版本（按目录自动发现）----
VERSIONS=()
for d in "$BASE"/ysp-live-docker-v*; do
  [ -d "$d" ] || continue
  VERSIONS+=("$(basename "$d" | sed 's/^ysp-live-docker-//')")
done
if [ ${#VERSIONS[@]} -eq 0 ]; then
  err "在 $BASE 下没找到任何 ysp-live-docker-v* 目录"; exit 1
fi

# ---- 解析 compose 目录（三种布局）----
compose_dir() {
  local v="$1" root="$BASE/ysp-live-docker-$v" found
  if [ -f "$root/docker-compose.yml" ]; then echo "$root"; return 0; fi
  found="$(find "$root" -maxdepth 2 -name docker-compose.yml \
             -not -path "*/__MACOSX/*" 2>/dev/null | head -1)"
  if [ -n "$found" ]; then echo "$(dirname "$found")"; return 0; fi
  return 1
}

ctr_name()  { echo "$CONTAINER_PREFIX$1"; }
image_tag() { echo "$IMAGE_PREFIX:$1"; }
current_version() { [ -f "$STATE_FILE" ] && head -n1 "$STATE_FILE" | tr -d ' \r\n'; }
image_exists() { docker image inspect "$(image_tag "$1")" >/dev/null 2>&1; }

normalize() {
  local raw="${1#v}" cand
  for cand in "${VERSIONS[@]}"; do [ "${cand#v}" = "$raw" ] && { echo "$cand"; return 0; }; done
  for cand in "${VERSIONS[@]}"; do [ "${cand#v}" = "$raw.0" ] && { echo "$cand"; return 0; }; done
  return 1
}

# 列出本项目所有容器（含已停止的），按名字精确匹配前缀
all_containers() {
  local n
  for n in $(docker ps -a --format '{{.Names}}' 2>/dev/null); do
    case "$n" in "${CONTAINER_PREFIX}"*) echo "$n";; esac
  done
}

# 停止所有容器 —— 不删除，保留在 Docker Desktop 列表里方便下次点 ▶
stop_all() {
  local n stopped=0
  for n in $(all_containers); do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$n"; then
      info "停止 $n ..."
      docker stop "$n" >/dev/null 2>&1 && stopped=1
    fi
  done
  if [ $stopped -eq 1 ]; then ok "已停止（容器保留，界面里可再次点 ▶）"
  else info "没有运行中的容器"; fi
}

# ---- 订阅路径探测（各版本不同）----
SUBSCRIBE_PATHS=("/all.m3u" "/cctv.m3u" "/ysp.m3u" "/live.m3u")
SUB_PATH=""
probe_subscribe() {
  local p code
  for p in "${SUBSCRIBE_PATHS[@]}"; do
    code="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://localhost:$HOST_PORT$p" 2>/dev/null)"
    if [ "$code" = "200" ]; then SUB_PATH="$p"; return 0; fi
  done
  return 1
}

wait_ready() {
  local i
  for i in $(seq 1 90); do
    if probe_subscribe; then ok "服务已就绪（约 ${i}s）"; return 0; fi
    sleep 1
  done
  warn "90 秒内订阅地址没就绪，用 'bash $(basename "$0") logs' 看日志排查"
  return 1
}

do_build() {
  local raw="$1" v dir
  v="$(normalize "$raw")" || { err "未知版本：$raw"; return 1; }
  dir="$(compose_dir "$v")" || { err "找不到 $v 的 docker-compose.yml"; return 1; }
  printf '\n%s----- 构建 %s -----%s\n' "$C_CYA" "$v" "$C_RESET"
  ( cd "$dir" && docker compose build ) || { err "$v 构建失败"; return 1; }
  ok "$(image_tag "$v") 构建完成"
}

do_build_all() {
  local v fail=0
  info "开始构建全部 ${#VERSIONS[@]} 个版本"
  for v in "${VERSIONS[@]}"; do do_build "$v" || fail=1; done
  printf '\n'
  [ $fail -eq 0 ] && ok "全部构建完成" || err "有版本构建失败"
  do_list
  return $fail
}

# 预创建全部容器（停止状态，端口已映射，方便在 Docker Desktop 里点 ▶ 直接用）
do_create_all() {
  local v dir
  info "预创建 ${#VERSIONS[@]} 个容器（停止状态，宿主 8767 已映射）..."
  for v in "${VERSIONS[@]}"; do
    dir="$(compose_dir "$v")" || continue
    if ( cd "$dir" && docker compose up --no-start >/dev/null 2>&1 ); then
      ok "$(ctr_name "$v") 已就绪"
    else
      err "$(ctr_name "$v") 创建失败"
    fi
  done
  printf '\n'
  warn "${#VERSIONS[@]} 个容器都映射了宿主 8767，同时只能启动一个"
  warn "在 Docker Desktop 点 ▶ 之前，请先把其他 ysp-live-v* 容器停掉"
}

do_run() {
  local raw="$1" v dir
  case "$raw" in
    latest|new|最新版) raw="$LATEST_VERSION" ;;
  esac
  v="$(normalize "$raw")" || { err "未知版本：$raw（可用：${VERSIONS[*]}）"; exit 1; }

  local cur; cur="$(current_version)"
  if [ "$cur" = "$v" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$(ctr_name "$v")"; then
    ok "$v 已在运行中"; do_status; return 0
  fi

  if ! image_exists "$v"; then
    warn "本地没有 $(image_tag "$v")，先构建..."; do_build "$v" || exit 1
  fi

  stop_all >/dev/null 2>&1

  dir="$(compose_dir "$v")" || { err "找不到 $v 的 docker-compose.yml"; exit 1; }
  mkdir -p "$dir/data" 2>/dev/null

  printf '\n%s===== 启动 %s =====%s\n' "$C_CYA" "$v" "$C_RESET"
  ( cd "$dir" && docker compose up -d ) || {
    err "启动失败，可能 $HOST_PORT 端口被占用"; exit 1; }

  echo "$v" > "$STATE_FILE"
  SUB_PATH=""
  wait_ready
  printf '\n'
  if [ -n "$SUB_PATH" ]; then ok "订阅地址：http://localhost:$HOST_PORT$SUB_PATH"
  else
    warn "没探到订阅路径，逐个试："
    local p; for p in "${SUBSCRIBE_PATHS[@]}"; do echo "    http://localhost:$HOST_PORT$p"; done
  fi
  ok "健康检查：http://localhost:$HOST_PORT/health"
  ok "诊断日志：http://localhost:$HOST_PORT/diag"
  printf '%s提示：看日志执行  bash %s logs%s\n\n' "$C_DIM" "$(basename "$0")" "$C_RESET"
}

do_status() {
  echo "当前记录版本：$(current_version)"
  [ -z "$(current_version)" ] && echo "            （无）"
  echo "---- 容器 ----"
  docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
    | grep -E "NAMES|${CONTAINER_PREFIX}"
  echo "---- 端口探测 ----"
  if probe_subscribe; then ok "订阅地址 http://localhost:$HOST_PORT$SUB_PATH 返回 200"
  else warn "http://localhost:$HOST_PORT 无可用订阅地址"; fi
}

do_list() {
  printf '%-8s %-22s %s\n' "版本" "本地镜像" "容器状态"
  echo "--------------------------------------------------------"
  local v cur img st cname
  cur="$(current_version)"
  for v in "${VERSIONS[@]}"; do
    if image_exists "$v"; then
      img="$(docker images --format '{{.Size}}' "$(image_tag "$v")" 2>/dev/null | head -1)"
      img="ysp-live:$v ($img)"
    else img="— 未构建"; fi
    cname="$(ctr_name "$v")"
    st="$(docker ps -a --filter "name=^${cname}$" --format '{{.Status}}' 2>/dev/null | head -1)"
    [ -z "$st" ] && st="容器未创建"
    [ "$v" = "$cur" ] && st="$st ${C_GRN}← 当前${C_RESET}"
    if [ "$v" = "$LATEST_VERSION" ]; then
      printf '%-8s %-22s %s %s\n' "$v ★最新" "$img" "$st" ""
    else
      printf '%-8s %-22s %s\n' "$v" "$img" "$st"
    fi
  done
  echo
  echo "最新版：$LATEST_VERSION（latest 标签恒定指向它）"
  echo "用法：bash $(basename "$0") latest  |  v9.0-G  |  create-all  |  push <DockerHub用户名>"
}

do_logs() {
  local cur; cur="$(current_version)"
  [ -z "$cur" ] && { err "没有记录的运行版本"; exit 1; }
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$(ctr_name "$cur")"; then
    info "跟踪 $(ctr_name "$cur") 日志（Ctrl+C 退出）"
    docker logs -f "$(ctr_name "$cur")"
  else err "容器 $(ctr_name "$cur") 不存在"; exit 1; fi
}

do_push() {
  local user="$1" only="${2:-}" v remote
  if [ -z "$user" ]; then err "用法：bash $(basename "$0") push <DockerHub用户名> [版本]"; exit 1; fi

  local logged helper
  logged="$(docker info 2>/dev/null | grep -i 'Username:' | head -1 | awk '{print $2}' | tr -d '\r')"
  if [ -z "$logged" ]; then
    for helper in docker-credential-desktop.exe docker-credential-desktop; do
      if command -v "$helper" >/dev/null 2>&1; then
        logged="$(echo "https://index.docker.io/v1/" | "$helper" get 2>/dev/null \
                  | sed -n 's/.*"Username":"\([^"]*\)".*/\1/p')"
        [ -n "$logged" ] && break
      fi
    done
  fi
  if [ -z "$logged" ]; then
    err "Docker 尚未登录，push 必然被拒（insufficient_scope）"
    cat <<EOF

  请先执行：
      docker login
  用户名填 Docker Hub 用户名，密码填 **Access Token**（不是账号密码！）
  Token 在这里生成： https://hub.docker.com/settings/security

EOF
    exit 1
  fi
  if [ "$logged" != "$user" ]; then
    err "登录用户名是「$logged」，但你要推送到「$user」，不一致会被拒绝"
    err "请改成：bash $(basename "$0") push $logged"; exit 1
  fi
  ok "已登录为 $logged"

  local list=("${VERSIONS[@]}")
  [ -n "$only" ] && { local x; x="$(normalize "$only")" || { err "未知版本：$only"; exit 1; }; list=("$x"); }

  for v in "${list[@]}"; do
    if ! image_exists "$v"; then warn "跳过 $v（本地无镜像，先 build）"; continue; fi
    remote="$user/$IMAGE_PREFIX:$v"
    info "标记 $(image_tag "$v") -> $remote"
    docker tag "$(image_tag "$v")" "$remote" || { err "标记失败"; continue; }
    if docker push "$remote"; then ok "已推送 $remote"
    else
      err "推送失败：$remote"
      warn "若提示 repository does not exist，先去 https://hub.docker.com/repositories 手动创建"
      return 1
    fi
  done
  # latest 恒定指向 LATEST_VERSION（v9.0-G），不跟随列表末位
  if [ -z "$only" ] && image_exists "$LATEST_VERSION"; then
    docker tag "$(image_tag "$LATEST_VERSION")" "$user/$IMAGE_PREFIX:latest"
    docker push "$user/$IMAGE_PREFIX:latest" && ok "已推送 $user/$IMAGE_PREFIX:latest（= $LATEST_VERSION）"
    # 本地 latest 同步指向最新版，Docker Desktop 里一眼能认出
    docker tag "$(image_tag "$LATEST_VERSION")" "$IMAGE_PREFIX:latest" \
      && ok "本地 $IMAGE_PREFIX:latest 已指向 $LATEST_VERSION"
  fi
}

cmd="${1:-}"
case "$cmd" in
  ""|list|ls)           do_list ;;
  latest|new|最新版)     do_run "$LATEST_VERSION" ;;
  status|st)            do_status ;;
  stop|down)            stop_all; rm -f "$STATE_FILE" ;;
  logs|log)             do_logs ;;
  build)                [ -n "${2:-}" ] && do_build "$2" || { err "用法：build <版本>"; exit 1; } ;;
  build-all|buildall)   do_build_all ;;
  create-all|createall) do_create_all ;;
  push)                 do_push "${2:-}" "${3:-}" ;;
  -h|--help|help)       sed -n '2,22p' "$0" ;;
  *)                    do_run "$cmd" ;;
esac
