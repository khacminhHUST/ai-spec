# MCP server "Thông tin dự án" cho Claude

Đường thứ hai để tra cứu tài liệu dự án: Claude (claude.ai / Desktop / Code) kết nối như một connector MCP.
UI và agent cũ **không đổi** (kể cả connector MCP riêng theo người dùng trong UI).

| | UI MEP (cũ) | MCP server cho Claude (mới) |
|---|---|---|
| Ai trả lời | Agent MEP (Gemini) | Claude |
| Tra tài liệu | tool `search_project_docs` của agent | tool `search_project_docs` (cùng code) |
| Gọi MCP khác (mail, nhân sự…) | MEP gọi (connector trong UI) | Claude tự gọi (connector của Claude) |
| Đăng nhập | token web Tri thức | OAuth HAWEE (user-management) |
| Ghi dữ liệu | upload/sửa kho tri thức, hội thoại | **không** – chỉ đọc, không lưu hội thoại |

## 1. Địa chỉ
- MCP: `https://trithuc.hawee.com.vn/thongtinduan/mcp` (streamable HTTP, stateless, trả JSON).
- Metadata (RFC 9728): `https://trithuc.hawee.com.vn/.well-known/oauth-protected-resource/thongtinduan/mcp`
  → `authorization_servers: ["https://user-management.hawee.com.vn"]`.
- Trong app: `/kb-mcp` (config `path`); metadata ở `/.well-known/oauth-protected-resource<path của resource_url>`.

Đường đi: Claude → edge → nginx Tri thức (server 1, 2 khối `location =`, không qua `_mep_auth`) → gateway MEP
(`/thongtinduan/mcp` → `api:8000/kb-mcp`) → app.

## 2. Tool (chỉ đọc, `readOnlyHint`)
| Tool | Vào | Ra |
|---|---|---|
| `search_project_docs` | `question`, `project_mentions?`, `disciplines?`, `streams?` | JSON như tool của agent: `status` (OK / PARTIAL / NOT_FOUND / ASK_CLARIFY), `projects`, `blocks` (n, project, stream, priority, doc, rev, section, pages, vi/en), `citations`, `missing?`, `clarify_options?` |
| `list_projects` | – | `projects`: project_id, name, aliases, governing_language |
| `list_project_documents` | `project_mentions?`, `streams?`, `disciplines?` | `projects`: project_id, name, n_documents, by_stream, `documents` (title, stream, disciplines, building, revision, languages) – chỉ bản đang hiệu lực |

- `list_project_documents` (từ 25/11/2026): để Claude biết kho có những tài liệu nào / thiếu bộ môn nào. Trước đó
  Claude chỉ có tìm kiếm nên suy ra nội dung kho từ vài kết quả trả về và kết luận sai (VD "CT05 chỉ có thuyết minh
  cơ điện" trong khi kho có đủ spec các bộ môn). `project_mentions` khớp mã / tên / alias (bỏ dấu, không phân biệt hoa).

- Chạy Query Pipeline N1–N6 (không N7) qua `search_payload` (dùng chung với agent, `app/services/agent/tools/search_docs.py`).
- Kết quả > `result_max_tokens` → cắt nội dung từng block (`truncated=true`) + `note`.
- Mỗi lần gọi độc lập: không lịch sử, không dự án đang chọn (Claude truyền `project_mentions`). Quyền dự án theo
  `user_id` = email trong token, như UI.

## 3. Xác thực (D:/ai-planner/MCP_SERVER_INTEGRATION_GUIDE.md)
- Không token / token hỏng → 401 + `WWW-Authenticate: Bearer … resource_metadata="<metadata URL>"` → Claude tự
  khám phá user-management, tự đăng ký client (DCR), mở trang đăng nhập.
- Verify: RS256 ghim cứng; khóa theo `kid` (khóa công khai nhúng sẵn `planner_key.py`, kid `391bb239b2493539` –
  không gọi mạng vì server MEP không ra được edge; kid lạ → JWKS); `iss` = issuer; `aud` chứa `resource_url` hoặc
  `server_name`; `exp`/`iat`; có `sub` + `email`.
- Quyền: `permissions[<resource_url | server_name>]` phải có `require_permission` (mặc định `read`). Thiếu → lỗi
  ở tầng tool ("chưa được cấp quyền…"), kết nối vẫn giữ. `require_permission: ""` → chỉ cần token hợp lệ.
- `TrustedProxyAuth` (token Tri thức) bỏ qua `/kb-mcp` và `/.well-known/oauth-protected-resource/`.

## 4. Mã nguồn
`app/services/mcp_server/` (config, auth, planner_key, server) · gắn vào `app/main.py` bằng middleware
`McpServerRoute` (chỉ 2 đường dẫn, còn lại đi FastAPI) · `config/mcp_server.yaml` · test `tests/mcp_server/`.

## 5. Cấu hình
`config/mcp_server.yaml`: `enabled`, `path`, `resource_url`, `server_name` (`ai-thongtinduan`), `issuer`, `jwks_url`,
`leeway_s`, `require_permission`, `result_max_tokens`. Env ghi đè: `MCP_SERVER_ENABLED`, `MCP_SERVER_RESOURCE_URL`
(deploy: `${PUBLIC_ORIGIN}${PUBLIC_PREFIX}/mcp`), `MCP_SERVER_PUBLIC_KEY_PEM` (khi user-management đổi khóa),
`MCP_SERVER_REQUIRE_PERMISSION`.

## 6. Triển khai (1 lần)
1. Push `main` → CI/CD deploy app MEP (gateway + api có MCP server).
2. Server 1: `sh deploy/trithuc/server1-add-mcp.sh` – thêm 2 khối `location =` vào nginx Tri thức (tự lấy IP app
   MEP từ khối MEP đang có, `nginx -t` trước, sao lưu, reload không gián đoạn; chạy lại không sao).
3. Quản trị user-management (`/admin/servers`) đăng ký: `name` = `ai-thongtinduan`, `display_name` = "Thông tin dự
   án MEP", `resource_uri` = `https://trithuc.hawee.com.vn/thongtinduan/mcp`; gán quyền `read` cho người dùng.
4. Người dùng: Claude → Settings → Connectors → Add custom connector → URL ở mục 1 → đăng nhập tài khoản HAWEE.

## 7. Kiểm thử
- Unit (`tests/mcp_server`): metadata + 401 trỏ đúng user-management; từ chối token sai khóa / sai iss / sai aud /
  hết hạn / thiếu email / HS256; tools/list chỉ đọc; tra cứu seed S5 + list_projects; thiếu quyền → lỗi tool;
  `TrustedProxyAuth` không chặn MCP và API cũ vẫn đòi token Tri thức; tắt `enabled` → 404.
- Thủ công 21/11/2026 (khóa thử, dữ liệu S5 thật, client `mcp` chính thức): trực tiếp app và qua gateway nginx
  (`/thongtinduan/mcp`) – search "Bảng 4.3 gió tươi" OK, 6.8s; script server 1 thử trên nginx giả lập.
