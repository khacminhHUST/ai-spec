"""Điểm khởi chạy khi app nhúng trong web Tri thức (deploy/trithuc) – không sửa app/main.py.

    uvicorn app.trithuc_main:app

Khác app.main:app:
  - GET /healthz (gateway nginx / Docker kiểm tra sống, không cần token);
  - MEP_AUTH_MODE=trusted_proxy → TrustedProxyAuth: user_id lấy từ token web Tri thức (app/api/trusted_proxy.py).
"""
from __future__ import annotations

import os

from app.api.trusted_proxy import TrustedProxyAuth
from app.main import create_app


def create_trithuc_app(**kwargs):
    app = create_app(**kwargs)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    if os.environ.get("MEP_AUTH_MODE", "").strip() == "trusted_proxy":
        app.add_middleware(TrustedProxyAuth, user_claim=os.environ.get("MEP_AUTH_USER_CLAIM") or None)
    return app


app = create_trithuc_app()
