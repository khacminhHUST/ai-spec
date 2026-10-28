# MEP Project Assistant – Backend Core

Chatbot hỏi đáp thông tin dự án xây dựng cơ điện (MEP) cho kỹ sư nhà thầu cơ điện.
Kiến trúc RAG: Ingestion (offline) + Query Pipeline (online, LangGraph).

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

Lưu trữ dùng chung:
- PostgreSQL: `documents`, `parent_chunks`, `project_aliases`, `conversations`, checkpoint LangGraph (PostgresSaver)
- Qdrant: `child_chunks_vi`, `child_chunks_en` (dense + BM25 sparse)
- Tham số query: `config/pipeline.yaml`
- Dự án, alias, ưu tiên nguồn, map thư mục: `config/projects.yaml`
- Model: `config/models.yaml` (ingest và query bắt buộc dùng cùng model theo ngôn ngữ)

## Tech stack
Python 3.11+, Docling, Embedding qua API (VN: Qwen3-Embedding-8B, EN: gemini-embedding-2), Reranker Qwen3-Reranker-8B, LLM Gemini (3.5-flash-lite / 3.8-flash), Qdrant, PostgreSQL, underthesea, lingua,
LLM qua interface chung (không khóa provider),
LangGraph (query pipeline).

## Cấu trúc thư mục đề xuất
```
ingestion/      # parse, metadata, bilingual, parent, child, index
query/          # (sau)
shared/         # schema, config, db clients, LLM interface
config/         # regex heading/reference theo stream, ngưỡng token, alias dự án
tests/          # test theo tài liệu mẫu
docs/           # spec từng module
```

## Thứ tự triển khai (song song)

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

## Test
- pytest, marker: `unit` (không gọi mạng, mock LLM/embedding/reranker), `integration`, `e2e` (gọi API thật).
- Hạ tầng test: `docker-compose.yml` (Postgres + Qdrant).
- Dữ liệu test: thư mục tài liệu mẫu S5 (chỉ đọc). Fixture câu hỏi vàng: `tests/fixtures/s5_golden.yaml` (lập từ bảng trong `docs/query-pipeline.md`).
- API key qua `.env` (không commit). Thiếu key → test `integration/e2e` skip, không fail.
- Báo cáo kết quả test ngắn gọn: pass/fail + lý do fail.
