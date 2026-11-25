"""CLI: upload hàng loạt PDF vào Kho tri thức qua API (POST /knowledge/uploads) – thay cho upload từng file trên web.

    python -m app.cli.bulk_upload <thư mục> [--api URL] [--token TOKEN] [--dry-run]

Cấu trúc thư mục: <thư mục>/<Dự án>/<LUỒNG>/*.pdf (VD 26DA08-DREAM CITY/DESIGN_CONSULTANT/…).
  - Dự án: khớp mã đầu tên thư mục (26DA08, 0125DA003…) với project_id trên hệ thống; không khớp → bỏ qua, báo lại.
  - Luồng: tên thư mục con (LEGAL_STANDARD | OPERATOR_STANDARD | DESIGN_CONSULTANT | CONTRACT).
  - Thư mục bắt đầu bằng "_" (VD _NGOAI_MEP_xem_truoc) bị bỏ qua, trừ khi --include-underscore.
Phân hệ: nếu có 1_DANH_SACH_UPLOAD.csv (danh sách lọc từ ổ dự án) → lấy theo thư mục bộ môn gần nhất của file gốc
  (VD ".../04. FIRE PROTECTION/SECTION 4 - PUMPS.pdf" → FIRE). Không suy ra được → để trống, worker tự nhận diện (LLM).
  Tên file chung chung ("SECTION 1 GENERAL REQUIREMENTS") → tiêu đề kèm tên bộ môn để phân biệt khi trích dẫn.
Kết quả ghi vào <thư mục>/4_KET_QUA_UPLOAD.csv; chạy lại → bỏ qua file đã upload / đã có trong kho.

--api   : gốc API. Local: http://localhost:8000 · Web Tri thức: https://trithuc.hawee.com.vn/thongtinduan/api
--token : token đăng nhập web Tri thức (hoặc biến môi trường MEP_TOKEN) – cần khi chạy qua web Tri thức.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import httpx

STREAMS = ("LEGAL_STANDARD", "OPERATOR_STANDARD", "DESIGN_CONSULTANT", "CONTRACT")
MANIFEST = "1_DANH_SACH_UPLOAD.csv"
RESULT = "4_KET_QUA_UPLOAD.csv"
DONE = ("OK", "ĐÃ CÓ")

# Thư mục bộ môn → phân hệ (xét từ thư mục gần file nhất trở lên, dừng ở thư mục đầu tiên khớp)
DISC_DIRS: list[tuple[str, str]] = [
    ("FIRE", r"fire|\bff\b|pccc|chua chay|bao chay|sprinkler"),
    ("ELV", r"\belv\b|extra low|cctv|security|telecom|telephone|public address|access control|\bpa\b|\bict\b"),
    ("BMS", r"\bbms\b|building management"),
    ("LIFT", r"\blifts?\b|thang may|elevator|escalator|vertical transport"),
    ("HVAC", r"\bacmv\b|\bmvac\b|hvac|\bac\b|air[ -]?con|ventilation|dieu hoa|thong gio"),
    ("PLUMBING", r"plumbing|sanitary|\bp ?& ?s\b|\bps\b|\bpl\b|cap thoat nuoc|\bctn\b|swim|pool|ho boi|water supply"),
    ("ELECTRICAL", r"electrical|\belec\b|\bel\b|\bdien\b|\bee\b"),
]
GENERIC_NAME = re.compile(r"^(section|chapter|chuong|part|muc|phan|vol(ume)?|quyen)?[\s._-]*\d", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("Đ", "D").replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def source_dirs(source: str) -> list[str]:
    """'Z:/…/a.zip → trong zip: x/y/f.pdf' → các thư mục từ gần file nhất trở lên (bỏ tên file)."""
    parts = [p for p in re.split(r"[\\/]| → trong zip: ", source) if p]
    return list(reversed(parts[:-1]))


def discipline_hint(source: str, max_up: int = 3) -> list[str]:
    """Phân hệ theo thư mục bộ môn gần file nhất (tối đa max_up cấp). Không rõ → [] (worker tự nhận diện)."""
    for d in source_dirs(source)[:max_up]:
        nd = norm(re.sub(r"^[\d.\s_-]+", "", d))
        hits = [disc for disc, pat in DISC_DIRS if re.search(pat, nd)]
        if len(hits) == 1:
            return hits
        if hits:          # thư mục ghép nhiều bộ môn (VD "MEP & FP") → không đoán
            return []
    return []


def title_hint(file_name: str, source: str) -> str | None:
    """Tên file chung chung ("SECTION 4 - PUMPS") → "FIRE PROTECTION – SECTION 4 - PUMPS"; còn lại → None."""
    stem = re.sub(r"\s*\(\d+\)$", "", Path(file_name).stem).strip()   # "(2)": hậu tố chống trùng tên khi chép
    if not GENERIC_NAME.match(stem):
        return None
    for d in source_dirs(source)[:3]:
        label = re.sub(r"^[\d.\s_-]+", "", d).strip()
        if label and discipline_hint(f"{d}/x.pdf", 1):
            return f"{label} – {' '.join(stem.split())}"
    return None


def project_code(folder_name: str) -> str:
    """'26DA08-DREAM CITY' → '26DA08' · '2025_0125DA003-The9Stellar' → '0125DA003'."""
    m = re.search(r"\d{2,4}DA[A-Z]?\d+", folder_name, re.I)
    return m.group(0).upper() if m else folder_name.split("-")[0].strip()


def match_project(folder_name: str, projects: list[dict[str, Any]]) -> str | None:
    code = project_code(folder_name)
    for p in projects:
        pid = str(p.get("project_id", ""))
        if pid.upper() == code or code in [str(a).upper() for a in p.get("aliases") or []]:
            return pid
    return None


def load_manifest(root: Path) -> dict[str, str]:
    """Đường dẫn tương đối (chữ thường, '/') → nguồn gốc trên ổ dự án."""
    f = root / MANIFEST
    if not f.exists():
        return {}
    out = {}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("Trạng thái") == "ĐÃ CHÉP" and row.get("File đã chép"):
                out[row["File đã chép"].replace("\\", "/").lower()] = row.get("Nguồn trên ổ Z", "")
    return out


def load_done(root: Path) -> set[str]:
    f = root / RESULT
    if not f.exists():
        return set()
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return {r["file"] for r in csv.DictReader(fh) if r.get("kết quả") in DONE}


def plan(root: Path, include_underscore: bool = False) -> Iterable[tuple[str, str, Path]]:
    """→ (thư mục dự án, luồng, file) theo <root>/<dự án>/<LUỒNG>/*.pdf (cả trong _NGOAI_MEP_xem_truoc nếu bật)."""
    bases = [root]
    if include_underscore:
        bases += [d for d in sorted(root.iterdir()) if d.is_dir() and d.name.startswith("_")]
    for base in bases:
        for proj in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
            for st in sorted(s for s in proj.iterdir() if s.is_dir()):
                if st.name.upper() not in STREAMS:
                    continue
                for f in sorted(x for x in st.iterdir() if x.is_file() and x.suffix.lower() == ".pdf"):
                    yield proj.name, st.name.upper(), f


def token_email(token: str | None) -> str | None:
    try:
        part = (token or "").split(".")[1]
        p = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return p.get("email") or p.get("sub")
    except (IndexError, ValueError):
        return None


def upload(client: httpx.Client, f: Path, meta: dict[str, Any], user: str, retries: int = 3) -> tuple[str, str]:
    """→ (kết quả, chi tiết). 409 trùng → 'ĐÃ CÓ'. Lỗi mạng / 5xx → thử lại."""
    for i in range(retries):
        try:
            with f.open("rb") as fh:
                r = client.post("/knowledge/uploads",
                                files={"file": (f.name, fh, "application/pdf")},
                                data={"metadata": json.dumps(meta, ensure_ascii=False), "user_id": user})
        except httpx.HTTPError as e:
            err = f"{type(e).__name__}: {e}"
        else:
            if r.status_code < 300:
                return "OK", r.json().get("job_id", "")
            detail = (r.json().get("detail") if "json" in r.headers.get("content-type", "") else r.text) or ""
            if r.status_code == 409:
                return "ĐÃ CÓ", str(detail)
            if r.status_code in (401, 403):
                raise SystemExit(f"Từ chối đăng nhập ({r.status_code}): token hết hạn/sai – lấy token mới rồi chạy lại "
                                 "(file đã upload sẽ được bỏ qua).")
            if r.status_code < 500:
                return "LỖI", f"{r.status_code} {detail}"
            err = f"{r.status_code} {detail}"
        time.sleep(2 * (i + 1))
    return "LỖI", err


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli.bulk_upload", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--api", default=os.environ.get("MEP_API", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("MEP_TOKEN"))
    ap.add_argument("--user", help="email ghi vết uploaded_by (mặc định lấy từ token)")
    ap.add_argument("--project", help="ép mã dự án cho mọi thư mục dự án")
    ap.add_argument("--only", help="chỉ upload thư mục dự án chứa chuỗi này (VD 26DA18)")
    ap.add_argument("--include-underscore", action="store_true", help="upload cả thư mục _NGOAI_MEP_xem_truoc")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch, không upload")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(args.folder)
    user = args.user or token_email(args.token) or "bulk_upload"
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    manifest, done = load_manifest(root), load_done(root)
    items = [(p, s, f) for p, s, f in plan(root, args.include_underscore) if not args.only or args.only in p]

    with httpx.Client(base_url=args.api.rstrip("/"), headers=headers, timeout=httpx.Timeout(600, connect=30)) as client:
        try:
            r = client.get("/projects")
            r.raise_for_status()
            projects = r.json()
        except httpx.HTTPError as e:
            print(f"Không gọi được {args.api}/projects: {e}", file=sys.stderr)
            return 2
        new = not (root / RESULT).exists()
        log = (root / RESULT).open("a", encoding="utf-8-sig", newline="")
        w = csv.writer(log)
        if new:
            w.writerow(["thời gian", "file", "dự án", "luồng", "phân hệ", "tiêu đề", "kết quả", "chi tiết"])
        counts: dict[str, int] = {}
        missing_proj: set[str] = set()
        for i, (proj_dir, stream, f) in enumerate(items, 1):
            rel = f.relative_to(root).as_posix()
            if rel in done:
                counts["bỏ qua (đã xong)"] = counts.get("bỏ qua (đã xong)", 0) + 1
                continue
            pid = args.project or match_project(proj_dir, projects)
            if not pid:
                missing_proj.add(proj_dir)
                continue
            src = manifest.get(rel.lower(), "")
            disc, title = discipline_hint(src), title_hint(f.name, src)
            meta: dict[str, Any] = {"project_id": pid, "stream": stream, "series": {"mode": "new"}}
            if disc:
                meta["disciplines"] = disc
            if title:
                meta["title"] = title
            if args.dry_run:
                res, detail = "DRY", ""
            else:
                res, detail = upload(client, f, meta, user)
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), rel, pid, stream, ",".join(disc), title or "", res,
                            detail])
                log.flush()
            counts[res] = counts.get(res, 0) + 1
            print(f"[{i}/{len(items)}] {res:6s} {pid} {stream} {','.join(disc) or '(tự nhận diện)'} | "
                  f"{title or f.name}{(' – ' + detail) if res == 'LỖI' else ''}", flush=True)
        log.close()
    print("Tổng:", counts)
    if missing_proj:
        print("Chưa có dự án trên hệ thống (tạo dự án với mã tương ứng rồi chạy lại):",
              ", ".join(f"{d} → {project_code(d)}" for d in sorted(missing_proj)))
    return 1 if counts.get("LỖI") else 0


if __name__ == "__main__":
    raise SystemExit(main())
