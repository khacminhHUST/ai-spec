"""MCP server "Thông tin dự án" (docs/mcp-server.md): Claude tra cứu tài liệu dự án MEP, chỉ đọc.

Chạy chung tiến trình với app (app/main.py): `McpServerRoute` chuyển đúng 2 đường dẫn sang đây, còn lại đi FastAPI.
  POST <path>         MCP streamable HTTP (stateless, trả JSON) – cần Bearer token user-management
  GET  <prm_path>     Protected Resource Metadata (RFC 9728) – Claude tự tìm máy chủ đăng nhập
Tool: search_project_docs (Query Pipeline N1–N6, dùng chung với agent), list_projects. Không ghi gì vào MEP.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from app.services.agent.state import AgentContext
from app.services.agent.tools.search_docs import SearchDocs, search_payload, summarize_payload
from app.services.mcp_server.auth import PlannerTokenVerifier
from app.services.mcp_server.config import ServerConfig, load_config

log = logging.getLogger(__name__)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

NAME = "Thông tin dự án MEP (HAWEE)"
INSTRUCTIONS = """Tra cứu tài liệu dự án cơ điện (MEP) của HAWEE, CHỈ ĐỌC: thuyết minh kỹ thuật, spec, chỉ dẫn kỹ thuật
của tư vấn, tiêu chuẩn đơn vị vận hành (Westin/Marriott), TCVN/QCVN, hợp đồng.

Cách dùng:
1. Chưa rõ dự án → list_projects. Câu hỏi nhắc dự án/công trình → điền project_mentions (VD ["S5"]).
   Cần biết kho có những tài liệu nào / thiếu bộ môn nào → list_project_documents (đừng suy ra từ kết quả tìm kiếm).
2. search_project_docs mỗi lần 1 chủ đề, câu hỏi đầy đủ, cụ thể. Nhiều chủ đề / so sánh 2 nguồn → gọi nhiều lần.
3. Trả lời CHỈ dựa trên `blocks`, trích dẫn [n] theo `citations` (tài liệu, revision, mục, trang).
   Nhiều nguồn mâu thuẫn → ưu tiên block có `priority` nhỏ hơn (1 là cao nhất) và nêu rõ chỗ khác nhau.
4. status NOT_FOUND → nói rõ tài liệu dự án chưa có thông tin đó, KHÔNG tự điền số liệu từ kiến thức chung.
   ASK_CLARIFY → hỏi lại người dùng theo clarify_options. PARTIAL → nêu phần còn thiếu (`missing`).
5. Block `truncated=true` là đã bị cắt bớt: cần đủ số liệu thì tra lại hẹp hơn (1 mục / 1 bảng)."""

SEARCH_DESC = """Tra cứu tài liệu dự án MEP (Query Pipeline: nhận diện dự án → tìm → xếp hạng → kiểm đủ).
- question: câu hỏi đầy đủ, 1 chủ đề (VD "Bảng 4.3 lưu lượng gió tươi theo khu vực trong Thuyết minh S5").
- project_mentions: tên/mã dự án hoặc công trình (VD ["S5"]); bỏ trống nếu người dùng chỉ có 1 dự án.
- disciplines: HVAC, ELECTRICAL, ELV, PLUMBING, FIRE, LIFT, BMS, GENERAL (không chắc → bỏ trống).
- streams: LEGAL_STANDARD (TCVN/QCVN), OPERATOR_STANDARD (Westin/Marriott), DESIGN_CONSULTANT (thuyết minh/spec/chỉ
  dẫn), CONTRACT (hợp đồng) – chỉ điền khi cần đúng 1 loại nguồn.
Trả về JSON: status (OK | PARTIAL | NOT_FOUND | ASK_CLARIFY), projects, blocks (n, project, stream, priority, doc, rev,
section, pages, vi/en = nội dung), citations."""

LIST_DOCS_DESC = """Danh mục tài liệu đang hiệu lực trong kho theo dự án (tiêu đề, luồng, phân hệ, công trình, revision,
ngôn ngữ) + số tài liệu theo luồng. Dùng khi cần biết kho có những tài liệu gì / còn thiếu bộ môn nào – KHÔNG suy ra
từ vài kết quả search_project_docs.
- project_mentions: tên/mã/alias dự án (VD ["CT05"]); bỏ trống = mọi dự án người dùng được xem.
- streams / disciplines: lọc như search_project_docs (tùy chọn)."""


def _user() -> str:
    """Email trong token đã verify (AuthContextMiddleware). Thiếu → lỗi tool."""
    tok = get_access_token()
    email = ((tok.claims or {}).get("email") if tok else "") or ""
    if not email:
        raise ToolError("Chưa đăng nhập")
    return email


class KbMcp:
    """FastMCP + ASGI app + đường dẫn. deps gắn vào lúc app khởi động (attach)."""

    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.deps = None
        self._search: SearchDocs | None = None
        self.mcp = FastMCP(
            NAME, instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
            streamable_http_path=cfg.path,
            token_verifier=PlannerTokenVerifier(cfg),
            # resource_server_url → SDK trả 401 kèm WWW-Authenticate: resource_metadata=<prm_url>.
            # Kiểm `aud` do verifier làm (chấp nhận cả resource_url lẫn server_name) – tắt kiểm resource của SDK.
            auth=AuthSettings(issuer_url=cfg.issuer, resource_server_url=cfg.resource_url,
                              validate_token_resource=False),
            # Sau reverse proxy Host là tên miền công khai; chống DNS rebinding không áp dụng (đã có token).
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        self._register_tools()
        self.asgi = self.mcp.streamable_http_app()

    # ---------------------------------------------------------------- vòng đời
    def attach(self, deps) -> None:
        self.deps = deps
        self._search = None

    def search(self) -> SearchDocs:
        if self.deps is None:
            raise ToolError("Hệ thống đang khởi động, thử lại sau")
        if self._search is None:
            self._search = SearchDocs(self.deps)
        return self._search

    def session(self):
        return self.mcp.session_manager.run()

    def prm(self) -> dict[str, Any]:
        """RFC 9728. issuer để TRẦN (không "/" cuối) – khớp `iss` của user-management."""
        return {"resource": self.cfg.resource_url, "authorization_servers": [self.cfg.issuer],
                "bearer_methods_supported": ["header"], "resource_name": NAME}

    # ---------------------------------------------------------------- quyền
    def require(self) -> str:
        """→ user_id (email). Thiếu quyền đọc trong token → lỗi tool (kết nối vẫn giữ)."""
        user = _user()
        need = self.cfg.require_permission
        tok = get_access_token()
        if need and need not in (tok.scopes if tok else []):
            raise ToolError(f"Tài khoản {user} chưa được cấp quyền '{need}' trên '{self.cfg.server_name}' – "
                            "liên hệ quản trị user-management.")
        return user

    # ---------------------------------------------------------------- tool
    def run_search(self, user: str, question: str, project_mentions, disciplines, streams) -> dict[str, Any]:
        ctx = AgentContext(user_id=user, conversation_id=f"mcp-{uuid.uuid4().hex}")
        payload, _, _ = search_payload(self.search(), ctx, [], question, project_mentions, disciplines, streams,
                                       sibling_tables_max=int(self._agent_cfg().get("sibling_tables_max", 0)))
        if payload["status"] != "ASK_CLARIFY":
            count = self.deps.count_tokens
            if count(json.dumps(payload, ensure_ascii=False)) > self.cfg.result_max_tokens:
                payload = {**summarize_payload(payload, self.cfg.result_max_tokens, count),
                           "note": "Kết quả dài: một số block đã bị cắt (truncated=true). "
                                   "Cần đủ số liệu thì tra lại hẹp hơn (1 mục / 1 bảng)."}
        return payload

    @staticmethod
    def _agent_cfg() -> dict[str, Any]:
        from app.services.agent.config import agent_cfg

        return agent_cfg()["agent"]

    def list_projects_for(self, user: str) -> list[dict[str, Any]]:
        if self.deps is None:
            raise ToolError("Hệ thống đang khởi động, thử lại sau")
        ids = self.deps.repo.user_projects(user)
        info = self.deps.repo.project_info(ids)
        return [{"project_id": pid, "name": info.get(pid, {}).get("name", pid),
                 "aliases": info.get(pid, {}).get("aliases") or [],
                 "governing_language": info.get(pid, {}).get("governing_language", "vi")} for pid in ids]

    def list_documents_for(self, user: str, project_mentions: list[str] | None, streams: list[str] | None,
                           disciplines: list[str] | None) -> dict[str, Any]:
        """Danh mục tài liệu đang hiệu lực (is_latest) theo dự án người dùng được xem – để Claude biết kho có gì,
        không phải đoán từ vài kết quả tìm kiếm. project_mentions khớp mã / tên / alias (không dấu, không hoa)."""
        from app.utils.keys import normalize_alias

        if self.deps is None:
            raise ToolError("Hệ thống đang khởi động, thử lại sau")
        repo = self.deps.repo
        ids = repo.user_projects(user)
        info = repo.project_info(ids)
        if project_mentions:
            want = {normalize_alias(m) for m in project_mentions if m and m.strip()}
            names: dict[str, set[str]] = {pid: {normalize_alias(pid), normalize_alias(info.get(pid, {}).get("name", ""))}
                                          for pid in ids}
            for a in repo.aliases(ids):
                names.setdefault(a["project_id"], set()).add(a["alias_norm"])
            ids = [pid for pid in ids if names.get(pid, set()) & want]
            if not ids:
                raise ToolError(f"Không tìm thấy dự án khớp {project_mentions} – gọi list_projects để xem tên/mã.")
        st = {s.upper() for s in streams or []}
        disc = {d.upper() for d in disciplines or []}
        docs = [d for d in repo.documents(ids)
                if (not st or (d.get("stream") or "").upper() in st)
                and (not disc or disc & {x.upper() for x in d.get("disciplines") or []})]
        out = []
        for pid in ids:
            mine = sorted((d for d in docs if d["project_id"] == pid),
                          key=lambda d: (d.get("stream") or "", d.get("title") or ""))
            by_stream: dict[str, int] = {}
            for d in mine:
                by_stream[d.get("stream") or "?"] = by_stream.get(d.get("stream") or "?", 0) + 1
            out.append({"project_id": pid, "name": info.get(pid, {}).get("name", pid), "n_documents": len(mine),
                        "by_stream": by_stream,
                        "documents": [{"title": d.get("title"), "stream": d.get("stream"),
                                       "disciplines": list(d.get("disciplines") or []), "building": d.get("building"),
                                       "revision": d.get("revision"), "languages": list(d.get("languages") or [])}
                                      for d in mine]})
        return {"projects": out,
                "note": "Chỉ liệt kê bản đang hiệu lực. Nội dung chi tiết → search_project_docs."}

    def _register_tools(self) -> None:
        kb = self

        @self.mcp.tool(name="search_project_docs", description=SEARCH_DESC,
                       annotations=READ_ONLY)
        async def search_project_docs(question: str, project_mentions: list[str] | None = None,
                                      disciplines: list[str] | None = None, streams: list[str] | None = None) -> str:
            user = kb.require()
            try:
                payload = await anyio.to_thread.run_sync(kb.run_search, user, question, project_mentions,
                                                         disciplines, streams)
            except ToolError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("[MCP-SERVER] tra cứu lỗi (user=%s)", user)
                raise ToolError(f"Tra cứu lỗi: {type(e).__name__}") from None
            log.info("[MCP-SERVER] search user=%s status=%s blocks=%d", user, payload.get("status"),
                     len(payload.get("blocks") or []))
            return json.dumps(payload, ensure_ascii=False)

        @self.mcp.tool(name="list_projects", description="Danh sách dự án người dùng được tra cứu (tên, alias).",
                       annotations=READ_ONLY)
        async def list_projects() -> str:
            user = kb.require()
            rows = await anyio.to_thread.run_sync(kb.list_projects_for, user)
            return json.dumps({"projects": rows}, ensure_ascii=False)

        @self.mcp.tool(name="list_project_documents", description=LIST_DOCS_DESC, annotations=READ_ONLY)
        async def list_project_documents(project_mentions: list[str] | None = None, streams: list[str] | None = None,
                                         disciplines: list[str] | None = None) -> str:
            user = kb.require()
            out = await anyio.to_thread.run_sync(kb.list_documents_for, user, project_mentions, streams, disciplines)
            return json.dumps(out, ensure_ascii=False)


class McpServerRoute:
    """Middleware ASGI: <path> → MCP, <prm_path> → metadata; còn lại đi app chính (không đụng route FastAPI)."""

    def __init__(self, app, kb: KbMcp):
        self.app = app
        self.kb = kb

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"].rstrip("/") or "/"
            if path == self.kb.cfg.path:      # "/kb-mcp/" → "/kb-mcp": tránh redirect 307 của Starlette
                scope = {**scope, "path": self.kb.cfg.path, "raw_path": self.kb.cfg.path.encode()}
                return await self.kb.asgi(scope, receive, send)
            if path == self.kb.cfg.prm_path and scope["method"] in ("GET", "HEAD"):
                return await JSONResponse(self.kb.prm())(scope, receive, send)
        return await self.app(scope, receive, send)


def build(cfg: ServerConfig | None = None) -> KbMcp | None:
    cfg = cfg or load_config()
    return KbMcp(cfg) if cfg.enabled else None
