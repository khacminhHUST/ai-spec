# Spec: Quản lý kho tri thức qua giao diện

Mục tiêu: người dùng tự **upload, kiểm tra, phát hành và quản lý phiên bản** tài liệu dự án qua giao diện web, thay cho chạy lệnh `python -m app.cli.ingest`. Dữ liệu tổ chức theo:

```
Dự án → Công trình → Luồng (stream) → Phân hệ (discipline) → Bộ tài liệu (doc_code) → Các revision theo thời gian
```

> **Cập nhật 2026-11-18 – kho gọn theo dự án** (người dùng duyệt; ghi đè các mục bên dưới khi mâu thuẫn):
> - Giao diện quản lý **theo dự án**: `Dự án → Luồng → Bộ tài liệu → revision`. Công trình và phân hệ không còn là tầng
>   của cây (vẫn lưu trong dữ liệu).
> - **Tạo dự án chỉ cần tên.** Mã dự án tự sinh từ tên (bỏ dấu, viết hoa, trùng → `-2`), có thể nhập tay ở phần nâng
>   cao. Thêm `start_date`, `end_date` (dự kiến) – tùy chọn, sửa sau được. Công trình không bắt buộc; ưu tiên nguồn
>   không khai → mặc định như `config/projects.yaml`.
> - **Upload chỉ cần File + Dự án + Luồng.** Công trình, loại tài liệu, phân hệ tùy chọn. Thiếu loại tài liệu / phân
>   hệ → worker **tự nhận diện** (bước `classify`, `app/services/knowledge/classify.py`: tên file + 3 trang đầu → LLM
>   `chunking_llm`; lỗi → dự phòng: loại theo luồng, phân hệ `GENERAL`), ghi vào job, rồi mới chạy ingestion với
>   metadata ghi đè (ingestion không đổi). Kết quả hiện ở trang duyệt kèm nguồn (AI / dự phòng); sửa được →
>   `PATCH /knowledge/jobs/{id}` → xử lý lại (DRAFT/FAILED → QUEUED; parse PDF có cache).
> - **Mã bộ khi upload gọn**: `<project>_<stream>` (+ `_2`, `_3`…). Gửi đủ công trình + phân hệ + loại tài liệu → mã như
>   cũ. "Revision mới của bộ X" là tùy chọn (mặc định tài liệu mới); khi chọn, công trình / phân hệ / loại tài liệu còn
>   trống lấy theo bộ X.
> - **Sửa / chuyển dự án ngay trong kho**: `PATCH /knowledge/series/{doc_code}` đổi dự án / luồng / phân hệ cho cả bộ
>   (Postgres + payload Qdrant). Chuyển sang dự án không có công trình cũ → công trình để trống.
> - Migration `005_knowledge_simple.sql`: `projects.start_date/end_date`; `ingest_jobs.building/doc_type` bỏ NOT NULL.
>   Bảng `documents` và payload Qdrant giữ nguyên.
> - `GET /knowledge/tree` trả `[{project_id, name, start_date, end_date, n_series, n_documents, active_jobs,
>   streams: [{stream, series: [...]}]}]`.

> **Cập nhật 2026-11-19 – upload tự động hoàn toàn** (người dùng duyệt; ghi đè nguyên tắc "duyệt rồi mới phát hành"):
> - `config/knowledge.yaml › worker.auto_publish: true`: xử lý xong phát hành luôn (người upload = `published_by`),
>   không dừng ở `DRAFT`. Nhận diện sai → sửa sau trong trang dự án. Tắt (`false`) → quay lại bước duyệt.
> - `worker.embedded: true`: server (`app/main.py` lifespan) tự chạy worker trong 1 thread nền; không cần
>   `python -m app.cli.worker` (lệnh này vẫn dùng được, SKIP LOCKED nên chạy song song không nhận trùng job).
> - UI: bỏ tab "Tải lên"; tải lên ở khung bên cạnh trong trang dự án, có chọn phân hệ (tùy chọn).

Nguyên tắc:
- **Chưa có đăng nhập và phân quyền.** `user_id` vẫn do client gửi. Mọi user như nhau, ai cũng upload, phát hành, xóa được. `user_id` chỉ được ghi lại để truy vết (`uploaded_by`, `published_by`). Sau này SSO sẽ thay.
- **Không đổi** schema `documents`, `parent_chunks`, payload Qdrant (hợp đồng dữ liệu trong `docs/ingestion.md`).
- **Không sửa Query Pipeline** (`app/services/query/`). Ngoại lệ duy nhất được phép: làm mới cache hồ sơ dự án (mục 6). Nếu buộc phải sửa thì sửa tối thiểu và ghi rõ trong báo cáo.
- Ingestion (`app/services/ingestion/`) **chỉ được thêm**: tham số ghi đè metadata và `doc_code`. Hành vi CLI hiện tại giữ nguyên.
- Tài liệu upload phải **duyệt rồi mới phát hành**. Bản nháp **không** vào DB tìm kiếm.
- "Thời gian" ở mức **quản lý phiên bản**. Tìm kiếm chỉ dùng bản hiệu lực (`is_latest`), như hiện tại.

---

## 1. Dự án và công trình (chuyển từ yaml sang DB)

- Bảng `projects` và `project_buildings`. Lần migrate đầu **seed từ `config/projects.yaml`**. `folder_map` vẫn ở yaml, chỉ dùng cho CLI.
- Sửa alias của dự án hoặc công trình thì **sinh lại `project_aliases`** (dùng `normalize_alias` sẵn có, giống `app/db/migrate.py`).
- Các hàm trong `app/repositories/postgres.py` chuyển sang đọc DB (`config_project_info`, `config_user_projects`, `project_info`, `user_projects`), giữ nguyên định dạng trả về.
- Quyền xem dự án do `config/knowledge.yaml` › `access.mode` quyết định:
  - `open` (mặc định cho app): mọi user thấy mọi dự án.
  - `config`: dùng `test_users` trong yaml như cũ, để các test hiện có (ví dụ "dự án không có quyền bị loại") vẫn chạy.
- Giao diện "Dự án": tạo/sửa dự án (mã, tên, alias, alias chủ đầu tư, thứ tự ưu tiên nguồn, ngôn ngữ ưu tiên) và công trình (mã, alias).

---

## 2. Luồng upload → nháp → duyệt → phát hành

### 2.1 Upload
- Chỉ nhận PDF. Parse hiện chỉ hỗ trợ PDF; nhận file khác thì báo rõ. Giới hạn kích thước theo `config/knowledge.yaml`.
- Form metadata:

  | Trường | Bắt buộc | Ghi chú |
  |---|---|---|
  | Dự án, công trình | ✔ | Chọn từ DB |
  | Luồng | ✔ | `LEGAL_STANDARD` / `OPERATOR_STANDARD` / `DESIGN_CONSULTANT` / `CONTRACT` |
  | Phân hệ | ✔ (≥1) | `HVAC` … `GENERAL` |
  | Loại tài liệu | ✔ | Một trong 6 profile của `config/headings.yaml`: `SPEC`, `TECH_INSTRUCTION`, `DESIGN_REPORT`, `BRAND_STANDARD`, `CONTRACT`, `LEGAL_STANDARD`. Kèm mô tả ngắn từng loại. |
  | Revision, ngày ban hành, giai đoạn, tiêu đề, đơn vị ban hành | ✘ | Gợi ý trước từ tên file (`revision_from`, `issue_date_from` trong `metadata.py`). Để trống thì pipeline tự suy từ bìa như hiện tại. |
  | **Bộ tài liệu** | ✔ | "Tài liệu mới" **hoặc** "Revision mới của: [chọn bộ đã có]" |

- **Quy tắc `doc_code`** (`doc_code` xác định bản nào thay bản nào):
  - "Revision mới của bộ X": dùng `doc_code` của X.
  - "Tài liệu mới": tính như hiện tại (`project_building_stream_discipline_doctype`). Nếu trùng một bộ đã có thì **thêm hậu tố** `_2`, `_3`… để không thay nhầm.
- Trùng `file_hash` với tài liệu đã có hoặc job đang chạy: báo trùng, không tạo job.
- File gốc lưu bằng interface `FileStore`. Bản local ghi vào `data/uploads/<project_id>/<file_hash>.pdf`, đường gốc lấy từ config. Interface để sẵn cho object storage sau này. `documents.source_path` trỏ tới file này.
- Tạo `ingest_jobs` với `status=QUEUED`.

### 2.2 Xử lý nền (worker)
- Tiến trình riêng: `python -m app.cli.worker [--ns]`. Lấy job bằng `SELECT … FOR UPDATE SKIP LOCKED`, xử lý **mỗi lần 1 job** (Docling và LLM nặng).
- `QUEUED` → `PROCESSING`: chạy bước 1–5 của ingestion (`process()` trong `app/services/ingestion/pipeline.py`) với metadata ghi đè từ form. Ghi artifact (`write_artifacts`: `parsed.json`, `vi.md`, `en.md`, `parents.jsonl`, `children.jsonl`, `report.json`) vào thư mục của job. **Không ghi Postgres/Qdrant tìm kiếm.**
- Cập nhật `step` theo tiến độ (`parse`, `bilingual`, `chunk_vi`, `chunk_en`) để giao diện hiển thị.
- Kết thúc: `DRAFT`, hoặc `FAILED` kèm `error` (thông báo ngắn, traceback ghi log).
- Worker khởi động: job `PROCESSING` của lần chạy trước chuyển `FAILED` ("bị gián đoạn"), cho phép **Chạy lại**.

### 2.3 Duyệt (trang báo cáo của job)
- Metadata đã nhận (gồm `doc_code`, bộ tài liệu, bản sẽ bị thay).
- Thống kê từ `report.json`: số mục, số bảng, số parent/child theo VN và EN, kiểm tra toàn vẹn, kiểm tra trang, cảnh báo.
- **Cây mục** (section_key, số mục, tiêu đề, trang) và **xem trước** nội dung một mục (parent + các child).
- Nút: **Phát hành**, **Hủy** (`CANCELLED`, xóa artifact, giữ file gốc), **Chạy lại** (khi `FAILED`).

### 2.4 Phát hành
- `DRAFT` → `PUBLISHING` (worker xử lý): đọc lại kết quả nháp, embed, gọi `save_document` có sẵn (`app/repositories/index_store.py`). Hàm này đã xử lý idempotent theo `file_hash` và versioning `is_latest` theo `doc_code` ở cả Postgres lẫn Qdrant.
- Ghi `doc_id`, `published_by`, `published_at`. Chuyển `PUBLISHED`, hoặc `FAILED` kèm lỗi.
- Không parse lại khi phát hành: dùng kết quả nháp, nạp lại từ artifact hoặc cache Docling/LLM sẵn có.

Trạng thái job: `QUEUED → PROCESSING → DRAFT → PUBLISHING → PUBLISHED`. Nhánh phụ: `FAILED`, `CANCELLED`.

---

## 3. Quản lý phiên bản (thời gian)

- **Cây kho tri thức**: dự án → công trình → luồng → phân hệ → bộ tài liệu, hiển thị số revision và bản hiệu lực.
- **Lịch sử revision của bộ**: revision, ngày ban hành, giai đoạn, ngày phát hành vào hệ thống, người phát hành, bản hiệu lực. Lọc theo khoảng ngày ban hành và giai đoạn.
- **Đặt bản hiệu lực**: chọn 1 revision. Bản đó `is_latest=true`, các bản khác cùng `doc_code` `false`, ở Postgres và payload Qdrant (dùng `set_latest_payload` có sẵn). Dùng khi upload nhầm bản mới hoặc cần quay lại bản cũ.
- **Gỡ khỏi tìm kiếm**: mọi revision của bộ `is_latest=false`. Bộ vẫn hiện trong kho với nhãn "Đã gỡ". **Khôi phục** bằng cách đặt lại bản hiệu lực.
- **Xóa revision**: xóa `documents` (cascade `parent_chunks`) và Qdrant (`delete_points` có sẵn). Nếu bản bị xóa đang hiệu lực thì bản mới nhất còn lại thành hiệu lực (theo `_version_key` có sẵn). File gốc giữ lại.
- **Tải file gốc** của từng revision.

---

## 4. Lưu trữ (migration `app/db/migrations/003_knowledge.sql`)

| Bảng | Cột chính |
|---|---|
| `projects` | `project_id` PK, `name`, `aliases` TEXT[], `owner_aliases` TEXT[], `source_priority` TEXT[], `governing_language`, `created_at`, `updated_at` |
| `project_buildings` | (`project_id`, `building`) PK, `aliases` TEXT[] |
| `ingest_jobs` | `job_id` PK, `file_name`, `file_hash`, `storage_key`, `size`, `project_id`, `building`, `stream`, `disciplines` TEXT[], `doc_type`, `revision`, `issue_date`, `stage`, `title`, `issuer`, `series_doc_code`, `status`, `step`, `report` JSONB, `warnings` TEXT[], `error`, `artifact_dir`, `doc_id`, `uploaded_by`, `created_at`, `published_by`, `published_at`, `updated_at` |

Seed `projects` / `project_buildings` từ yaml đặt trong `app/db/migrate.py`, cạnh phần seed `project_aliases` hiện có. **Bảng `documents` giữ nguyên.** Các thông tin thêm của một revision lấy bằng join `ingest_jobs.doc_id`; tài liệu nạp bằng CLI cũ thì để trống.

Cấu hình `config/knowledge.yaml`: `access.mode`, `uploads_dir`, `max_upload_mb`, `worker.poll_interval_s`, `artifacts_dir`.

---

## 5. API

`app/api/routes/projects.py`:

| Method | Path | Mô tả |
|---|---|---|
| GET / POST | `/projects` | Danh sách / tạo dự án |
| PATCH | `/projects/{id}` | Sửa dự án (sinh lại alias) |
| POST / PATCH / DELETE | `/projects/{id}/buildings[/{b}]` | Quản lý công trình |

`app/api/routes/knowledge.py`:

| Method | Path | Mô tả |
|---|---|---|
| GET | `/knowledge/tree?project_id=` | Cây dự án → … → bộ tài liệu |
| GET | `/knowledge/series/{doc_code}` | Lịch sử revision (lọc `from`, `to`, `stage`) |
| POST | `/knowledge/series/{doc_code}/latest` `{doc_id}` | Đặt bản hiệu lực |
| POST | `/knowledge/series/{doc_code}/unpublish` | Gỡ khỏi tìm kiếm |
| DELETE | `/knowledge/documents/{doc_id}` | Xóa revision |
| GET | `/knowledge/documents/{doc_id}/file` | Tải file gốc |
| POST | `/knowledge/uploads` (multipart: file + metadata JSON, `user_id`) | Tạo job |
| GET | `/knowledge/jobs?status=` · `/knowledge/jobs/{id}` | Danh sách / chi tiết job (kèm report) |
| GET | `/knowledge/jobs/{id}/sections` · `/knowledge/jobs/{id}/sections/{lang}/{section_key}` | Cây mục / xem trước |
| POST | `/knowledge/jobs/{id}/publish` · `/cancel` · `/retry` | Phát hành / hủy / chạy lại |
| GET | `/knowledge/options` | Danh sách stream, discipline, doc_type (kèm mô tả), stage |

Thêm proxy `/knowledge` và `/projects` vào `frontend/vite.config.ts`.

---

## 6. Cache hồ sơ dự án

Resolver (`app/services/query/resolver.py`) cache hồ sơ dự án trong `profile_cache_ttl_s` (600s). Hồ sơ này gồm danh sách luồng và ngôn ngữ có tài liệu. Sau khi phát hành, gỡ hoặc xóa, phải làm mới cache để tài liệu mới vào tìm kiếm ngay:
- Ưu tiên làm **ngoài** `app/services/query/`. Ví dụ: API gọi hàm xóa cache trên `Deps` sau mỗi thao tác. Với worker là tiến trình riêng: dùng một "phiên bản kho" (ví dụ `max(ingested_at)` cùng số lượng `documents`), và kiểm tra nó trước khi dùng cache.
- Nếu buộc phải sửa resolver hoặc deps của query thì sửa tối thiểu và ghi rõ trong báo cáo.

---

## 7. Giao diện (`frontend/src/components/knowledge/**`, `frontend/src/knowledgeApi.ts`)

Tab **"Kho tri thức"** trong `App.tsx` (chỉ thêm vài dòng), gồm:
1. **Cây kho** (trái) và **lịch sử revision** của bộ đang chọn (phải), có các nút đặt hiệu lực, gỡ, xóa, tải file gốc.
2. **Upload**: form ở mục 2.1, gợi ý revision và ngày từ tên file, chọn bộ tài liệu.
3. **Hàng chờ xử lý**: danh sách job, trạng thái, bước hiện tại (tự làm mới vài giây một lần khi có job đang chạy).
4. **Trang duyệt** của job: thống kê, cảnh báo, cây mục, xem trước, các nút Phát hành / Hủy / Chạy lại.
5. **Dự án**: tạo/sửa dự án và công trình.

Không thêm thư viện router; điều hướng bằng state.

---

## 8. Phạm vi file

Được sửa/thêm:
- `app/services/knowledge/**` (mới: `jobs.py`, `worker.py`, `storage.py`, `versions.py`, `projects.py`, …).
- `app/api/routes/knowledge.py`, `app/api/routes/projects.py`.
- `app/cli/worker.py`.
- `app/db/migrations/003_knowledge.sql`, phần seed trong `app/db/migrate.py`.
- Các hàm dự án trong `app/repositories/postgres.py`.
- `app/services/ingestion/metadata.py`, `pipeline.py`: **chỉ thêm** tham số ghi đè.
- `config/knowledge.yaml`.
- `frontend/src/components/knowledge/**`, `frontend/src/knowledgeApi.ts`.
- `tests/knowledge/**`.

Sửa tối thiểu (vài dòng): `app/main.py` (include router), `frontend/src/App.tsx` (thêm tab), `frontend/vite.config.ts` (proxy), `docker-compose.yml` (nếu thêm service worker, không đổi service cũ).

Không sửa: `app/services/agent/**`, `app/services/agent/mcp/oauth/**`, schema bảng cũ, `CLAUDE.md`. `app/services/query/` chỉ theo ngoại lệ ở mục 6.

---

## 9. Test

### Unit (mock LLM, embedding, FileStore)
1. Quy tắc `doc_code`: revision mới dùng mã của bộ; tài liệu mới trùng mã thì thêm hậu tố.
2. Ghi đè metadata: form ghi đè giá trị suy từ đường dẫn; trường để trống thì vẫn tự suy; CLI cũ không đổi.
3. Chuyển trạng thái job hợp lệ/không hợp lệ (ví dụ không phát hành được job chưa `DRAFT`); trùng `file_hash` bị từ chối.
4. Worker: lấy job `SKIP LOCKED`; job dở dang khi khởi động thì chuyển `FAILED`.
5. Đặt bản hiệu lực, gỡ, xóa bản đang hiệu lực thì bản còn lại mới nhất lên thay.
6. `access.mode` `open` / `config`.
7. Sinh lại `project_aliases` khi sửa alias.

### Integration (Postgres + Qdrant thật, `test_ns`)
- Upload trích đoạn Spec Điện S5 (trang 19–20, như fixture ingestion) → `DRAFT` có report và cây mục có `sec_5_7` → phát hành → có `documents`, `parent_chunks`, điểm Qdrant `is_latest=true`.
- Upload bản sao đổi revision với "Revision mới của bộ" → bản cũ `is_latest=false` ở Postgres và Qdrant.
- Đặt lại bản cũ làm hiệu lực → payload Qdrant đúng.
- Gỡ khỏi tìm kiếm → mọi bản `false`; xóa → không còn bản ghi.
- Sau khi phát hành: Query Pipeline (mock LLM) tìm thấy tài liệu mới mà không cần khởi động lại (kiểm tra làm mới cache).
- Hủy job → không có gì trong DB tìm kiếm.

### E2E (nếu có key, `MEP_NS` riêng, không đụng `ingest`)
- Upload → phát hành Spec Điện → câu hỏi vàng #1 trả đúng "6 lần đường kính ngoài".

### Hồi quy
- Toàn bộ test hiện có pass (276 unit + integration). Lệnh CLI ingest cho kết quả như cũ.

## 10. Tiêu chí nghiệm thu
- Từ giao diện: upload → xem báo cáo → phát hành → hỏi được trong chat, không chạy lệnh tay.
- Bản nháp không bao giờ xuất hiện trong tìm kiếm.
- Quản lý được revision: lịch sử, đặt hiệu lực, gỡ, xóa; Postgres và Qdrant luôn khớp nhau.
- Dự án và công trình quản lý trên giao diện; alias mới dùng được ngay trong chat.
- Không đổi schema cũ; test cũ pass.
