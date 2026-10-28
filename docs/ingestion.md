# Spec: Ingestion Pipeline

Mục tiêu: biến tài liệu PDF/DOCX thành Parent chunks (Postgres) và Child chunks (Qdrant),
cắt theo cấu trúc tài liệu, giữ nguyên 100% text gốc, sẵn sàng cho Query Pipeline.

Nguyên tắc:
- Không dùng LLM để viết lại nội dung. LLM chỉ được trả về VỊ TRÍ CẮT.
- Mọi text trong chunk phải là text gốc (có bước kiểm tra toàn vẹn).
- Chạy lại cùng file → không tạo bản ghi trùng (idempotent theo `file_hash`).

---

## Luồng xử lý (6 bước)

### 1. Metadata tài liệu
- Nguồn: đường dẫn thư mục + tên file + trang bìa.
- Trường: dự án, công trình, stream, discipline(s), tiêu đề, đơn vị ban hành, revision, ngày phát hành, giai đoạn, ngôn ngữ.
- Chuẩn hóa tên dự án qua bảng `project_aliases` (VD: "Quảng Trường Biển 88ha" = "Bãi Đất Đỏ").
- Versioning: cùng `doc_code` (dự án + stream + discipline + loại tài liệu) có revision mới → bản cũ `is_latest = false` ở cả Postgres và payload Qdrant.
- Không suy ra được trường nào → để null + ghi warning, không đoán.

### 2. Parse
- Docling: PDF → danh sách khối (heading, paragraph, list item, table), mỗi khối có `page` và `bbox`.
- Bật OCR cho trang scan.
- Bỏ header/footer lặp lại của trang.

### 3. Tách song ngữ
- Nhận diện tài liệu song ngữ: tỷ lệ khối VN/EN trên trang.
- Tách theo cột: tâm bbox so với giữa trang (vị trí cột cấu hình theo tài liệu; mẫu Spec CPG: EN trái, VN phải).
- Kiểm tra lại bằng nhận diện ngôn ngữ (lingua). Khối lệch cột/ngôn ngữ → warning.
- Heading song ngữ: tách theo cột, CẢ HAI bên giữ số mục.
- Bảng có cột EN + cột VN: tách cột theo ngôn ngữ header.
- Khối full-width không có chữ (hình, bảng số) → đưa vào cả hai.
- Kết quả: 2 file Markdown `vi.md`, `en.md` cấu trúc giống nhau. Tài liệu đơn ngữ → 1 file.
- Kiểm tra: danh sách số mục của VN và EN phải trùng nhau; lệch → báo cáo diff.

### 4. Chunk cha (Parent)
- Cắt theo heading bằng regex riêng từng loại tài liệu (`config/`):
  | Loại | Mẫu heading |
  |---|---|
  | Spec CPG | `5.7`, `5.7.1` |
  | Chỉ dẫn PTN | `CHƯƠNG N`, `N.`, `N.N.` |
  | Thuyết minh | `I.`, `1.`, `5.1`, `a.` |
  | Westin | `GR1`, `4C`, `4C.6`, `9.7` |
  | Hợp đồng | `Điều X`, `Khoản`, `Phụ lục` |
  | TCVN | `1`, `1.1`, `Phụ lục A` |
- Mỗi mục = 1 parent. Mỗi bảng = 1 parent riêng (gắn với mục chứa nó).
- `section_key` chuẩn hóa: `sec_5_7_1`, `table_4_3`, `sec_15c_6`. Giống nhau giữa VN và EN.
- Mục vượt `max_parent_tokens` → cắt tiếp ở heading cấp con.
- `referenced_ids`: regex bắt mã tham chiếu trong nội dung: TCVN/QCVN, ASTM/IEC/BS/NFPA/ASHRAE, "Bảng x.y", "Mục x.y", "Section 15C"… Lưu dạng chuẩn hóa.

### 5. Chunk con (Child)
**Text**
- Mục ≤ `max_child_tokens` → 1 child.
- Mục ngắn/vừa → đơn vị = đoạn (Docling).
- Mục dài (> `long_section_tokens`) hoặc có đoạn > `max_child_tokens` → đơn vị = câu, đánh số liên tục cả mục, đánh dấu ¶ ở ranh giới đoạn.
- Tách câu: che (mask) trước các mẫu dễ nhầm — số thập phân (`1.5`, `3,5`), số mục (`5.7.1`), mã tiêu chuẩn (`TCVN 5687:2024`), viết tắt (`v.v.`, `No.`, `Rev.01`) → tách khi gặp `. ; ? !` + khoảng trắng + chữ hoa, hoặc xuống dòng + bullet/số thứ tự → trả lại mẫu gốc. Nền: underthesea.
- LLM nhẹ (`chunking_llm` trong `config/models.yaml`, temperature 0) nhận danh sách đơn vị đã đánh số, trả về các nhóm liền kề `{start, end, topic}`.
- Kiểm tra output LLM: nhóm liên tục, phủ hết, không chồng, mỗi nhóm ≤ `max_child_tokens`. Sai → fallback gộp tham lam theo token.
- Code ghép text gốc theo nhóm. Cache kết quả LLM theo hash nội dung.

**Bảng**
- Ghép bảng bị ngắt trang (cùng số cột/header lặp, nằm đầu trang sau).
- Child = 1 dòng hoặc nhóm dòng; ưu tiên cắt theo dòng phân nhóm (VD "General", "Hotel Room"), nếu không thì gộp tới `max_child_tokens`.
- Mỗi child luôn có: tên bảng + header + tên nhóm (nếu có).
- Bảng nhỏ → 1 child = cả bảng.

**Chung**
- Mỗi child có breadcrumb ở đầu: `[Dự án | Stream | Discipline | Mục | Topic]`.
- Kiểm tra toàn vẹn: nối nội dung các child (bỏ breadcrumb/header lặp) = nội dung parent.

### 6. Embed & lưu
- Model embedding theo ngôn ngữ, đọc từ `config/models.yaml` (hiện tại: VN = Qwen3-Embedding-8B qua DeepInfra, EN = gemini-embedding-2). Không hard-code model.
- Embed child (text có breadcrumb) bằng model của đúng ngôn ngữ của child.
- Sparse: BM25 (Qdrant) cho cả hai ngôn ngữ; tokenizer giữ nguyên mã kỹ thuật.
- Qdrant: 1 collection / ngôn ngữ (`child_chunks_vi`, `child_chunks_en`), named vectors `dense`, `sparse`; payload index cho các trường lọc.
- Gọi API theo batch, có retry; cache embedding theo hash text + tên model.
- Postgres: `documents`, `parent_chunks`, `project_aliases`.

---

## Schema đầu ra

**Document** (Postgres `documents`)
`doc_id, doc_code, project_id, building, stream, disciplines[], title, issuer, revision, issue_date, stage, languages[], is_bilingual, is_latest, source_path, file_hash, ingested_at, warnings[]`

**Parent chunk** (Postgres `parent_chunks`)
`parent_id, doc_id, section_key, parent_section_key, lang, type (SECTION|TABLE), number, title, breadcrumb, content_md, page_start, page_end, referenced_ids[], token_count`
- `parent_id` = `doc_id + lang + section_key` (unique).

**Child chunk** (Qdrant `child_chunks_vi` / `child_chunks_en`)
- Nội dung: `child_id, parent_id, section_key, lang, type (TEXT|TABLE_ROWS), text, raw_text, topic, page_start, page_end`
- Payload lọc: `project_id, building, stream, disciplines[], doc_id, revision, is_latest, lang`
- Vectors: `dense`, `sparse`

## Tham số cấu hình (mặc định)
`max_child_tokens = 400`, `min_child_tokens = 80`, `long_section_tokens = 1200`, `max_parent_tokens = 6000` (đếm token bằng tokenizer của model embedding tương ứng). Regex heading/reference theo loại tài liệu trong `config/`.

## Artifact debug (mỗi tài liệu)
`parsed.json`, `vi.md`, `en.md`, `parents.jsonl`, `children.jsonl`, `report.json` (số mục, số parent/child, warnings, kết quả kiểm tra).

## Interface
- CLI: ingest 1 file hoặc 1 thư mục, tham số dự án/công trình (nếu không suy ra được từ path).
- Chế độ `--dry-run`: chỉ sinh artifact, không ghi DB.

---

## Tiêu chí nghiệm thu (trên bộ tài liệu mẫu)
1. **Spec Điện CPG** (song ngữ): VN và EN đều có đủ mục 5.1 → 5.24, cùng `section_key`. Bảng bán kính uốn cáp ở mục 5.7 là 1 parent TABLE; có child chứa dòng "< 10mm² … 3 lần ĐK" ở bản VN và "3 times outside diameter" ở bản EN.
2. **Thuyết minh**: Bảng 4.3 (gió tươi) parse đúng cột; child nhóm "Hotel Room" chứa dòng Guest; `referenced_ids` của mục liên quan có `TCVN 5687:2024`.
3. **Chỉ dẫn PCCC PTN**: nhận đủ 13 chương; Chương 2 có các mục 1 → 11.
4. **Westin**: nhận đủ các phần GR1 → 15C; tồn tại `sec_9_7`, `sec_10_7`, `sec_15c_6`.
5. Kiểm tra toàn vẹn text: 100% parent pass.
6. Không child rỗng; không child > `max_child_tokens` (trừ 1 dòng bảng đơn lẻ quá dài – ghi warning).
7. Không chunk nào cắt giữa số thập phân / mã tiêu chuẩn.
8. Ingest lại cùng file → số bản ghi không đổi.
9. Ingest revision mới → bản cũ `is_latest = false` ở Postgres và Qdrant.
10. Mỗi child có `page_start/page_end` đúng trang trong PDF gốc (kiểm tra ngẫu nhiên 20 child).

## Test

Dữ liệu: bộ tài liệu mẫu `S5_Bo-tai-lieu-mau_GD-thiet-ke_20261020/` (đọc-only, không sửa/di chuyển file gốc).
Để test nhanh, unit test dùng trang trích (VD Spec Điện trang 19–20, Thuyết minh trang 10–11); integration chạy toàn bộ 4 file.

### Unit (không gọi mạng; mock LLM & embedding)
1. **Tách câu**: không cắt ở `1.5mm²`, `3,5m`, `5.7.1`, `TCVN 5687:2024`, `QCVN 06:2022/BXD`, `v.v.`, `Rev.01`; cắt đúng ở `. ` + chữ hoa và bullet.
2. **Heading regex** từng loại tài liệu: nhận đúng `5.7`, `5.7.1`, `CHƯƠNG 2`, `4C.6`, `I.`, `Điều 5`.
3. **Tách song ngữ**: trang Spec Điện → khối trái = EN, phải = VN; heading song ngữ giữ số mục cả 2 bên; bảng 4 cột tách đúng.
4. **Bảng**: ghép bảng ngắt trang; child theo dòng/nhóm dòng luôn có tên bảng + header; cắt theo dòng phân nhóm.
5. **Output LLM chunking**: nhóm hợp lệ được chấp nhận; nhóm chồng/thiếu/vượt token → fallback gộp tham lam.
6. **Toàn vẹn**: nối child = parent; cố ý làm mất 1 câu → test phải fail.
7. **referenced_ids**: bắt và chuẩn hóa `TCVN 5687:2024`, `Bảng 4.3`, `ASHRAE 62.1`, `Section 15C`.
8. **Metadata**: suy đúng stream/discipline/revision từ đường dẫn + tên file mẫu; trường không suy được = null + warning.

### Integration (Postgres + Qdrant thật qua docker compose; embedding API thật)
- Ingest toàn bộ bộ S5 → chạy đủ **Tiêu chí nghiệm thu** ở trên.
- Idempotent: ingest lần 2 → số bản ghi không đổi.
- Versioning: ingest bản sao đổi revision → bản cũ `is_latest = false` ở Postgres & Qdrant.
- `--dry-run` sinh đủ artifact, không ghi DB.

## Ngoài phạm vi
Query, rerank, generation, memory, eval, phân quyền — thuộc module khác.
