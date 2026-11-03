# Spec: MCP Connectors (kết nối MCP server qua URL https)

Mục tiêu: agent dùng được tool của MCP server từ xa, khai bằng **URL https** qua giao diện, theo 2 kiểu:
- **OAuth 2.1**: server được bảo vệ bởi Authorization Server, ví dụ Server A `user-management` của HAWEE. Làm theo cách Claude/ChatGPT xử lý "custom connector": tự khám phá, tự đăng ký client, người dùng đăng nhập và đồng ý trên trình duyệt, token lưu riêng cho từng user.
- **Trực tiếp**: MCP server thường, không qua A. Không xác thực, hoặc header/token tĩnh do người khai nhập.

Tài liệu chuẩn OAuth của hệ thống HAWEE, **đọc trước khi code**:
`C:\Users\Administrator\Downloads\Khung dự án\HUONG_DAN_TICH_HOP_MCP_SERVER.md`. Các mục cần đọc:
- 0: khái niệm.
- 3: hành vi thật của A.
- 4.5: ba tầng 401 / 403 / ToolError.
- 5.5–5.7: phía MCP server.
- 14: các lỗi thực tế đã gặp.

Code thật của A (`D:\ai-planner\app\api\oauth\`, `app/services/oauth/`) và MCP server mẫu (`D:\git_ai_hopdong`, `D:\New folder\mcp-reference\`) **chỉ đọc**, không sửa.

Nguyên tắc:
- **Chưa có đăng nhập và phân quyền.** `user_id` vẫn do client gửi như các API hiện có. Sau này SSO sẽ thay. Mọi user như nhau, ai cũng thêm/sửa được connector.
- **Rủi ro đã biết** (ghi vào README của trang): biết `user_id` của người khác thì dùng được kết nối OAuth của người đó. Chấp nhận trong giai đoạn này.
- **Không sửa** `app/services/query/`, `app/services/ingestion/`, schema bảng cũ. Cần đổi thì dừng lại, ghi đề xuất và hỏi.
- Mọi tham số đọc từ `config/mcp.yaml`. Secret lấy từ `.env`.
- Server khai trong `config/mcp.yaml`, kể cả stdio và token tĩnh, **vẫn chạy như cũ**, hiển thị trên giao diện ở chế độ chỉ đọc.

---

## 1. Khái niệm

| Khái niệm | Ý nghĩa |
|---|---|
| **Connector** | Một MCP server đã khai: URL, tên, transport, kiểu xác thực. Dùng chung cho mọi user. |
| **Kết nối (connection)** | Chỉ có ở connector `oauth`: token của **một user** cho **một connector**. Mỗi user tự bấm "Kết nối". |
| **Tên connector** | Dạng slug `[a-z0-9_]{2,32}`, duy nhất, dùng làm tiền tố tên tool: `<tên>_<tool>`. |

Kiểu xác thực (`auth_type`):
- `oauth`: theo mục 2.
- `none`: gọi thẳng, không header.
- `headers`: header tĩnh, ví dụ `Authorization: Bearer xxx` hoặc `X-API-Key`. Giá trị mã hóa khi lưu. Cho phép `${ENV_VAR}`, thay bằng giá trị trong `.env` lúc gọi.

Transport: `streamable_http` (mặc định) hoặc `sse`. Chỉ chấp nhận `https://`, trừ `http://localhost` / `127.0.0.1` khi dev.

---

## 2. Luồng OAuth 2.1 (phía client = app MEP)

### 2.1 Thêm connector (khám phá tự động)
1. Người dùng nhập URL, tên, và chọn "Tự động". Backend gửi `POST <url>` với `initialize`, không kèm token.
   - Trả 2xx: server không cần xác thực, gợi ý chọn `none`.
   - Trả 401: đọc `WWW-Authenticate`, lấy `resource_metadata="…"` nếu có.
2. Lấy **Protected Resource Metadata** (RFC 9728) theo thứ tự:
   1. URL trong `resource_metadata`.
   2. `/.well-known/oauth-protected-resource/<path>` (biến thể có đuôi, ví dụ `/mcp`).
   3. `/.well-known/oauth-protected-resource`.

   Dùng `mcp.client.auth.utils` (mcp 1.30.0 đã có `build_protected_resource_metadata_discovery_urls`, `extract_resource_metadata_from_www_auth`, …). Kết quả cần lấy: `resource` (URI chính tắc), `authorization_servers[0]`, `scopes_supported` (nếu có).
3. Lấy **Authorization Server Metadata** (RFC 8414): `/.well-known/oauth-authorization-server`, không được thì thử `openid-configuration`. **Kiểm tra `issuer` khớp tuyệt đối** với giá trị trong PRM (mục 3.1 của hướng dẫn: không thừa `/`, không có cổng nội bộ).
4. Không có PRM: tương thích MCP spec 2025-03-26, coi gốc của URL MCP là Authorization Server.
5. Lưu connector: `resource`, `issuer`, metadata của AS (JSON), `scopes`.

### 2.2 Đăng ký client (DCR, RFC 7591)
- **Mỗi issuer chỉ đăng ký 1 lần** rồi lưu `oauth_clients` (A giới hạn 10 lần/giờ/IP).
- Body gửi đi: `{"client_name": "MEP Project Assistant", "redirect_uris": ["<PUBLIC_BASE_URL>/mcp/oauth/callback"], "grant_types": ["authorization_code","refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}`.
- AS không có `registration_endpoint`: form "Nâng cao" cho nhập `client_id` (và `client_secret` nếu có). Có secret thì dùng `client_secret_post` hoặc `client_secret_basic`, theo `token_endpoint_auth_methods_supported`.
- Đổi `redirect_uri` (ví dụ đổi domain) thì đăng ký lại.

### 2.3 Kết nối (từng user)
1. `POST /mcp/connectors/{id}/connect?user_id=` → backend:
   - Sinh `state` (ngẫu nhiên, dùng 1 lần, sống 10 phút) và PKCE (`code_verifier` 43–128 ký tự, S256).
   - Lưu `oauth_pending` gồm state, user_id, connector_id, verifier (mã hóa), hạn.
   - Trả `authorize_url` = `authorization_endpoint?response_type=code&client_id&redirect_uri&state&code_challenge&code_challenge_method=S256&resource=<resource>` (+ `scope` nếu PRM khai).
2. Frontend mở popup tới `authorize_url`. User đăng nhập và bấm đồng ý ở AS. A hỏi đồng ý riêng theo (user, client, server). Cookie của A là SameSite=Strict nên user thường phải đăng nhập lại; đây là hành vi bình thường.
3. `GET /mcp/oauth/callback?code&state` (hoặc `?error=…&state`):
   - Kiểm tra `state` tồn tại, chưa dùng, chưa hết hạn. Xóa ngay sau khi đọc.
   - `POST token_endpoint` với `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `code_verifier`, `resource`.
   - Lưu `mcp_user_tokens` (access, refresh mã hóa; `expires_at`; scope).
   - **Kiểm tra kết nối**: `initialize` + `tools/list` bằng token mới.
     - OK: `status=connected`.
     - 403 `insufficient_scope`: `status=forbidden`, báo "Tài khoản chưa được cấp quyền trên server này – liên hệ quản trị Server A".
   - Trả về trang HTML nhỏ: `window.opener.postMessage({type:"mcp-oauth", connector_id, status}, origin)` rồi tự đóng. Không có `opener` thì redirect về `/?view=connectors`.
   - Lỗi `invalid_target` từ A: `resource` chưa được admin A đăng ký. Báo rõ cho user.
4. Ngắt kết nối: xóa token phía MEP. Thông báo cho user: access token cũ còn hiệu lực tối đa 60 phút (mục 3.7 của hướng dẫn); muốn thu hồi hẳn thì thao tác ở A.

### 2.4 Dùng token và refresh
- Token còn dưới `refresh_skew_s` (60s) thì refresh trước khi gọi.
- **Refresh phải có khóa theo (user_id, connector_id)**: `SELECT … FOR UPDATE` trong transaction. Sau khi có khóa, đọc lại token; nếu luồng khác đã refresh xong thì dùng luôn. Lý do: A **xoay vòng refresh token và phát hiện dùng lại**. Hai luồng cùng dùng một refresh token thì A thu hồi toàn bộ phiên.
- Lưu ngay refresh token mới mà A trả về (có xoay vòng).
- Gọi tool nhận 401: refresh **1 lần** rồi gọi lại. Vẫn 401, hoặc refresh trả `invalid_grant`: `status=needs_reauth`, agent nhận lỗi có cấu trúc "Kết nối <tên> đã hết hạn – người dùng cần bấm Kết nối lại".
- **Không bao giờ log token**, kể cả access token: payload JWT chứa email và quyền.

---

## 3. Agent dùng tool MCP

> Cập nhật 2026-11-03: tool của connector **không** nạp sẵn vào mọi lượt nữa mà nạp 2 tầng – danh mục nguồn + mở nguồn
> (`docs/agent-harness.md` mục 3.2). Agent dựng 1 lần; tool của nguồn đã mở được gắn động mỗi lần gọi LLM.

- Mỗi connector đang bật (và mỗi server trong `config/mcp.yaml`) là 1 **nguồn** trong danh mục, kèm trạng thái theo user: `none` / `headers` → sẵn sàng; `oauth` → đã kết nối / chưa kết nối / hết hạn / không có quyền. Danh mục chỉ đọc DB.
- Agent mở nguồn (`open_source_tools`) → nạp tool của connector cho user đó (connector `oauth` phải `status=connected`).
- Cột `description` (migration `004_source_description.sql`): mô tả nguồn cho danh mục. Để trống → tự điền ở lần thử kết nối thành công đầu tiên (giới thiệu server tự trả → LLM tóm tắt danh sách tool → tên tool). `POST /connectors/{id}/describe?user_id=` trả gợi ý (không lưu).
- Danh sách tool (`tools/list`) cache theo (connector, user) trong `tool_cache_ttl_s` (600s). Kết nối lại hoặc sửa connector thì xóa cache. Connector lỗi khi mở nguồn → lỗi có cấu trúc cho agent, lượt chat vẫn chạy.
- Gọi tool: mỗi lần gọi mở phiên MCP qua `langchain-mcp-adapters` (`streamable_http` / `sse`).
  - Connector `oauth`: truyền `auth=httpx.Auth` tự viết, lấy token của user đang chat theo mục 2.4.
  - Connector `headers`: truyền `headers`.
  - `user_id` lấy từ `AgentContext` (code), **không** lấy từ tham số LLM.
- **Phân loại đọc/ghi**, ưu tiên từ trên xuống:
  1. Ghi đè do người dùng đặt trên giao diện (`mcp_tool_overrides`).
  2. Annotation MCP: `readOnlyHint=true` là `read`; `destructiveHint=true` hoặc không có annotation là `write`.
  3. Mặc định `write`.

  Tool `write` đi qua `ConfirmMiddleware` hiện có (`app/services/agent/loop/confirm.py`).
- `ToolError` / `isError` từ server: trả cho agent dạng lỗi tool có cấu trúc (`ok:false`). Kết nối vẫn giữ.
- **Chạy tiếp sau xác nhận** (`/chat/confirm`): tool của nguồn đã mở được nạp lại tại thời điểm đó. Tool cần chạy không còn (ví dụ vừa ngắt kết nối) thì trả lỗi có cấu trúc, không làm sập.

---

## 4. Lưu trữ (migration `app/db/migrations/002_mcp.sql`)

Mã hóa: Fernet, khóa lấy từ `MEP_SECRET_KEY` trong `.env`. Thiếu khóa thì API OAuth/headers trả lỗi rõ ràng; connector `none` vẫn chạy.

| Bảng | Cột chính |
|---|---|
| `mcp_connectors` | `connector_id` PK, `name` UNIQUE, `display_name`, `url`, `transport`, `auth_type` (`oauth`/`none`/`headers`), `headers_enc`, `resource`, `issuer`, `as_metadata` JSONB, `scopes`, `enabled`, `created_by`, `created_at`, `updated_at` |
| `oauth_clients` | `issuer` + `redirect_uri` PK, `client_id`, `client_secret_enc`, `auth_method`, `registered_at` |
| `oauth_pending` | `state` PK, `user_id`, `connector_id`, `code_verifier_enc`, `expires_at` |
| `mcp_user_tokens` | (`user_id`, `connector_id`) PK, `access_token_enc`, `refresh_token_enc`, `expires_at`, `scope`, `status` (`connected`/`forbidden`/`needs_reauth`), `updated_at` |
| `mcp_tool_overrides` | (`connector_id`, `tool_name`) PK, `access` (`read`/`write`/`disabled`) |

Cấu hình bổ sung trong `config/mcp.yaml`: `public_base_url` (để dựng `redirect_uri`, mặc định `http://localhost:5173`), `client_name`, `tool_cache_ttl_s`, `refresh_skew_s`, `http_timeout_s`, `allow_http_localhost`.

---

## 5. API (`app/api/routes/mcp.py`)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/mcp/connectors?user_id=` | Danh sách connector (DB + yaml) kèm trạng thái kết nối của user |
| POST | `/mcp/connectors/discover` `{url}` | Chạy khám phá mục 2.1, trả gợi ý `auth_type`, resource, issuer (chưa lưu) |
| POST | `/mcp/connectors` | Tạo connector |
| PATCH / DELETE | `/mcp/connectors/{id}` | Sửa / xóa (xóa kèm token và override) |
| POST | `/mcp/connectors/{id}/connect?user_id=` | Trả `authorize_url` |
| GET | `/mcp/oauth/callback` | Callback OAuth (mục 2.3) |
| POST | `/mcp/connectors/{id}/disconnect?user_id=` | Xóa token của user |
| GET | `/mcp/connectors/{id}/tools?user_id=` | Danh sách tool kèm `access` hiệu lực |
| PUT | `/mcp/connectors/{id}/tools/{tool}` `{access}` | Ghi đè đọc/ghi/tắt |
| POST | `/mcp/connectors/{id}/test?user_id=` | Gọi thử `initialize` + `tools/list` |

Thêm proxy `/mcp` vào `frontend/vite.config.ts`. Callback đi qua origin của frontend nên `redirect_uri` dùng `public_base_url`.

---

## 6. Giao diện (`frontend/src/components/connectors/**`, `frontend/src/mcpApi.ts`)

- Tab **"Kết nối"** trong `App.tsx`, chỉ thêm vài dòng.
- Danh sách connector: tên, URL, kiểu xác thực, bật/tắt, trạng thái của tôi (Chưa kết nối · Đã kết nối · Cần kết nối lại · Chưa được cấp quyền), các nút Kết nối / Ngắt / Thử.
- Form thêm/sửa:
  - Nhập URL rồi bấm "Kiểm tra" (gọi `discover`) để tự chọn kiểu.
  - Có tùy chọn ép kiểu: OAuth / Không xác thực / Header tĩnh (danh sách cặp tên–giá trị).
  - Mục "Nâng cao" cho client_id/secret thủ công.
- Kết nối: mở popup tới `authorize_url`, nghe `postMessage`, cập nhật trạng thái.
- Danh sách tool của connector: tên, mô tả, đọc/ghi/tắt (đổi được).
- Connector từ yaml hiển thị ở chế độ chỉ đọc.

---

## 7. Phạm vi file

Được sửa/thêm:
- `app/services/agent/**`, gồm `mcp/`, `runtime.py`, `graph.py`, `loop/agent.py`, `tools/registry.py`.
- `app/services/agent/mcp/oauth/**` (mới: discovery, pkce, client_registry, token_store, crypto).
- `app/api/routes/mcp.py`.
- `app/db/migrations/002_mcp.sql`.
- `config/mcp.yaml`.
- `frontend/src/components/connectors/**`, `frontend/src/mcpApi.ts`.
- `tests/agent/unit/mcp/**`, `tests/agent/integration/mcp/**`, `tests/fixtures/oauth_fake/**`.

Sửa tối thiểu (vài dòng, để merge dễ):
- `app/main.py` (include router).
- `frontend/src/App.tsx` (thêm tab).
- `frontend/vite.config.ts` (proxy).
- `pyproject.toml` (dependency nếu cần).

Không sửa: `app/services/query/`, `app/services/ingestion/`, `app/services/knowledge/`, `001_init.sql`, `CLAUDE.md`, `docs/` (trừ file này nếu cần ghi chú lệch spec).

---

## 8. Test

### Unit (không gọi mạng, httpx `MockTransport`)
1. PKCE: verifier 43–128 ký tự, challenge S256 đúng (đối chiếu `D:\ai-planner\app\services\oauth\pkce.py`).
2. Khám phá:
   - 401 + `resource_metadata` → PRM → AS metadata.
   - Có PRM đuôi `/mcp` và bản gốc.
   - Không có PRM thì dùng gốc của URL.
   - Issuer lệch (thừa `/`, sai scheme) thì từ chối.
3. DCR chỉ gọi 1 lần cho mỗi (issuer, redirect_uri). Không có registration endpoint thì yêu cầu client_id thủ công.
4. `state`: dùng 2 lần, hết hạn, sai user thì từ chối.
5. Refresh có khóa: 2 luồng đồng thời chỉ gọi token endpoint **1 lần**. Refresh token mới được lưu.
6. 401 khi gọi tool: refresh 1 lần rồi gọi lại. `invalid_grant` thì `needs_reauth`.
7. Phân loại đọc/ghi: override > annotation > mặc định `write`. Tool `write` sinh `confirm_required`.
8. Danh mục nguồn + tool theo nguồn: connector `oauth` chưa kết nối không mở được; connector tắt không có trong danh mục; user A không thấy token của user B.
9. Token không xuất hiện trong log (dùng `caplog`).
10. Mã hóa: giá trị lưu DB khác plaintext, giải mã đúng.

### Integration (`tests/fixtures/oauth_fake/` + Postgres thật)
- **A giả** (FastAPI, dựng theo `D:\ai-planner\app\api\oauth\router.py`):
  - Discovery, DCR công khai.
  - `/oauth/authorize` tự đồng ý, kiểm tra `resource`, trả `invalid_target`.
  - `/oauth/token`: S256, JWT RS256 với cặp khóa tự sinh và payload đúng mục 3.2 của hướng dẫn, xoay vòng refresh, dùng lại refresh token cũ thì thu hồi toàn bộ.
  - JWKS.
- **MCP server giả có OAuth**: SDK chính thức (`mcp.server.fastmcp`) + `TokenVerifier` theo mục 5.5 của hướng dẫn: 401 kèm `WWW-Authenticate`, 403 `insufficient_scope`, `ToolError`. PRM có cả 2 route. Có 1 tool đọc (`readOnlyHint`) và 1 tool ghi.
- **MCP server không xác thực**, và **MCP server dùng header tĩnh**.
- Kịch bản:
  1. Thêm connector (discover) → kết nối (giả lập trình duyệt theo redirect) → agent gọi tool đọc.
  2. Gọi tool ghi → `confirm_required` → đồng ý / từ chối.
  3. Access token hết hạn → tự refresh.
  4. A thu hồi refresh token → `needs_reauth`.
  5. User không có quyền nào → `forbidden`.
  6. Connector `none` / `headers` dùng được ngay, không cần kết nối.
  7. Hai user khác nhau có token riêng.

### Hồi quy
- Toàn bộ test hiện có pass (276 unit + integration).
- Agent E2E A1–A8 và câu hỏi vàng qua agent không giảm nếu có key (`MEP_NS=ingest`).
- A7 (tool MCP write) vẫn đạt.

### E2E với A thật (tùy chọn, nếu A chạy được ở local)
`D:\ai-planner` chạy ở `localhost:8000`, trùng cổng mặc định của app MEP, nên chạy MEP ở cổng khác. Nếu không dựng được A thật thì ghi rõ "chưa kiểm chứng với A thật". Khi lỗi, chẩn đoán bằng 3 lệnh curl ở mục 14.1 của hướng dẫn.

## 9. Tiêu chí nghiệm thu
- Kết nối được MCP server OAuth giả **chỉ bằng URL**: khám phá, DCR, đồng ý, token, tool chạy, không cấu hình tay.
- Kết nối được MCP server thường (không xác thực / header tĩnh).
- Token mã hóa khi lưu, không xuất hiện trong log, đúng user.
- Hai refresh đồng thời không làm A thu hồi phiên.
- Toàn bộ test cũ pass.

---

## 10. Ghi chú triển khai (lệch / bổ sung so với spec, branch `feat/mcp-connectors`)
- **So khớp issuer**: so chuỗi tuyệt đối theo `mcp.client.auth.utils.issuers_match`, chỉ coi `https://host` ≡ `https://host/`
  (RFC 3986 §6.2.3). Lý do: SDK `mcp` chính thức ghi PRM gốc dạng `authorization_servers: ["https://host/"]` (pydantic
  `AnyHttpUrl`), trong khi A khai `issuer` không có `/`. Thừa `/` ở issuer có path, sai scheme, thêm cổng → vẫn từ chối.
- **Gắn trình duyệt với callback**: `/connect` đặt cookie `mep_oauth_uid` (HttpOnly, SameSite=Lax, path `/mcp/oauth`,
  sống `state_ttl_s`); callback có cookie mà khác `user_id` của state → từ chối. Không có cookie → vẫn chấp nhận theo state.
- **403 `insufficient_scope` khi gọi tool** (không chỉ lúc callback) cũng đặt `status=forbidden`; bấm "Thử"/"Kết nối lại"
  để kiểm tra lại.
- **Callback**: đổi token xong mà `initialize`/`tools/list` lỗi mạng tạm thời → vẫn `connected`, kèm cảnh báo.
- **Resume sau xác nhận** khi tool không còn trong bộ tool của lượt: `ConfirmMiddleware` trả lỗi có cấu trúc
  (`unavailable: true`), không thực thi.
- Thêm `state_ttl_s` (600) vào `config/mcp.yaml`. `MEP_SECRET_KEY` là chuỗi bất kỳ → SHA-256 → khóa Fernet.
- `GET /mcp/connectors/{id}/tools` của connector OAuth khi user chưa kết nối → 409.
- Rủi ro "biết `user_id` của người khác thì dùng được kết nối OAuth" ghi ở đầu tab "Kết nối".
