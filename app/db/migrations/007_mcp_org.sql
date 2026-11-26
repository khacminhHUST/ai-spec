-- Connector dùng chung cho cả tổ chức (docs/mcp-connectors.md mục 0): `shared = true` → mọi người đều thấy và tự bấm
-- Kết nối (token vẫn riêng từng người, bảng mcp_user_tokens). Chỉ ADMIN web Tri thức thêm / sửa / xóa / đặt quyền tool.
-- Connector cá nhân (shared = false) giữ nguyên như 006_mcp_per_user.sql.
ALTER TABLE mcp_connectors ADD COLUMN IF NOT EXISTS shared boolean NOT NULL DEFAULT false;

-- Tên connector dùng chung là duy nhất trong tổ chức (tên = tiền tố tool của agent)
CREATE UNIQUE INDEX IF NOT EXISTS mcp_connectors_shared_name_idx ON mcp_connectors (name) WHERE shared;
