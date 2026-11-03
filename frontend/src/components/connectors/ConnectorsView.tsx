import { useCallback, useEffect, useState } from "react";
import {
  connect,
  deleteConnector,
  disconnect,
  listConnectors,
  testConnector,
  updateConnector,
  type ConnStatus,
  type Connector,
  type OAuthMessage,
} from "../../mcpApi";
import ConnectorForm from "./ConnectorForm";
import ToolList from "./ToolList";
import "./connectors.css";

const STATUS_LABEL: Record<ConnStatus, string> = {
  not_connected: "Chưa kết nối",
  connected: "Đã kết nối",
  needs_reauth: "Cần kết nối lại",
  forbidden: "Chưa được cấp quyền",
  ready: "Sẵn sàng",
};
const AUTH_LABEL: Record<string, string> = { oauth: "OAuth", none: "Không xác thực", headers: "Header tĩnh" };

export default function ConnectorsView({ userId }: { userId: string }) {
  const [items, setItems] = useState<Connector[]>([]);
  const [form, setForm] = useState<{ editing: Connector | null } | null>(null);
  const [openTools, setOpenTools] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listConnectors(userId)
      .then((c) => {
        setItems(c);
        setError(null);
      })
      .catch((e) => setError(String((e as Error).message ?? e)));
  }, [userId]);
  useEffect(load, [load]);

  // Trang callback OAuth (popup) báo kết quả qua postMessage
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const d = e.data as OAuthMessage;
      if (e.origin !== window.location.origin || d?.type !== "mcp-oauth") return;
      setNotice(d.message || STATUS_LABEL[d.status as ConnStatus] || d.status);
      load();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [load]);

  const act = async (f: () => Promise<string | void>) => {
    setError(null);
    setNotice(null);
    try {
      const msg = await f();
      if (msg) setNotice(msg);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
    load();
  };

  const doConnect = (c: Connector) => {
    // Mở popup ngay trong sự kiện click (tránh bị chặn), điều hướng sau khi có authorize_url
    const popup = window.open("about:blank", "mep-oauth", "width=520,height=720");
    act(async () => {
      try {
        const { authorize_url } = await connect(c.connector_id, userId);
        if (popup) popup.location.href = authorize_url;
        else window.location.href = authorize_url;
        return "Đăng nhập và đồng ý ở cửa sổ mới (Server A có thể yêu cầu đăng nhập lại – bình thường).";
      } catch (e) {
        popup?.close();
        throw e;
      }
    });
  };

  return (
    <div className="cn-view">
      <div className="cn-head">
        <h2>Kết nối MCP</h2>
        <button className="cn-primary" onClick={() => setForm({ editing: null })}>
          + Thêm connector
        </button>
      </div>
      <div className="cn-note small">
        Chưa có đăng nhập: trạng thái kết nối OAuth gắn với <b>User ID</b> ở thanh bên ({userId}). Ai biết User ID của người
        khác thì dùng được kết nối OAuth của người đó – rủi ro đã biết, chấp nhận trong giai đoạn này. Mọi người đều
        thêm/sửa được connector. Connector từ <code>config/mcp.yaml</code> chỉ đọc.
      </div>
      {notice && <div className="cn-ok">{notice}</div>}
      {error && <div className="banner-error">{error}</div>}
      {form && (
        <ConnectorForm
          userId={userId}
          editing={form.editing}
          onCancel={() => setForm(null)}
          onSaved={() => {
            setForm(null);
            load();
          }}
        />
      )}
      <table className="cn-table">
        <thead>
          <tr>
            <th>Tên</th>
            <th>URL</th>
            <th>Xác thực</th>
            <th>Bật</th>
            <th>Trạng thái của tôi</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td colSpan={6} className="muted small">
                Chưa có connector
              </td>
            </tr>
          )}
          {items.map((c) => (
            <ConnectorRow
              key={c.connector_id}
              c={c}
              userId={userId}
              toolsOpen={openTools === c.connector_id}
              onToggleTools={() => setOpenTools(openTools === c.connector_id ? null : c.connector_id)}
              onConnect={() => doConnect(c)}
              onDisconnect={() => act(async () => (await disconnect(c.connector_id, userId)).message)}
              onTest={() => act(async () => (await testConnector(c.connector_id, userId)).message)}
              onToggleEnabled={() => act(async () => void (await updateConnector(c.connector_id, { enabled: !c.enabled })))}
              onEdit={() => setForm({ editing: c })}
              onDelete={() => {
                if (window.confirm(`Xóa connector ${c.name}? Token và cấu hình tool đi kèm cũng bị xóa.`))
                  act(() => deleteConnector(c.connector_id));
              }}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface RowProps {
  c: Connector;
  userId: string;
  toolsOpen: boolean;
  onToggleTools: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onTest: () => void;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function ConnectorRow({ c, userId, toolsOpen, ...h }: RowProps) {
  const oauth = c.auth_type === "oauth" && !c.readonly;
  const linked = c.status !== "not_connected";
  return (
    <>
      <tr className={c.enabled ? "" : "cn-disabled"}>
        <td>
          <b>{c.display_name || c.name}</b>
          <div className="muted small">
            {c.name}
            {c.readonly && <span className="chip">yaml · chỉ đọc</span>}
          </div>
          {c.description ? (
            <div className="cn-desc small">{c.description}</div>
          ) : (
            <div className="muted small">Chưa có mô tả – agent khó biết khi nào dùng nguồn này</div>
          )}
        </td>
        <td className="cn-url small">{c.url}</td>
        <td>
          {AUTH_LABEL[c.auth_type] ?? c.auth_type}
          {c.header_names.length > 0 && <div className="muted small">{c.header_names.join(", ")}</div>}
        </td>
        <td>
          {c.readonly ? (
            "Có"
          ) : (
            <input type="checkbox" checked={c.enabled} onChange={h.onToggleEnabled} title="Bật/tắt connector" />
          )}
        </td>
        <td>
          <span className={`cn-status cn-${c.status}`}>{STATUS_LABEL[c.status] ?? c.status}</span>
        </td>
        <td className="cn-actions">
          {oauth && c.enabled && (
            <button onClick={h.onConnect}>{c.status === "connected" ? "Kết nối lại" : "Kết nối"}</button>
          )}
          {oauth && linked && <button onClick={h.onDisconnect}>Ngắt</button>}
          {!c.readonly && (!oauth || linked) && <button onClick={h.onTest}>Thử</button>}
          {(!oauth || linked) && <button onClick={h.onToggleTools}>{toolsOpen ? "Ẩn tool" : "Tool"}</button>}
          {!c.readonly && <button onClick={h.onEdit}>Sửa</button>}
          {!c.readonly && (
            <button className="cn-danger" onClick={h.onDelete}>
              Xóa
            </button>
          )}
        </td>
      </tr>
      {toolsOpen && (
        <tr>
          <td colSpan={6}>
            <ToolList connector={c} userId={userId} />
          </td>
        </tr>
      )}
    </>
  );
}
