#!/bin/sh
# Kiểm tra server 2 trước khi deploy:  sh deploy/trithuc/preflight.sh   (chạy từ thư mục gốc repo)
# Chỉ đọc, không cài / sửa gì. Mỗi dòng: OK / LỖI / CẢNH BÁO.
cd "$(dirname "$0")" || exit 1
fail=0
ok()   { echo "  OK        $*"; }
bad()  { echo "  LỖI       $*"; fail=1; }
warn() { echo "  CẢNH BÁO  $*"; }

echo "== Docker"
if docker info >/dev/null 2>&1; then ok "Docker Engine $(docker version -f '{{.Server.Version}}')"; else bad "không gọi được Docker (cài Docker / thêm user vào nhóm docker)"; fi
if docker compose version >/dev/null 2>&1; then ok "$(docker compose version --short 2>/dev/null | sed 's/^/compose /')"; else bad "thiếu plugin docker compose"; fi
if docker image inspect mep-sandbox:latest >/dev/null 2>&1; then ok "image mep-sandbox:latest"; else warn "chưa có mep-sandbox:latest → chạy: sh sandbox/build.sh (agent không xuất file được nếu thiếu)"; fi

echo "== Tài nguyên"
mem=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null)
[ -n "$mem" ] && { [ "$mem" -ge 15 ] && ok "RAM ${mem} GB" || warn "RAM ${mem} GB (< 16 GB: Docling/OCR có thể thiếu bộ nhớ)"; }
disk=$(df -BG --output=avail "$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /)" 2>/dev/null | tail -1 | tr -dc 0-9)
[ -n "$disk" ] && { [ "$disk" -ge 50 ] && ok "ổ trống ${disk} GB" || warn "ổ trống ${disk} GB (< 50 GB)"; }

echo "== Mạng ra ngoài"
for u in https://generativelanguage.googleapis.com https://huggingface.co https://registry-1.docker.io https://pypi.org https://registry.npmjs.org; do
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$u" 2>/dev/null)
  [ "$code" != "000" ] && ok "$u ($code)" || bad "không tới được $u (internet / proxy công ty?)"
done

echo "== Cổng"
if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":${GATEWAY_PORT:-8090} "; then warn "cổng ${GATEWAY_PORT:-8090} đang bị chiếm (đổi GATEWAY_PORT trong .env)"; else ok "cổng ${GATEWAY_PORT:-8090} trống"; fi
if command -v iptables >/dev/null && sudo -n iptables -S DOCKER-USER 2>/dev/null | grep -q "dport ${GATEWAY_PORT:-8090}"; then ok "đã có luật DOCKER-USER cho cổng ${GATEWAY_PORT:-8090}"; else warn "chưa thấy luật DOCKER-USER chặn cổng ${GATEWAY_PORT:-8090} (README mục 2) – gateway vẫn tự allow/deny"; fi

echo "== .env"
if [ ! -f .env ]; then bad "thiếu deploy/trithuc/.env (cp .env.example .env)"; else
  for k in AUTH_TOKEN_KEY POSTGRES_PASSWORD GOOGLE_API_KEY PUBLIC_ORIGIN ALLOW_FROM; do
    v=$(grep -E "^$k=" .env | tail -1 | cut -d= -f2-)
    [ -n "$v" ] && ok "$k đã điền" || bad "$k còn trống"
  done
  v=$(grep -E "^MEP_SECRET_KEY=" .env | cut -d= -f2-); [ -n "$v" ] || warn "MEP_SECRET_KEY trống (chỉ cần khi kết nối nguồn MCP OAuth)"
  pw=$(grep -E "^POSTGRES_PASSWORD=" .env | cut -d= -f2-)
  echo "$pw" | grep -q '[^A-Za-z0-9]' && bad "POSTGRES_PASSWORD có ký tự đặc biệt (nằm trong URL kết nối) – chỉ dùng chữ và số"
  docker compose config -q 2>/dev/null && ok "docker compose config hợp lệ" || bad "docker compose config lỗi (chạy: docker compose config)"
fi

echo
[ $fail -eq 0 ] && echo "Sẵn sàng: docker compose build && (README mục 3)" || echo "Còn LỖI – sửa rồi chạy lại."
exit $fail
