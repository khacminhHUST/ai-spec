# Triển khai MEP Assistant nhúng trong web Tri thức

App MEP chạy trên **server 2**, hiện ra ở `https://trithuc.hawee.com.vn/thongtinduan/` và trong trang
`/#/thong-tin-du-an` của web Tri thức (iframe). Web Tri thức ở **server 1** (`10.34.91.6`) chỉ thêm cấu hình
nginx và 1 iframe; không thêm container, không sửa backend Tri thức.

**Thông số thực tế (18/11/2026)**
| | Giá trị |
|---|---|
| Server 1 (web Tri thức, `nginx_proxy`) | `10.34.91.6` |
| Server 2 (app MEP) | `10.34.91.10` – `srv-ai-tools`, Ubuntu 24.04, 16 GB RAM, **dùng chung** với các app khác (elec-qto, revit-mcp, catalogue, pdf_parse – chiếm cổng 80/443/441/442/555) |
| Thư mục cài trên S2 | `~/mep` (user `ai-tool`) |
| Repo | `https://github.com/khacminhHUST/ai-spec.git`, branch `main` (public) |
| `AUTH_TOKEN_KEY` | `hawee_auth_token` (`frontend/services/userService.ts` của web Tri thức) |
| Cổng gateway | `8090` |

Phía repo này không sửa code lõi: backend chạy bằng điểm khởi chạy riêng `app.trithuc_main:app` (bọc `app.main`,
thêm `/healthz` + middleware `app/api/trusted_proxy.py`); frontend chỉ thêm `src/deploy.ts` (gọi 1 dòng trong
`main.tsx`) và `base` trong `vite.config.ts` – không đặt biến môi trường thì chạy local như cũ.

## 1. Luồng request

```
Trình duyệt ── https://trithuc.hawee.com.vn ──► SERVER 1  nginx_proxy (:443, chứng chỉ hawee.crt)
                                                 ├─ /, /api/, /static/        → web Tri thức (giữ nguyên)
                                                 ├─ /thongtinduan/api/…       → auth_request /_mep_auth
                                                 │     → hawee-backend /api/auth/phien (401 → dừng)
                                                 │     → 200 → http://<IP-S2>:8090/thongtinduan/api/…
                                                 └─ /thongtinduan/…           → http://<IP-S2>:8090/thongtinduan/…
SERVER 2  docker compose "mep" (thư mục này)
  gateway :8090 (chỉ nhận từ IP server 1)
    ├─ /thongtinduan/api/…  → api:8000/…   (bỏ tiền tố)
    └─ /thongtinduan/…      → frontend tĩnh
  api (uvicorn, 1 tiến trình) · worker (ingest nền) · postgres · qdrant · sandbox (api tạo qua docker.sock)
  api/worker ──HTTPS──► Gemini API; api ──► nguồn MCP (nếu bật)
```

**Đăng nhập dùng chung**: iframe cùng tên miền nên frontend MEP đọc được token web Tri thức
(`sessionStorage`/`localStorage`, khóa `AUTH_TOKEN_KEY`) và gửi `Authorization: Bearer`. Server 1 kiểm token
(`auth_request`); server 2 chỉ đọc email trong payload JWT (`MEP_AUTH_MODE=trusted_proxy`,
`app/api/trusted_proxy.py`) và ghi đè `user_id` trình duyệt gửi. An toàn vì gateway chỉ nhận kết nối từ server 1.

## 2. Chuẩn bị server 2
- Ubuntu + Docker Engine + plugin compose. RAM ≥ 16 GB, ổ trống ≥ 50 GB.
- Ra được internet HTTPS (Gemini API, tải mô hình HuggingFace/EasyOCR lần đầu). Qua proxy công ty → bỏ comment
  `HTTPS_PROXY`/`NO_PROXY` trong `.env`.
- Chặn cổng 8090 với mọi IP trừ server 1. **Lưu ý: cổng do Docker mở đi vòng qua `ufw`**, phải chặn ở chuỗi
  `DOCKER-USER`:
  ```bash
  sudo iptables -I DOCKER-USER -p tcp --dport 8090 ! -s 10.34.91.6 -j DROP
  sudo apt install -y iptables-persistent && sudo netfilter-persistent save
  ```
  (gateway cũng tự `allow 10.34.91.6; deny all;` – hai lớp.)

## 3. Cài lần đầu
Đưa code lên server 2: `git clone` branch đã push (cần quyền đọc repo GitHub), hoặc chép thẳng từ máy dev
(`rsync -a --exclude .venv --exclude frontend/node_modules --exclude artifacts --exclude data ./ <user>@<IP-S2>:/opt/mep/`).
Build cần internet (Docker Hub, PyPI, npm); lần chạy đầu tải thêm mô hình HuggingFace/EasyOCR (~3 GB) vào volume `models`.

```bash
cd ~
git clone https://github.com/khacminhHUST/ai-spec.git mep && cd mep
sh sandbox/build.sh                                   # image mep-sandbox:latest (agent xuất file)
cd deploy/trithuc
cp .env.example .env && nano .env                     # AUTH_TOKEN_KEY, POSTGRES_PASSWORD (chỉ chữ+số), GOOGLE_API_KEY, MEP_SECRET_KEY
sh preflight.sh                                       # kiểm Docker, RAM, ổ, internet, cổng, .env – sửa hết LỖI
docker compose build
docker compose up -d postgres qdrant
docker compose run --rm api python -m app.cli.migrate  # schema + collection + checkpoint + seed dự án theo MEP_NS
docker compose up -d                                  # lần đầu api có thể mất vài phút (tải reranker)
docker compose ps                                     # api: healthy; worker, gateway: running
curl -s http://127.0.0.1:8090/healthz                 # {"ok":true}
# Thiếu khóa API thì app TỰ CHUYỂN SANG MOCK (không báo lỗi) – phải thấy google: False:
docker compose exec api python -c "from app.core.config import get_settings as g; s=g(); print({p: s.is_mock(p) for p in ('google','deepinfra','local')})"
```
Gateway trả 403 khi gọi từ server 1 → xem IP thật nginx thấy: `docker compose logs gateway | tail`, sửa `ALLOW_FROM`
rồi `docker compose up -d gateway`.
`MEP_SECRET_KEY`: sinh bằng
`docker compose run --rm api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

### Chuyển dữ liệu từ máy hiện tại (thay cho ingest lại)
```bash
# máy cũ
pg_dump -h localhost -p 5442 -U mep -n ingest -Fc mep > mep_ingest.dump
curl -X POST http://localhost:6333/collections/ingest_child_chunks_vi/snapshots   # lặp cho _en; tải file snapshot về
# server 2
docker compose cp mep_ingest.dump postgres:/tmp/ && \
  docker compose exec postgres pg_restore -U mep -d mep --clean --if-exists /tmp/mep_ingest.dump
# Qdrant: upload snapshot qua API /collections/<tên>/snapshots/upload (qdrant không mở cổng ra máy chủ:
#   docker compose exec / tạm map cổng 6333 khi khôi phục)
# File gốc đã upload: chép data/uploads/ vào volume appdata (docker compose cp … api:/app/data/uploads/)
```
Tên collection theo `MEP_NS` (tiền tố) – kiểm bằng `GET /collections` trước khi chụp.

## 4. Nối server 1
1. Tên khóa token: `hawee_auth_token` (`frontend/services/userService.ts` của web Tri thức) – đã đặt sẵn trong
   `.env.example`. Đổi thì sửa `AUTH_TOKEN_KEY` rồi `docker compose build gateway && docker compose up -d gateway`.
2. Dán khối trong [`server1-nginx.conf`](server1-nginx.conf) vào `/home/ai-trithuc/hawee-deploy/nginx.conf`
   (file thật đang mount vào `nginx_proxy`; deploy của web Tri thức không ghi đè file này), làm theo các bước ở đầu file.
3. Repo `ai_trithuc_frontend`, `pages/ThongTinDuAnThuNghiem.tsx` – thay khu vực placeholder:
   ```tsx
   <iframe src="/thongtinduan/" title="Thông tin dự án"
           style={{ width: "100%", height: "calc(100vh - 120px)", border: 0 }} />
   ```
   (tùy chọn) nghe `window.addEventListener("message", e => e.data?.type === "mep:auth-expired" && …)` để làm mới
   token / chuyển trang đăng nhập khi app MEP báo phiên hết hạn.

## 5. Kiểm tra sau khi nối
| Kiểm tra | Kỳ vọng |
|---|---|
| `curl -sk https://trithuc.hawee.com.vn/thongtinduan/api/projects` (không token) | 401 |
| Mở `/#/thong-tin-du-an` khi đã đăng nhập | App MEP hiện trong iframe, ô User ID = email (sửa tay không có tác dụng: backend lấy theo token) |
| Hỏi 1 câu | Chữ chạy dần (không ra một cục cuối) |
| Upload PDF ~85 MB ở Kho tri thức | Vào hàng chờ, worker xử lý, phát hành được |
| Agent xuất Excel → bấm tải | Tải được (fetch kèm token) |
| Kết nối 1 nguồn MCP OAuth | Cửa sổ đăng nhập đóng lại, nguồn chuyển "connected" |
| Đăng xuất web Tri thức rồi thao tác trong iframe | Báo "phiên đã hết hạn" |

## 6. Vận hành
- Cập nhật: `cd ~/mep && git pull && cd deploy/trithuc && docker compose up -d --build` (server 1 không đổi).
- Log: `docker compose logs -f api worker gateway`. Sandbox: `docker events --filter label=mep.sandbox`.
- Sao lưu: volume `pgdata`, `qdrantdata`, `appdata` (file gốc upload).
- Gỡ khỏi web Tri thức: chép lại `nginx.conf.bak-*` trên server 1 rồi `nginx -s reload`.

## 7. Giới hạn còn lại (xem CLAUDE.md › Còn thiếu so với production)
- `api` mount `docker.sock` = quyền root trên server 2 → server 2 chỉ chạy app này; sandbox nên chuyển `runsc`.
- 1 tiến trình `api` (SandboxManager trong tiến trình); file agent xuất mất khi restart / idle 30 phút.
- Upload multipart: `uploaded_by` lấy từ form (frontend gửi email trong token), middleware không sửa body multipart.
- Reranker chạy CPU nếu server 2 không có GPU (chậm hơn máy dev).
