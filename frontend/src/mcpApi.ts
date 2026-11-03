// API MCP connector (backend app/api/routes/mcp.py, proxy /mcp trong vite.config.ts)

export type AuthType = "oauth" | "none" | "headers";
export type Transport = "streamable_http" | "sse";
export type Access = "read" | "write" | "disabled";
export type ConnStatus = "not_connected" | "connected" | "needs_reauth" | "forbidden" | "ready";

export interface Connector {
  connector_id: string;
  name: string;
  display_name: string;
  description: string; // mô tả cho danh mục nguồn của agent
  url: string;
  transport: string;
  auth_type: AuthType;
  enabled: boolean;
  source: "db" | "yaml";
  readonly: boolean;
  status: ConnStatus;
  header_names: string[];
  resource?: string | null;
  issuer?: string | null;
  registration?: boolean;
}

export interface Discovery {
  auth_type: "oauth" | "none";
  status_code: number;
  resource: string | null;
  issuer: string | null;
  scopes: string[] | null;
  has_prm: boolean;
  registration: boolean;
  notes: string[];
}

export interface HeaderItem {
  name: string;
  value: string;
}

export interface ConnectorInput {
  name: string;
  display_name?: string;
  description?: string;
  url: string;
  transport: Transport;
  auth_type: AuthType;
  headers?: HeaderItem[];
  client_id?: string;
  client_secret?: string;
  enabled: boolean;
}

export interface McpTool {
  name: string;
  full_name: string;
  description: string;
  default_access: string;
  override: Access | null;
  access: Access;
  readonly?: boolean;
}

export interface TestResult {
  ok: boolean;
  status: string;
  message: string;
  tool_count: number;
}

/** Lỗi API: detail là chuỗi hoặc {code, message} */
async function call<T>(r: Response): Promise<T> {
  if (r.ok) return (r.status === 204 ? undefined : await r.json()) as T;
  let msg = `${r.status}`;
  try {
    const body = await r.json();
    const d = body?.detail;
    msg = typeof d === "string" ? d : d?.message ?? JSON.stringify(d ?? body);
  } catch {
    /* body không phải JSON */
  }
  throw new Error(msg);
}

const q = (userId: string) => `user_id=${encodeURIComponent(userId)}`;
const id = (cid: string) => encodeURIComponent(cid);
const post = (url: string, body?: unknown) =>
  fetch(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const listConnectors = (userId: string) =>
  fetch(`/mcp/connectors?${q(userId)}`).then((r) => call<Connector[]>(r));

export const discover = (url: string) => post("/mcp/connectors/discover", { url }).then((r) => call<Discovery>(r));

export const createConnector = (body: ConnectorInput, userId: string) =>
  post("/mcp/connectors", { ...body, user_id: userId }).then((r) => call<Connector>(r));

export const updateConnector = (cid: string, body: Partial<ConnectorInput>) =>
  fetch(`/mcp/connectors/${id(cid)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => call<Connector>(r));

export const deleteConnector = (cid: string) =>
  fetch(`/mcp/connectors/${id(cid)}`, { method: "DELETE" }).then((r) => call<void>(r));

export const connect = (cid: string, userId: string) =>
  post(`/mcp/connectors/${id(cid)}/connect?${q(userId)}`).then((r) => call<{ authorize_url: string }>(r));

export const disconnect = (cid: string, userId: string) =>
  post(`/mcp/connectors/${id(cid)}/disconnect?${q(userId)}`).then((r) => call<{ message: string }>(r));

export const testConnector = (cid: string, userId: string) =>
  post(`/mcp/connectors/${id(cid)}/test?${q(userId)}`).then((r) => call<TestResult>(r));

/** Gợi ý mô tả (không lưu): giới thiệu server tự trả → LLM tóm tắt danh sách tool → tên tool */
export const describeConnector = (cid: string, userId: string) =>
  post(`/mcp/connectors/${id(cid)}/describe?${q(userId)}`).then((r) => call<{ description: string }>(r));

export const listTools = (cid: string, userId: string) =>
  fetch(`/mcp/connectors/${id(cid)}/tools?${q(userId)}`).then((r) => call<McpTool[]>(r));

export const setToolAccess = (cid: string, tool: string, access: Access | null) =>
  fetch(`/mcp/connectors/${id(cid)}/tools/${encodeURIComponent(tool)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access }),
  }).then((r) => call<{ tool: string; override: Access | null }>(r));

/** Tin nhắn trang callback OAuth gửi về cửa sổ chính (window.opener.postMessage) */
export interface OAuthMessage {
  type: "mcp-oauth";
  connector_id: string | null;
  status: string;
  message: string;
}
