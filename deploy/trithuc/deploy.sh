#!/bin/sh
# Deploy app MEP lên server 2 – CD (GitHub Actions self-hosted runner) gọi, chạy tay cũng được:
#   sh deploy/trithuc/deploy.sh <thư mục code mới>        (mặc định: thư mục repo chứa script này)
# Các bước: đồng bộ code → MEP_DIR (giữ .env) → build image → migrate → up -d → chờ api healthy.
# Hỏng ở build / migrate / health → gắn lại image cũ (:prev) và bật lại – dữ liệu (volume) không đụng tới.
set -eu

SRC="$(cd "${1:-$(dirname "$0")/../..}" && pwd)"
MEP_DIR="${MEP_DIR:-$HOME/mep}"
APP="$MEP_DIR/deploy/trithuc"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-600}"   # api lần đầu tải mô hình có thể lâu

log() { echo "== $(date '+%H:%M:%S') $*"; }

[ -f "$APP/.env" ] || { echo "Thiếu $APP/.env (cài lần đầu theo README mục 3)"; exit 1; }
command -v rsync >/dev/null || { echo "Thiếu rsync: sudo apt install -y rsync"; exit 1; }

if [ "$SRC" != "$MEP_DIR" ]; then
  log "Đồng bộ code $SRC → $MEP_DIR"
  # --delete không xóa đường dẫn bị exclude (.env, dữ liệu local)
  rsync -a --delete \
    --exclude .git --exclude .github --exclude .env --exclude '**/.env' --exclude .venv \
    --exclude docker-compose.override.yml \
    --exclude frontend/node_modules --exclude frontend/dist --exclude artifacts --exclude data \
    --exclude '__pycache__' --exclude '*.pdf' --exclude '~$*' \
    "$SRC/" "$MEP_DIR/"
fi
echo "${GITHUB_SHA:-manual} $(date '+%F %T')" > "$MEP_DIR/.deploy-rev"

cd "$APP"
docker image tag mep-app:latest mep-app:prev 2>/dev/null || true
docker image tag mep-gateway:latest mep-gateway:prev 2>/dev/null || true

rollback() {
  log "LỖI → quay về image trước (:prev)"
  if docker image inspect mep-app:prev >/dev/null 2>&1; then
    docker image tag mep-app:prev mep-app:latest
    docker image tag mep-gateway:prev mep-gateway:latest
    docker compose up -d --no-build
    log "Đã quay về bản trước"
  else
    log "Không có image :prev – để nguyên hiện trạng"
  fi
  exit 1
}

log "Build image sandbox"
sh "$MEP_DIR/sandbox/build.sh" || rollback
log "Build image app + gateway"
docker compose build api gateway || rollback

log "Migrate (chỉ áp migration mới)"
docker compose up -d postgres qdrant
docker compose run --rm --no-deps api python -m app.cli.migrate || rollback

log "Khởi động lại dịch vụ (job ingest đang chạy → FAILED, bấm Chạy lại)"
docker compose up -d --no-build || rollback   # gateway chờ api healthy

log "Chờ api healthy (tối đa ${HEALTH_TIMEOUT_S}s)"
api="$(docker compose ps -q api)"
t=0
while :; do
  st="$(docker inspect -f '{{.State.Health.Status}}' "$api" 2>/dev/null || echo none)"
  [ "$st" = healthy ] && break
  [ "$st" = unhealthy ] || [ "$t" -ge "$HEALTH_TIMEOUT_S" ] && { docker compose logs --tail 80 api; rollback; }
  sleep 5; t=$((t + 5))
done

docker compose ps
docker image prune -f >/dev/null
log "Deploy xong: $(cat "$MEP_DIR/.deploy-rev" 2>/dev/null || echo '?')"
