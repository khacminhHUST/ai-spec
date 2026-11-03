"""httpx.Auth cho connector OAuth: gắn access token của user đang chat (docs/mcp-connectors.md mục 2.4, 3).

- Token sắp hết hạn → TokenStore refresh (có khóa) trước khi gọi.
- Server trả 401 → refresh 1 lần rồi gửi lại; vẫn 401 → status=needs_reauth.
- 403 `insufficient_scope` → status=forbidden.
user_id do code đặt (AgentContext / tham số API), không bao giờ lấy từ tham số LLM.
"""
from __future__ import annotations

from typing import Any, Generator

import httpx

from app.services.agent.mcp.oauth.token_store import FORBIDDEN, NEEDS_REAUTH, TokenStore


class ConnectorAuth(httpx.Auth):
    def __init__(self, tokens: TokenStore, user_id: str, connector: dict[str, Any]):
        self.tokens = tokens
        self.user_id = user_id
        self.connector = connector

    def __repr__(self) -> str:
        return f"ConnectorAuth({self.connector.get('name')!r}, user={self.user_id!r})"

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        cid = self.connector["connector_id"]
        token = self.tokens.access_token(self.user_id, self.connector)
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            token = self.tokens.refresh(self.user_id, self.connector, rejected=token)
            request.headers["Authorization"] = f"Bearer {token}"
            response = yield request
            if response.status_code == 401:
                self.tokens.set_status(self.user_id, cid, NEEDS_REAUTH)
        if response.status_code == 403 and "insufficient_scope" in response.headers.get("WWW-Authenticate", ""):
            self.tokens.set_status(self.user_id, cid, FORBIDDEN)
