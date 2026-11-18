-- Kho tri thức gọn (docs/knowledge-admin.md mục 1, 2.1 – bản 2026-11-18):
-- dự án chỉ cần tên (+ ngày bắt đầu / kết thúc dự kiến, tùy chọn); upload chỉ cần dự án + luồng,
-- công trình / loại tài liệu / phân hệ để trống → worker tự nhận diện trước khi xử lý.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS start_date DATE;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS end_date DATE;

ALTER TABLE ingest_jobs ALTER COLUMN building DROP NOT NULL;
ALTER TABLE ingest_jobs ALTER COLUMN doc_type DROP NOT NULL;
