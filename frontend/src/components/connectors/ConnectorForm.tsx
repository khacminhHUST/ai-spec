import { useState } from "react";
import {
  createConnector,
  describeConnector,
  discover,
  testConnector,
  updateConnector,
  type AuthType,
  type Connector,
  type ConnectorInput,
  type Discovery,
  type HeaderItem,
  type Transport,
} from "../../mcpApi";

interface Props {
  userId: string;
  editing: Connector | null; // null = thêm mới
  onSaved: () => void;
  onCancel: () => void;
}

const AUTH_LABEL: Record<AuthType, string> = { oauth: "OAuth", none: "Không xác thực", headers: "Header tĩnh" };

/** Gợi ý mã connector từ tên miền: https://phaply.hawee.com.vn/mcp → "phaply" */
function slugFromUrl(url: string): string {
  try {
    const host = new URL(url).hostname.split(".")[0] ?? "";
    const slug = host.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32);
    return slug.length >= 2 ? slug : "";
  } catch {
    return "";
  }
}

export default function ConnectorForm({ userId, editing, onSaved, onCancel }: Props) {
  const [url, setUrl] = useState(editing?.url ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const [displayName, setDisplayName] = useState(editing?.display_name ?? "");
  const [description, setDescription] = useState(editing?.description ?? "");
  const [transport, setTransport] = useState<Transport>((editing?.transport as Transport) ?? "streamable_http");
  const [authType, setAuthType] = useState<AuthType>(editing?.auth_type ?? "oauth");
  const [headers, setHeaders] = useState<HeaderItem[]>(
    editing?.header_names.length ? editing.header_names.map((n) => ({ name: n, value: "" })) : [{ name: "", value: "" }],
  );
  const [headersTouched, setHeadersTouched] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [found, setFound] = useState<Discovery | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = async () => {
    setError(null);
    setFound(null);
    setBusy(true);
    try {
      const d = await discover(url.trim());
      setFound(d);
      if (!name.trim()) setName(slugFromUrl(url.trim()));
      setAuthType(d.auth_type);
      if (d.auth_type === "oauth" && !d.registration) setAdvanced(true);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const canSuggest = !!editing && (editing.auth_type !== "oauth" || editing.status === "connected");
  const suggest = async () => {
    if (!editing) return;
    setError(null);
    setBusy(true);
    try {
      setDescription((await describeConnector(editing.connector_id, userId)).description);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const setHeader = (i: number, patch: Partial<HeaderItem>) => {
    setHeadersTouched(true);
    setHeaders((hs) => hs.map((h, j) => (j === i ? { ...h, ...patch } : h)));
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const body: ConnectorInput = {
      name: name.trim(),
      display_name: displayName.trim() || undefined,
      description: description.trim(),
      url: url.trim(),
      transport,
      auth_type: authType,
      enabled,
    };
    if (authType === "headers" && (!editing || headersTouched || editing.auth_type !== "headers"))
      body.headers = headers.filter((h) => h.name.trim());
    if (authType === "oauth" && clientId.trim()) {
      body.client_id = clientId.trim();
      body.client_secret = clientSecret.trim() || undefined;
    }
    try {
      if (editing) await updateConnector(editing.connector_id, body);
      else {
        const c = await createConnector(body, userId);
        // Không cần token: thử kết nối ngay để server trả danh sách tool → tự điền mô tả nếu đang trống
        if (c.auth_type !== "oauth") await testConnector(c.connector_id, userId).catch(() => undefined);
      }
      onSaved();
    } catch (err) {
      setError(String((err as Error).message ?? err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="cn-form" onSubmit={save}>
      <h3>{editing ? `Sửa connector ${editing.name}` : "Thêm connector"}</h3>
      <label>
        URL MCP server
        <div className="cn-row">
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://kho.hawee.com.vn/mcp" required />
          <button type="button" onClick={check} disabled={busy || !url.trim()}>
            Kiểm tra
          </button>
        </div>
      </label>
      {found && (
        <div className="cn-note">
          Gợi ý: <b>{AUTH_LABEL[found.auth_type]}</b>
          {found.issuer && <> · Authorization Server: {found.issuer}</>}
          {found.resource && <> · resource: {found.resource}</>}
          {found.auth_type === "oauth" && !found.registration && <> · AS không hỗ trợ đăng ký tự động – nhập client_id ở Nâng cao</>}
          {found.notes.map((n) => (
            <div key={n} className="muted small">
              {n}
            </div>
          ))}
        </div>
      )}
      <div className="cn-grid">
        <label>
          Mã connector (a-z, 0-9, _ – tiền tố tên tool)
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            pattern="[a-z0-9_]{2,32}"
            placeholder="vd: mail_hawee"
            autoComplete="off"
            name="mcp-connector-code"
            required
          />
        </label>
        <label>
          Tên hiển thị
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="vd: Hộp thư HAWEE"
            autoComplete="off"
            name="mcp-connector-label"
          />
        </label>
        <label>
          Transport
          <select value={transport} onChange={(e) => setTransport(e.target.value as Transport)}>
            <option value="streamable_http">streamable_http</option>
            <option value="sse">sse</option>
          </select>
        </label>
        <label className="cn-check">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Bật
        </label>
      </div>
      <label>
        Mô tả cho agent (nguồn này chứa dữ liệu gì, làm được gì)
        <div className="cn-row">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            maxLength={500}
            placeholder="Để trống: tự điền khi kết nối thành công lần đầu (giới thiệu của server hoặc tóm tắt danh sách tool)"
          />
          {editing && (
            <button
              type="button"
              onClick={suggest}
              disabled={busy || !canSuggest}
              title={canSuggest ? "" : "Kết nối trước để server trả danh sách tool"}
            >
              Gợi ý
            </button>
          )}
        </div>
      </label>
      <fieldset className="cn-auth">
        <legend>Kiểu xác thực</legend>
        {(Object.keys(AUTH_LABEL) as AuthType[]).map((t) => (
          <label key={t} className="cn-check">
            <input type="radio" name="auth" checked={authType === t} onChange={() => setAuthType(t)} /> {AUTH_LABEL[t]}
          </label>
        ))}
      </fieldset>
      {authType === "headers" && (
        <div className="cn-headers">
          {editing?.auth_type === "headers" && !headersTouched && (
            <div className="muted small">Giá trị header đã lưu được ẩn – nhập lại nếu muốn đổi.</div>
          )}
          {headers.map((h, i) => (
            <div key={i} className="cn-row">
              <input placeholder="Tên header (VD Authorization)" value={h.name} onChange={(e) => setHeader(i, { name: e.target.value })} />
              <input
                placeholder="Giá trị (cho phép ${TEN_BIEN} trong .env)"
                type="password"
                value={h.value}
                onChange={(e) => setHeader(i, { value: e.target.value })}
              />
              <button type="button" onClick={() => setHeaders((hs) => hs.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
          <button type="button" onClick={() => setHeaders((hs) => [...hs, { name: "", value: "" }])}>
            + Header
          </button>
        </div>
      )}
      {authType === "oauth" && (
        <details open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
          <summary>Nâng cao (client_id thủ công)</summary>
          <div className="cn-grid">
            <label>
              client_id
              <input value={clientId} onChange={(e) => setClientId(e.target.value)} />
            </label>
            <label>
              client_secret (nếu có)
              <input type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} />
            </label>
          </div>
        </details>
      )}
      {error && <div className="banner-error">{error}</div>}
      <div className="cn-row">
        <button type="submit" className="cn-primary" disabled={busy}>
          {busy ? "Đang xử lý…" : "Lưu"}
        </button>
        <button type="button" onClick={onCancel}>
          Hủy
        </button>
      </div>
    </form>
  );
}
