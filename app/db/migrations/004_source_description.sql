-- Mô tả ngắn của connector cho danh mục nguồn của agent (docs/agent-harness.md mục 3.3).
-- Tự điền khi thử kết nối thành công lần đầu (giới thiệu server tự trả → LLM tóm tắt danh sách tool), sửa được trên giao diện.
ALTER TABLE mcp_connectors ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
