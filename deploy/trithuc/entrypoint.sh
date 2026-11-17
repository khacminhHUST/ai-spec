#!/bin/sh
# Chỉnh config theo môi trường trước khi chạy (không sửa file trong repo, chỉ bản trong container).
set -e

# Callback OAuth của nguồn MCP phải quay về đúng địa chỉ công khai:
#   redirect_uri = <public_base_url>/mcp/oauth/callback  (app/services/agent/mcp/hub.py)
if [ -n "$MCP_PUBLIC_BASE_URL" ]; then
  sed -i "s#^public_base_url:.*#public_base_url: ${MCP_PUBLIC_BASE_URL}#" /app/config/mcp.yaml
fi

mkdir -p /app/data/uploads /app/artifacts "$HF_HOME" "$MEP_EASYOCR_DIR"
exec "$@"
