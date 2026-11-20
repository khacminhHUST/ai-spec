-- Connector MCP riêng theo người dùng (docs/mcp-connectors.md mục 4): created_by = chủ sở hữu (email web Tri thức).
-- Chỉ chủ sở hữu thấy, sửa, kết nối và dùng connector của mình; quyền tool (mcp_tool_overrides) theo connector nên
-- cũng thành riêng. Tên connector chỉ cần không trùng trong phạm vi 1 người.

-- Connector cũ không rõ người tạo: xóa (token, override, state đi kèm xóa theo ON DELETE CASCADE)
DELETE FROM mcp_connectors WHERE created_by IS NULL OR btrim(created_by) = '';

ALTER TABLE mcp_connectors ALTER COLUMN created_by SET NOT NULL;
ALTER TABLE mcp_connectors DROP CONSTRAINT IF EXISTS mcp_connectors_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS mcp_connectors_owner_name_idx ON mcp_connectors (created_by, name);
