# MEP Project Assistant

Chatbot hỏi đáp tài liệu dự án cơ điện (RAG). Cấu trúc thư mục: xem `CLAUDE.md`.

## Chạy thử

```bash
docker compose up -d                                   # Postgres :5442 + Qdrant :6333
uv sync                                                # môi trường Python (.venv)
cp .env.example .env                                   # điền GOOGLE_API_KEY …

python -m app.cli.migrate --ns ingest                  # tạo bảng/collection (lần đầu)
python -m app.cli.ingest S5_Bo-tai-lieu-mau_GD-thiet-ke_20261020 --ns ingest

# Backend (namespace có dữ liệu ingest)
MEP_NS=ingest uvicorn app.main:app --port 8000         # PowerShell: $env:MEP_NS="ingest"; uvicorn ...

# Frontend
cd frontend && npm install && npm run dev              # http://localhost:5173
```

## Test

```bash
pytest -m unit                  # không gọi mạng
pytest -m "unit or integration" # cần docker compose
pytest -m e2e -s                # bộ câu hỏi vàng trên namespace ingest → artifacts/e2e_report.json
```
