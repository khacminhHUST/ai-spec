#!/bin/sh
# Thêm đường MCP "Thông tin dự án" cho Claude vào nginx web Tri thức (server 1) – docs/mcp-server.md mục 6.
#   https://trithuc.hawee.com.vn/thongtinduan/mcp                                   → app MEP (không qua _mep_auth)
#   https://trithuc.hawee.com.vn/.well-known/oauth-protected-resource/thongtinduan/mcp → app MEP (metadata OAuth)
# Chỉ THÊM 2 khối `location =` (khớp đúng 2 đường dẫn này) vào khối MEP đã có; trang, API, đăng nhập Tri thức và
# MCP ai-trithuc không đổi. Chạy lại nhiều lần không sao (đã có thì bỏ qua). Lỗi cú pháp → dừng, không đụng file.
#
# Chạy trên server 1:  sh server1-add-mcp.sh
# Gỡ:                  cat <file .bak-mcp-… in ra lúc chạy> > nginx.conf && docker exec nginx_proxy nginx -s reload
set -eu

CONF="${NGINX_CONF:-/home/ai-trithuc/hawee-deploy/nginx.conf}"
CT="${NGINX_CONTAINER:-nginx_proxy}"
MARK="# ----- MCP Thông tin dự án cho Claude -----"

die() { echo "LỖI: $*" >&2; exit 1; }
[ -f "$CONF" ] || die "không thấy $CONF (đặt NGINX_CONF=<đường dẫn nginx.conf>)"
docker inspect "$CT" >/dev/null 2>&1 || die "không thấy container $CT (đặt NGINX_CONTAINER=<tên>)"

if grep -qF "$MARK" "$CONF"; then
  echo "Đã có khối MCP trong $CONF – không làm gì."
  exit 0
fi

# IP:cổng app MEP lấy từ khối MEP đang chạy (proxy_pass của /thongtinduan/api/)
UPSTREAM=$(grep -oE 'proxy_pass http://[0-9.]+:8090' "$CONF" | head -n1 | sed 's#proxy_pass http://##')
[ -n "$UPSTREAM" ] || die "không tìm thấy khối MEP (proxy_pass http://<IP>:8090) trong $CONF"
echo "App MEP: $UPSTREAM"
docker exec "$CT" wget -qO- -T 5 "http://$UPSTREAM/healthz" >/dev/null || die "nginx_proxy không gọi được http://$UPSTREAM/healthz"

SNIP=$(mktemp)
cat > "$SNIP" <<'EOF'
        # ----- MCP Thông tin dự án cho Claude -----
        # Token do user-management cấp, app MEP tự verify → KHÔNG qua _mep_auth. `location =` chỉ khớp đúng đường dẫn.
        location = /thongtinduan/mcp {
            proxy_pass http://__UPSTREAM__;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_read_timeout 300s;
        }
        location = /.well-known/oauth-protected-resource/thongtinduan/mcp {
            proxy_pass http://__UPSTREAM__;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
        # ----- hết MCP Thông tin dự án -----
EOF
sed -i "s#__UPSTREAM__#$UPSTREAM#g" "$SNIP"

# Chèn ngay trước dòng "# ----- hết App MEP -----" (khối MEP 18/11); không có dòng đó → trước location ^~ /thongtinduan {
NEW=$(mktemp)
if grep -qF -- "# ----- hết App MEP -----" "$CONF"; then
  awk -v snip="$SNIP" '!done && index($0, "# ----- hết App MEP -----") { while ((getline l < snip) > 0) print l; done=1 } { print }' "$CONF" > "$NEW"
else
  awk -v snip="$SNIP" '!done && $0 ~ /^[[:space:]]*location \^~ \/thongtinduan[[:space:]]*\{/ { while ((getline l < snip) > 0) print l; done=1 } { print }' "$CONF" > "$NEW"
fi
grep -qF "$MARK" "$NEW" || die "không tìm được chỗ chèn trong $CONF"

docker cp "$NEW" "$CT:/tmp/nginx.conf.mcp"
docker exec "$CT" nginx -t -c /tmp/nginx.conf.mcp || die "cấu hình mới lỗi cú pháp – $CONF giữ nguyên"

BAK="$CONF.bak-mcp-$(date +%Y%m%d-%H%M)"
cp -p "$CONF" "$BAK"
cat "$NEW" > "$CONF"        # ghi ĐÈ NỘI DUNG (file bind-mount: cp/mv tạo inode mới → container vẫn đọc bản cũ)
docker exec "$CT" nginx -t && docker exec "$CT" nginx -s reload
rm -f "$SNIP" "$NEW"
echo "Đã thêm. Sao lưu: $BAK"

# Kiểm tra (app MEP phải đã deploy bản có MCP server)
PRM=$(curl -sk -m 10 --resolve trithuc.hawee.com.vn:443:127.0.0.1 \
  https://trithuc.hawee.com.vn/.well-known/oauth-protected-resource/thongtinduan/mcp || true)
CODE=$(curl -sk -m 10 -o /dev/null -w '%{http_code}' --resolve trithuc.hawee.com.vn:443:127.0.0.1 -X POST \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{}' \
  https://trithuc.hawee.com.vn/thongtinduan/mcp || true)
echo "Metadata: $PRM"
echo "MCP không token: $CODE (đúng: 401)"
case "$PRM" in *thongtinduan/mcp*) echo "OK – Claude kết nối được tới https://trithuc.hawee.com.vn/thongtinduan/mcp";;
  *) echo "Chưa thấy metadata: kiểm tra app MEP đã deploy bản mới chưa (curl có thể khác nếu nginx không nghe 443 trên host).";; esac
