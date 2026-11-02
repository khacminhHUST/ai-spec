# MEP Project Assistant – Backend Core

Chatbot hỏi đáp thông tin dự án xây dựng cơ điện (MEP) cho kỹ sư nhà thầu cơ điện.
Kiến trúc RAG: Ingestion (offline) + Query Pipeline (online, LangGraph) + Agent Harness (yêu cầu linh hoạt: xuất file, nhiều bước, MCP).

## Quy tắc làm việc
- Trả lời / báo cáo NGẮN GỌN, đúng trọng tâm, không mở rộng vấn đề ngoài yêu cầu.
- Không tự thêm tính năng phụ trợ (memory, eval, cache, ACL…) khi chưa được yêu cầu.
- Mỗi module làm độc lập theo spec riêng trong `docs/`. Hợp đồng dữ liệu giữa các module là schema trong spec — không tự đổi schema; cần đổi thì ghi chú và hỏi lại.
- Mọi bước xử lý phải có kiểm tra (test) dựa trên bộ tài liệu mẫu.

## Nguồn tài liệu
Chia theo dự án → công trình → phân hệ; tài liệu cập nhật theo giai đoạn/revision.
4 luồng tài liệu (`stream`):
| stream | Mô tả | Ví dụ |
|---|---|---|
| `LEGAL_STANDARD` | TCVN/QCVN – nhà nước ban hành | TCVN 5687:2024 |
| `OPERATOR_STANDARD` | Tiêu chuẩn đơn vị vận hành | Westin Design Standards |
| `DESIGN_CONSULTANT` | Thuyết minh, Spec, Chỉ dẫn KT của tư vấn | CPG, PTN |
| `CONTRACT` | Hợp đồng & phụ lục – chủ đầu tư ký | (chưa có mẫu) |

Phân hệ (`discipline`): `HVAC`, `ELECTRICAL`, `ELV`, `PLUMBING`, `FIRE`, `LIFT`, `BMS`, `GENERAL`.

Bộ tài liệu mẫu: `S5_Bo-tai-lieu-mau_GD-thiet-ke_20261020/` (dự án Quảng Trường Biển 88ha = Bãi Đất Đỏ, tòa S5, Phú Quốc):
- `1_Thuyet minh ky thuat/` – Thuyết minh TK (VN, 70 tr, nhiều bảng)
- `2_Yeu cau ky thuat (Spec)/` – Spec Điện lắp đặt (song ngữ EN trái | VN phải, 50 tr)
- `3_Chi dan ky thuat/` – Chỉ dẫn KT PCCC (VN, 102 tr, 13 chương)
- `4_Tieu chuan Marriott/` – Westin Design Standards (EN, 833 tr)

## Kiến trúc tổng
1. **Ingestion** – spec: `docs/ingestion.md` ✅ đã chốt
2. **Query Pipeline** – spec: `docs/query-pipeline.md` ✅ đã chốt (sơ đồ: `docs/query-pipeline.png`)
3. **Agent Harness** – spec: `docs/agent-harness.md` ✅ đã làm (Gate: lookup → Query Pipeline | agent → `create_agent` + middleware + sandbox Docker + MCP + skills)

Lưu trữ dùng chung:
- PostgreSQL: `documents`, `parent_chunks`, `project_aliases`, `conversations`, checkpoint LangGraph (PostgresSaver)
- Qdrant: `child_chunks_vi`, `child_chunks_en` (dense + BM25 sparse)
- Tham số query: `config/pipeline.yaml`
- Dự án, alias, ưu tiên nguồn, map thư mục: `config/projects.yaml`
- Model: `config/models.yaml` (ingest và query bắt buộc dùng cùng model theo ngôn ngữ)
- Agent: `config/agent.yaml` (model, giới hạn vòng lặp, sandbox), `config/mcp.yaml` (MCP server – hiện rỗng)
- Namespace `MEP_NS` → schema Postgres + tiền tố collection Qdrant. Dữ liệu S5 thật nằm ở `MEP_NS=ingest`.

## Tech stack
Python 3.11+, Docling, Embedding qua API (VN: Qwen3-Embedding-8B, EN: gemini-embedding-2), Reranker Qwen3-Reranker-8B, LLM Gemini (3.5-flash-lite / 3.8-flash), Qdrant, PostgreSQL, underthesea, lingua,
LLM qua interface chung (không khóa provider),
LangGraph (query pipeline + assistant graph).
Agent (chốt 2026-10-29): langgraph 1.2.18 · langchain 1.4.7 · langchain-core 1.7.0 · deepagents 0.7.32 (chỉ dùng
PatchToolCallsMiddleware – `create_deep_agent` ép tool filesystem + subagent `task` nên không dùng) ·
langchain-mcp-adapters 0.3.2 · docker SDK 7.2.0 / Engine 29.0.1. Agent: gemini-3.8-flash (`thinking_level: low`); Gate: gemini-3.5-flash-lite.

## Cấu trúc thư mục
```
app/
  main.py             # FastAPI create_app (uvicorn app.main:app)
  api/                # deps.py + routes/ (chat SSE, confirm, conversations, files upload/tải)
  core/               # config.py (Settings, config/*.yaml, .env)
  db/                 # client.py (Postgres/Qdrant), migrate.py, migrations/*.sql
  models/             # document.py (Document/Parent/Child – hợp đồng dữ liệu), query.py, chat.py, agent.py (API)
  prompts/            # prompt LLM (.md) theo service: query/, ingestion/, agent/ – load()/render()
  providers/          # llm, embedding, reranker (+ mock)
  repositories/       # postgres.py (đọc), qdrant.py (tìm kiếm), index_store.py (ghi khi ingest)
  services/
    ingestion/        # parse, structure, bilingual, metadata, chunks, pipeline
    query/            # LangGraph: graph + router, resolver, retrieval, context, grader, generator
    agent/            # Agent Harness: graph (gate → lookup | agent → save), gate/, loop/ (agent, limits,
                      #   confirm, context), tools/ (search_docs, sandbox_tools, skill_tool, registry), sandbox/, skills/, mcp/
  utils/              # keys (ID/section_key), sparse (BM25), tokens
  cli/                # python -m app.cli.migrate | python -m app.cli.ingest
sandbox/              # image/Dockerfile + helpers/ (mep_helpers) + templates/ + build.ps1 → image mep-sandbox:latest
skills/               # xlsx-export, docx-export, file-check (<name>/SKILL.md)
frontend/             # React + Vite (npm run dev → :5173, proxy sang :8000)
config/  tests/  docs/
```
(Giai đoạn 1–3 bên dưới viết theo tên cũ: `shared/` → `app/{core,db,models,providers,utils}`, `ingestion/` → `app/services/ingestion`, `query/` → `app/services/query` + `app/api`.)

## Trạng thái (2026-11-02): TẠM DỪNG PHÁT TRIỂN
Đã xong cả 3 module; tất cả đã gộp vào `main`. Bản này đủ cho demo hoặc pilot nội bộ có giám sát, **chưa đủ cho production**.

Kết quả kiểm thử lần cuối:
- 276 unit + integration pass.
- Agent E2E: A1–A8 8/8; Gate 22/22; xuất file p50 33.7s.
- Câu hỏi vàng 100%: TTFT p50 5.73s, qua Gate 6.6s.
- Số lần chạy E2E còn ít; UI chưa được test bằng click tay.

Việc còn mở:
- Spec `docs/agent-harness.md` mục 10 A1 ghi "gym 3.8 / 0.9", lệch với PDF Thuyết minh tr.11 (Health club 10 / 0.3;
  Lounges 3.8 / 0.9). Test đang theo PDF; spec chờ người dùng xác nhận để sửa.
- Ingest nên gắn caption vào chunk bảng (thay cho cách vá ở agent: nạp kèm `table_A_B_*` theo mục). Việc này cần đổi ingest nên phải được duyệt trước.
- Luồng `CONTRACT` chưa có dữ liệu mẫu; chưa nối MCP server thật.

Còn thiếu so với production (ưu tiên trên xuống):
1. Xác thực: `user_id` hiện do client tự gửi, nên ai cũng mạo danh được.
2. Lưu file output bền vững (object storage): hiện file chỉ nằm trong sandbox, mất sau 30 phút idle hoặc khi restart app.
3. Rate limit, quota, trần số sandbox, theo dõi chi phí token.
4. Sandbox chạy `runsc` (gVisor) thay `runc`.
5. Trace/log theo request, metrics, cảnh báo.
6. Scale ngang: hiện chỉ chạy được 1 worker vì SandboxManager nằm trong tiến trình. Cần tách thành dịch vụ riêng + hàng đợi.
7. Retry / model dự phòng cho LLM.
8. CI/CD + image cho app; eval chạy tự động.
9. Phòng thủ prompt injection từ tài liệu hoặc file tải lên.

## Chạy
```
docker compose up -d                          # Postgres + Qdrant
python -m app.cli.migrate --ns ingest
python -m app.cli.ingest "<thư mục S5>" --ns ingest   # đã ingest sẵn ở ns ingest
.\sandbox\build.ps1                           # image mep-sandbox:latest (1 lần)
$env:MEP_NS="ingest"; uvicorn app.main:app --port 8000
cd frontend; npm run dev                      # http://localhost:5173
```
Sandbox: mỗi hội thoại có 1 container + 1 volume (label `mep.sandbox`, không có mạng, rootfs chỉ đọc, uid 1000). App tự dọn khi
khởi động và khi idle quá `idle_ttl_s`. Theo dõi: `docker events --filter label=mep.sandbox`.

## Lịch sử triển khai (đã xong)
Giai đoạn 1 `shared-v1` → Giai đoạn 2 `feat/ingestion` + `feat/query` → Giai đoạn 3 ghép + nghiệm thu →
tái cấu trúc `app/` + UI (`refactor/app-structure`) → Agent Harness (`feat/agent`, 6 bước theo `docs/agent-harness.md` mục 9).
Phần dưới giữ lại để tham khảo quy trình.

### Giai đoạn 1 – `shared/` (1 phiên, làm trước)
- Schema Document / Parent / Child (theo `docs/ingestion.md`), bảng Postgres, collection Qdrant, migration.
- Config loader (`config/*.yaml`, `.env`), DB clients, interface LLM / embedding / reranker + bản **mock**.
- `docker-compose.yml` (Postgres + Qdrant), khung pytest + marker.
- Khởi tạo git repo, commit, tag `shared-v1`.
- Xong khi: `docker compose up` chạy, migration tạo đủ bảng/collection, unit test `shared/` pass.

### Giai đoạn 2 – 2 phiên song song (mỗi phiên 1 git worktree/branch)
| Phiên | Branch | Phạm vi được sửa | Dữ liệu test |
|---|---|---|---|
| A – Ingestion | `feat/ingestion` | `ingestion/`, `tests/ingestion/`, `config/` regex | Bộ tài liệu S5 thật |
| B – Query | `feat/query` | `query/`, `tests/query/` | `tests/fixtures/query_seed/` (dữ liệu giả lập) |

Quy tắc chung:
- **Không sửa `shared/`** và schema. Cần đổi → dừng, ghi rõ đề xuất thay đổi, hỏi người dùng.
- Không sửa file ngoài phạm vi của phiên mình.
- Mỗi phiên tự chạy test của mình, báo kết quả ngắn gọn.

Dữ liệu giả lập cho phiên B (`tests/fixtures/query_seed/`):
- Viết tay theo đúng schema, nội dung chép từ tài liệu S5: Spec Điện mục 5.7 (VN + EN, gồm bảng bán kính uốn cáp, tiết diện tối thiểu, màu pha) và 5.23 (bảo hành), Thuyết minh Bảng 4.3 (gió tươi) và mục gió thải toilet, Chỉ dẫn PCCC Chương 1 mục 2.
- Script nạp seed vào Postgres/Qdrant test (embedding thật nếu có key, mock nếu không).
- Đủ để chạy unit test và thử các câu #1–#10, #12, #13 của bộ câu hỏi vàng ở mức sơ bộ.

### Giai đoạn 3 – Ghép (1 phiên)
- Merge `feat/ingestion` + `feat/query` vào `main`.
- Ingest toàn bộ bộ S5 thật → chạy E2E bộ câu hỏi vàng (`docs/query-pipeline.md` mục 6) → báo cáo theo tiêu chí nghiệm thu.

### Prompt giao việc mẫu
- Giai đoạn 1: "Đọc CLAUDE.md, docs/, config/. Làm Giai đoạn 1 (shared). Kiểm tra key và model ID trước. Báo kết quả ngắn gọn."
- Phiên A: "Đọc CLAUDE.md, docs/ingestion.md. Tạo worktree branch feat/ingestion. Làm phiên A Giai đoạn 2. Chạy unit + integration test trên bộ S5."
- Phiên B: "Đọc CLAUDE.md, docs/query-pipeline.md. Tạo worktree branch feat/query. Làm phiên B Giai đoạn 2: tạo query_seed rồi code query pipeline. Chạy unit test."
- Giai đoạn 3: "Đọc CLAUDE.md. Làm Giai đoạn 3, báo cáo theo tiêu chí nghiệm thu."
- Agent Harness: "Đọc CLAUDE.md, docs/agent-harness.md, docs/query-pipeline.md. Tạo worktree branch feat/agent. Làm theo mục 9 từng bước, mỗi bước chạy test và báo kết quả ngắn gọn. Kiểm tra phiên bản deepagents/langgraph và model ID trước."

## Test
- pytest, marker: `unit` (không gọi mạng, mock LLM/embedding/reranker), `integration`, `e2e` (gọi API thật).
- Hạ tầng test: `docker-compose.yml` (Postgres + Qdrant).
- Dữ liệu test: thư mục tài liệu mẫu S5 (chỉ đọc). Fixture câu hỏi vàng: `tests/fixtures/s5_golden.yaml` (lập từ bảng trong `docs/query-pipeline.md`).
- API key qua `.env` (không commit). Thiếu key → test `integration/e2e` skip, không fail.
- Báo cáo kết quả test ngắn gọn: pass/fail + lý do fail.
- Agent: `tests/agent/` (unit, integration/sandbox cần Docker + image), kịch bản E2E `tests/fixtures/agent_scenarios.yaml`
  → `tests/agent/e2e/test_ag_scenarios.py` (chạy với `MEP_NS=ingest`).
