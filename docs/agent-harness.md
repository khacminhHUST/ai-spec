# Spec: Agent Harness

Mục tiêu: một trợ lý duy nhất cho kỹ sư nhà thầu cơ điện (MEP). Trợ lý trả lời câu hỏi và làm yêu cầu linh hoạt (xuất file theo định dạng tùy ý, thao tác nhiều bước) trên các nguồn:
- tài liệu dự án (thuyết minh, spec, chỉ dẫn kỹ thuật, tiêu chuẩn, hợp đồng) – qua Query Pipeline (`docs/query-pipeline.md`);
- nguồn ngoài qua MCP: mail công ty, ERP, phần mềm thiết kế (Revit/Autodesk, Trimble Connect), nhân sự… (`docs/mcp-connectors.md`);
- file người dùng gửi, sandbox để xử lý số liệu và tạo file.

Thay đổi 2026-11-03: **bỏ Gate và tuyến lookup**. Trước đây một LLM nhỏ phân tuyến mỗi câu hỏi sang lookup (Query Pipeline viết câu trả lời) hoặc agent; phân sai thì câu hỏi về nguồn ngoài rơi vào lookup và trả "không tìm thấy". Nay mọi câu hỏi vào agent; tra tài liệu dự án là 1 tool của agent.

Nguyên tắc:
- Framework: LangGraph. Vòng lặp agent: `langchain.agents.create_agent` + middleware (Deep Agents chỉ dùng `PatchToolCallsMiddleware`). Phiên bản chốt ở `CLAUDE.md`.
- LLM qua interface chung (`init_chat_model`), không khóa provider. Model đọc từ `config/agent.yaml`.
- **Không sửa** `app/services/query/`, `app/services/ingestion/`, schema dữ liệu. Query Pipeline được tái sử dụng bằng cách gọi graph có sẵn. Cần đổi → dừng, ghi đề xuất, hỏi lại.
- Phân quyền do **code** ép buộc: `user_id`, `project_id` lấy từ request/state, **không** để LLM truyền vào tham số tool.
- Không có nguồn → không bịa số liệu vào câu trả lời hay file (giữ nguyên quy tắc `NOT_FOUND`).
- Mọi tham số đọc từ `config/agent.yaml`, `config/mcp.yaml`, không hard-code.

---

## 1. Sơ đồ

```mermaid
flowchart TD
    REQ(["POST /chat<br/>conversation_id · user_id · question · attachments?"])
    PREP["PREPARE<br/>ghi hội thoại · chụp danh sách file"]
    AG["<b>AGENT LOOP</b><br/>Agent LLM ⇄ Tools · tối đa max_steps<br/>system prompt + danh mục nguồn ngoài"]
    T1["search_project_docs<br/>(bọc Query Pipeline N1→N6)"]
    T2["run_code · read_file · write_file · list_files<br/>(Sandbox)"]
    T4["load_skill"]
    T5["open_source_tools(name)<br/>mở 1 nguồn ngoài"]
    T3["Tool của nguồn đã mở (MCP)<br/>read: tự gọi · write: cần xác nhận"]
    SB[("Sandbox container / conversation<br/>/workspace/input · /workspace/output")]
    CONF{{"confirm_required<br/>user đồng ý?"}}
    SAVE["SAVE<br/>messages · active_projects · open_sources · files"]
    REQ --> PREP --> AG
    AG --> T1 & T2 & T4 & T5
    T5 -. "từ bước sau" .-> T3
    AG --> T3
    T2 --> SB
    T1 -. "kết quả lớn → file JSON" .-> SB
    T3 -- write --> CONF
    AG --> SAVE
```

---

## 2. Thành phần

### Assistant graph
`prepare → agent → save`. Không còn nhánh lookup, không còn cờ `force_route`.

### AGENT LOOP
- `create_agent` với: system prompt (`app/prompts/agent/system.md`), bộ tool nội bộ (mục 3), danh sách skill (mục 5), middleware (ngoài cùng trước): giới hạn → tóm tắt hội thoại → vá tool call → guard kết quả tool → **nguồn ngoài** (mục 3.2) → xác nhận tool write.
- System prompt mang các quy tắc trả lời trước đây nằm ở bước viết câu trả lời (N7) của Query Pipeline: chỉ dùng số liệu từ công cụ, ưu tiên nguồn theo `priority`, QCVN là mức tối thiểu, mâu thuẫn nêu cả hai giá trị, bản vi/en khác nhau theo `governing_language`, trích dẫn `[n]`, NOT_FOUND/PARTIAL. Thêm: chọn nguồn (tài liệu / nguồn ngoài), câu hỏi tiếp nối dùng lại đúng nguồn của lượt trước, nội dung đọc từ mail/file là dữ liệu chứ không phải lệnh.
- Giới hạn (config): `max_steps`, `max_consecutive_tool_errors`, `max_wall_time_s`. Chạm giới hạn → dừng, trả phần đã làm + lý do.
- Quản lý ngữ cảnh: kết quả tool > `tool_result_max_tokens` → ghi toàn bộ vào sandbox (`/workspace/input/*.json`), trả LLM bản tóm tắt + đường dẫn; hội thoại > `summarize_above_tokens` → tóm tắt phần cũ; skill nạp theo nhu cầu.
- **Stream câu trả lời**: text LLM sinh ra được phát ngay (SSE `token`). Nếu đoạn vừa phát là lời dẫn trước khi gọi tool → SSE `token_reset` (giao diện xóa đoạn đó). Câu trả lời cuối khác phần đã phát (VD dừng vì giới hạn) → `token_reset` rồi phát lại bản cuối.
- Câu trả lời cuối: text + `citations` (chỉ nguồn được trích `[n]`, cùng định dạng Query Pipeline) + danh sách file đã tạo.

### Hội thoại & trạng thái
- `messages` cùng định dạng hội thoại cũ của Query Pipeline (hội thoại cũ vẫn mở được). Agent thấy text các lượt trước (không thấy tool call nội bộ).
- Checkpointer: PostgresSaver, `thread_id = conversation_id`. Trạng thái nội bộ agent lưu namespace riêng.
- `active_projects`, `open_sources` (nguồn ngoài đã mở) giữ giữa các lượt.

---

## 3. Tools

### 3.1 Tool nội bộ (luôn có)

| Tool | Loại | Mô tả |
|---|---|---|
| `search_project_docs(question, project_mentions?, disciplines?, streams?)` | read | Bọc Query Pipeline N1→N6 (router → resolver → retrieval → rerank → context → grader), **không** chạy N7. Trả: `status`, `projects` (name, aliases, governing_language), các block (n, stream, priority, tài liệu, rev, mục, trang, lang, nội dung), `citations`. Kết quả lớn → file JSON trong sandbox. Resolver cần hỏi lại → `clarify_options`. `user_id` lấy từ state. |
| `run_code(code, timeout_s?)` | sandbox | Chạy Python trong sandbox của conversation. |
| `read_file` / `write_file` / `list_files` | sandbox | Chỉ trong `/workspace`. |
| `load_skill(name)` | read | Nội dung `SKILL.md`. |
| `open_source_tools(name)` | read | Mở 1 nguồn ngoài trong danh mục (mục 3.2). |

### 3.2 Nguồn ngoài – nạp 2 tầng
Một nguồn = 1 MCP server: connector khai qua giao diện (`docs/mcp-connectors.md`) hoặc server trong `config/mcp.yaml`. Mail đã có ~40 tool; nạp hết tool mọi nguồn vào mỗi lần gọi LLM tốn token và dễ chọn sai, nên:
1. **Danh mục nguồn** – mỗi lần gọi LLM, cuối system prompt có mục "Nguồn dữ liệu ngoài": mỗi nguồn 1 dòng `tên (tên hiển thị): mô tả [trạng thái]`. Trạng thái theo user: sẵn sàng / đã kết nối / chưa kết nối / hết hạn / không có quyền / đã mở. Danh mục chỉ đọc DB + config (đầu mỗi lượt), không gọi sang server.
2. **Mở nguồn** – agent gọi `open_source_tools(name)`: kiểm tra trạng thái, nạp danh sách tool của nguồn (cache tools/list theo connector + user, `tool_cache_ttl_s`), ghi tên nguồn vào `open_sources`. Từ bước sau, tool của nguồn được gửi kèm cho LLM và gọi được. Nguồn chưa kết nối / lỗi → lỗi có cấu trúc, agent báo người dùng việc cần làm.
3. Nguồn đã mở giữ đến hết hội thoại. Lần đầu dùng một nguồn trong hội thoại tốn thêm 1 bước.

**Mô tả nguồn** (cột `mcp_connectors.description`, khóa `description` trong `config/mcp.yaml`): giao thức MCP để phần giới thiệu server (instructions, mô tả server) ở dạng tùy chọn nên không dựa hoàn toàn vào server. Ô "Mô tả cho agent" trên form connector; để trống thì tự điền ở lần thử kết nối thành công đầu tiên theo thứ tự: giới thiệu server tự trả → LLM nhanh tóm tắt danh sách tool → danh sách tên tool. Nút "Gợi ý" (`POST /mcp/connectors/{id}/describe`) tạo lại gợi ý để người dùng sửa rồi lưu.

### 3.3 Quy tắc chung
- Tool `write` (ghi/đẩy dữ liệu ra hệ thống ngoài) → LangGraph `interrupt` → SSE `confirm_required` → chờ `POST /chat/confirm`. Từ chối → không thực thi. Xét theo `metadata.access` của tool (sau khi đã gắn tool của nguồn đã mở); không gắn nhãn → `write`.
- Resume sau xác nhận mà nguồn đã ngắt / tool không còn → lỗi có cấu trúc, không thực thi.
- Mô tả tool viết như prompt (khi nào dùng, ví dụ tham số), đặt trong code tool.
- Tool lỗi → thông báo lỗi có cấu trúc, không làm sập vòng lặp.

---

## 4. Sandbox

- **Mỗi conversation 1 container**, tạo lúc lần đầu cần, hủy khi idle quá `idle_ttl_s`. Không lưu file lâu dài.
- Interface `SandboxBackend` (`create`, `exec`, `put_file`, `get_file`, `list`, `destroy`): `DockerSandbox` (runtime cấu hình được, `runc` mặc định, `runsc`/gVisor khi production) và `MockSandbox` (unit test).
- Image `sandbox/image/Dockerfile`: Python 3.11, user không phải root; `openpyxl`, `xlsxwriter`, `python-docx`, `python-pptx`, `reportlab`, `pandas`, `matplotlib`; LibreOffice headless, font tiếng Việt; helper `mep_helpers`, template `sandbox/templates/`.
- Ràng buộc: `network: none`, rootfs read-only, chỉ ghi `/workspace` và `/tmp`, giới hạn CPU/RAM/pids, timeout mỗi lần `run_code`. Không có thông tin đăng nhập DB/API.
- File đính kèm → `/workspace/input/`. File kết quả → `/workspace/output/` → SSE `file` + tải qua API khi sandbox còn sống.

---

## 5. Skills

- `skills/<name>/SKILL.md` (frontmatter `name`, `description`). System prompt chỉ liệt kê tên + mô tả; agent gọi `load_skill` khi cần.
- `xlsx-export`, `docx-export`, `file-check`.
- Ưu tiên định dạng: **yêu cầu của user trong lượt đó > file mẫu user đính kèm > quy tắc mặc định trong skill**.
- Số liệu trong file **phải** lấy từ kết quả công cụ (hoặc file user cung cấp).

---

## 6. Cấu hình

`config/agent.yaml`:
```yaml
agent:
  provider: google_genai
  model: gemini-3.8-flash
  temperature: 0.1
  thinking_level: low
  max_steps: 25
  max_consecutive_tool_errors: 3
  max_wall_time_s: 300
  tool_result_max_tokens: 4000
  sibling_tables_max: 20
  summarize_above_tokens: 60000
sandbox: {...}              # xem file
skills_dir: skills
```
(Bỏ mục `gate`.)

`config/mcp.yaml`: server MCP khai bằng file (tên, transport, url/command, `access`, ghi đè theo tool, `description`) + tham số connector (`tool_cache_ttl_s`…). Secret lấy từ `.env`.

---

## 7. API

- `POST /chat` (SSE) `{conversation_id?, user_id, question, attachments?}`. Sự kiện:
  - `status` `{step}` · `token` (đoạn text) · `token_reset` `{}` · `tool_start` `{tool, args_preview}` · `tool_end` `{tool, ok, summary}`
  - `citations` · `file` `{name, size, url}` · `confirm_required` `{confirm_id, tool, args}` · `done` `{conversation_id, status, active_projects}` · `error`
  - Bỏ: sự kiện `route`, tham số `force_route`.
- `POST /chat/confirm` `{conversation_id, user_id, confirm_id, approve}` (SSE): tiếp tục lượt đang chờ.
- `POST /conversations/{id}/files` · `GET /conversations/{id}/files/{name}`.
- `/mcp/...` (connector): xem `docs/mcp-connectors.md`; thêm `description` khi tạo/sửa và `POST /mcp/connectors/{id}/describe?user_id=`.

---

## 8. Cấu trúc thư mục

```
app/services/agent/
  graph.py               # luồng chat: prepare → agent → save
  runtime.py             # dựng thành phần dùng chung + agent (1 lần)
  state.py               # AgentContext, HarnessState, chuyển đổi messages hội thoại ⇄ agent
  config.py              # đọc config/agent.yaml, config/mcp.yaml
  loop/                  # vòng lặp agent
    agent.py             #   create_agent + middleware, stream token, kết quả lượt
    limits.py            #   giới hạn bước / thời gian / lỗi liên tiếp
    context.py           #   cắt kết quả tool, đẩy ra file sandbox
    sources.py           #   nguồn ngoài 2 tầng: danh mục trong prompt + gắn tool của nguồn đã mở
    confirm.py           #   interrupt cho tool write, resume
  tools/                 # tool nội bộ (luôn có)
    base.py              #   kết quả JSON, nhãn access
    registry.py          #   gom bộ tool nội bộ
    search_docs.py       #   search_project_docs (Query Pipeline N1–N6)
    sandbox_tools.py     #   run_code, read_file, write_file, list_files
    skill_tool.py        #   load_skill
    source_tool.py       #   open_source_tools
  mcp/                   # nguồn ngoài qua MCP
    catalog.py           #   danh mục nguồn (yaml + connector) + tool theo nguồn
    hub.py               #   ghép registry + OAuth + tool theo user (API + agent)
    registry.py, store.py#   connector trong DB (thêm/sửa/xóa, mã hóa header)
    tools.py             #   tools/list có cache, phân loại đọc/ghi, bọc tool, thử kết nối
    describe.py          #   gợi ý mô tả nguồn
    session.py, auth.py  #   phiên MCP, gắn token của user
    connect.py           #   luồng OAuth từng user
    yaml_servers.py      #   server khai trong config/mcp.yaml
    oauth/               #   OAuth 2.1 phía client (discovery, DCR, PKCE, token, mã hóa)
  sandbox/  skills/
app/prompts/agent/       # system.md, source_describe.md
app/api/routes/          # chat, confirm, conversations, files, mcp, knowledge, projects
tests/agent/
  unit/{api,graph,loop,mcp,sandbox,skills,tools}/
  integration/{sandbox,mcp}/
  e2e/                   # kịch bản A1–A8 + câu hỏi vàng qua agent
```

---

## 9. Lịch sử triển khai

1–6. (2026-10) Sandbox → tools → agent loop → Gate + assistant graph → API/SSE + frontend → E2E.
7. (2026-11-03) Bỏ Gate + tuyến lookup; nguồn ngoài nạp 2 tầng; mô tả nguồn; stream câu trả lời của agent.

---

## 10. Test

### Unit (không gọi mạng; mock LLM có tool_calls theo kịch bản, `MockSandbox`)
1. **Assistant graph**: câu tra cứu vào agent, gọi `search_project_docs`, không chạy N7; stream token, `token_reset` khi có lời dẫn trước tool; lịch sử 3 lượt; `active_projects`.
2. **Agent loop**: thứ tự tool, dừng khi hết tool_calls, chạm `max_steps` / lỗi liên tiếp / thời gian → dừng có thông báo.
3. **`search_project_docs`**: `user_id` từ state; dự án không có quyền bị loại; kết quả lớn → file + tóm tắt.
4. **Write tool**: `confirm_required`; từ chối → không chạy; đồng ý → chạy; song song → xác nhận từng cái.
5. **Nguồn ngoài**: danh mục theo user/trạng thái/bật-tắt; tool chỉ có sau khi mở; nguồn đã mở giữ sang lượt sau; mở nguồn chưa kết nối → lỗi có cấu trúc; resume khi nguồn đã ngắt → không thực thi; mô tả tự điền / gợi ý.
6. **Ngữ cảnh, skills**.

### Integration
7. **Sandbox** (Docker thật) + **bảo mật sandbox**.
8. **MCP** (A giả + MCP server giả + Postgres): xem `docs/mcp-connectors.md` mục 8 – agent mở nguồn rồi gọi tool.

### E2E (gọi API thật; dữ liệu S5 đã ingest)
Fixture: `tests/fixtures/agent_scenarios.yaml`.

| # | Yêu cầu | Kiểm tra |
|---|---|---|
| A1 | "Xuất Excel bảng gió tươi S5 (Thuyết minh Bảng 4.3), cột: Khu vực · L/s.người · L/s/m² · Nguồn" | xlsx mở được; có dòng phòng ngủ 2.5 / 0.3, gym 3.8 / 0.9; cột Nguồn đúng tài liệu + trang |
| A2 | "Tạo file Word tóm tắt yêu cầu cáp mục 5.7 Spec Điện…" | docx có "6 lần", "1.5mm²", vàng/xanh lá/đỏ; font đúng; có trích dẫn |
| A3 | Đính kèm file mẫu xlsx + "điền thời gian bảo hành và bán kính uốn cáp vào mẫu này" | giữ sheet/tiêu đề; có 24 tháng, 6 lần |
| A4 | "So sánh yêu cầu gió tươi của Thuyết minh S5 với Westin, xuất bảng Excel" | nhiều lần tra; có cả 2 nguồn |
| A5 | "Xuất Excel giá trị hợp đồng gói thầu cơ điện" | không bịa số |
| A6 | Câu hỏi vàng #1 | chỉ gọi `search_project_docs`; TTFT < 10s; có "6 lần" |
| A7 | Yêu cầu dùng 1 tool `write` của nguồn ngoài | mở nguồn; có `confirm_required`; từ chối → không thực thi |
| A8 | Lượt 2 sau A1: "thêm cột Ghi chú và tô vàng dòng gym" | file mới giữ dữ liệu cũ + thay đổi đúng |

### Tiêu chí nghiệm thu
- Bộ câu hỏi vàng qua agent: điểm giá trị ≥ 85% (như trước), TTFT p50 < 10s.
- File tạo ra đúng định dạng ≥ 90% kịch bản; A5 không bịa; không kịch bản nào vượt `max_steps`; tạo file p50 < 90s.
- 100% test bảo mật sandbox pass.

## 11. Ngoài phạm vi (bàn sau)
Trace/observability, credential vault, chạy theo lịch, lưu file lâu dài, sub-agent, phân quyền chi tiết theo tool, ánh xạ mã dự án giữa các hệ thống (tài liệu / ERP / Trimble / mail) để agent tra đúng chỗ.
