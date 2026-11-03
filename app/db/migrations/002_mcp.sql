-- MCP connectors + OAuth 2.1 phía client (docs/mcp-connectors.md mục 4).
-- Cột *_enc: mã hóa Fernet (khóa MEP_SECRET_KEY trong .env), không lưu plaintext.

CREATE TABLE IF NOT EXISTS mcp_connectors (
    connector_id  TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9_]{2,32}$'),
    display_name  TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL,
    transport     TEXT NOT NULL DEFAULT 'streamable_http' CHECK (transport IN ('streamable_http','sse')),
    auth_type     TEXT NOT NULL CHECK (auth_type IN ('oauth','none','headers')),
    headers_enc   TEXT,
    resource      TEXT,
    issuer        TEXT,
    as_metadata   JSONB,
    scopes        TEXT,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Mỗi (issuer, redirect_uri) chỉ đăng ký client 1 lần (DCR) hoặc nhập tay
CREATE TABLE IF NOT EXISTS oauth_clients (
    issuer             TEXT NOT NULL,
    redirect_uri       TEXT NOT NULL,
    client_id          TEXT NOT NULL,
    client_secret_enc  TEXT,
    auth_method        TEXT NOT NULL DEFAULT 'none'
                       CHECK (auth_method IN ('none','client_secret_post','client_secret_basic')),
    registered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (issuer, redirect_uri)
);

-- state OAuth dùng 1 lần, sống state_ttl_s
CREATE TABLE IF NOT EXISTS oauth_pending (
    state              TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    connector_id       TEXT NOT NULL REFERENCES mcp_connectors (connector_id) ON DELETE CASCADE,
    code_verifier_enc  TEXT NOT NULL,
    expires_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_pending_expires_idx ON oauth_pending (expires_at);

CREATE TABLE IF NOT EXISTS mcp_user_tokens (
    user_id            TEXT NOT NULL,
    connector_id       TEXT NOT NULL REFERENCES mcp_connectors (connector_id) ON DELETE CASCADE,
    access_token_enc   TEXT NOT NULL,
    refresh_token_enc  TEXT,
    expires_at         TIMESTAMPTZ,
    scope              TEXT,
    status             TEXT NOT NULL DEFAULT 'connected' CHECK (status IN ('connected','forbidden','needs_reauth')),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, connector_id)
);

-- Ghi đè đọc/ghi/tắt theo tool (tool_name = tên gốc trên MCP server, không tiền tố)
CREATE TABLE IF NOT EXISTS mcp_tool_overrides (
    connector_id  TEXT NOT NULL REFERENCES mcp_connectors (connector_id) ON DELETE CASCADE,
    tool_name     TEXT NOT NULL,
    access        TEXT NOT NULL CHECK (access IN ('read','write','disabled')),
    PRIMARY KEY (connector_id, tool_name)
);
