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
import Icon from "../Icon";
import ConnectorForm from "./ConnectorForm";
import ToolList from "./ToolList";
import "../knowledge/knowledge.css"; // kiểu hộp thoại nổi (.kp-modal)
import "./connectors.css";

const STATUS_LABEL: Record<ConnStatus, string> = {
  not_connected: "Chưa kết nối",
  connected: "Đã kết nối",
  needs_reauth: "Hết hạn đăng nhập",
  forbidden: "Chưa được cấp quyền",
  ready: "Sẵn sàng",
};

/** Tên hiển thị; bỏ qua giá trị trình duyệt tự điền nhầm (email) → dùng mã connector */
function title(c: Connector): string {
  const d = c.display_name?.trim();
  return d && !d.includes("@") ? d : c.name;
}

function host(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export default function ConnectorsView({ userId }: { userId: string }) {
  const [items, setItems] = useState<Connector[]>([]);
  const [form, setForm] = useState<{ editing: Connector | null } | null>(null);
  const [openTools, setOpenTools] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [checking, setChecking] = useState<string[]>([]);
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

  /** Kiểm tra kết nối thật (server cập nhật trạng thái: hết hạn / bị chặn / đã kết nối) rồi tải lại danh sách */
  const recheck = useCallback(
    (c: Connector) => {
      setChecking((x) => [...x, c.connector_id]);
      testConnector(c.connector_id, userId)
        .catch(() => undefined)
        .finally(() => {
          setChecking((x) => x.filter((id) => id !== c.connector_id));
          load();
        });
    },
    [userId, load],
  );

  // Mở trang: tự kiểm tra các connector đang bật và đã kết nối (hoặc không cần đăng nhập)
  useEffect(() => {
    listConnectors(userId)
      .then((list) =>
        list
          .filter((c) => !c.readonly && c.enabled && (c.auth_type !== "oauth" || c.status !== "not_connected"))
          .forEach(recheck),
      )
      .catch(() => undefined);
  }, [userId, recheck]);

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

  // Đóng menu "…" khi bấm ra ngoài
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [menu]);

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

  /** popup: mở sẵn trong sự kiện bấm (tránh bị chặn), điều hướng sau khi có authorize_url */
  const doConnect = (c: Connector, opened?: Window | null) => {
    const popup = opened ?? window.open("about:blank", "mep-oauth", "width=520,height=720");
    act(async () => {
      try {
        const { authorize_url } = await connect(c.connector_id, userId);
        if (popup) popup.location.href = authorize_url;
        else window.location.href = authorize_url;
        return "Đăng nhập và đồng ý ở cửa sổ mới.";
      } catch (e) {
        popup?.close();
        throw e;
      }
    });
  };

  return (
    <div className="cn-view">
      <div className="cn-head">
        <h2>Kết nối</h2>
        <button className="cn-primary" onClick={() => setForm({ editing: null })}>
          <Icon name="plus" /> Thêm
        </button>
      </div>
      {notice && <div className="cn-ok">{notice}</div>}
      {error && <div className="banner-error">{error}</div>}
      {form && (
        <ConnectorForm
          userId={userId}
          editing={form.editing}
          existingNames={items.map((c) => c.name)}
          onCancel={() => setForm(null)}
          onSaved={(c, popup) => {
            setForm(null);
            if (c && popup) doConnect(c, popup);
            else load();
          }}
        />
      )}
      <ul className="cn-list">
        {items.length === 0 && <li className="muted small cn-empty">Chưa có connector</li>}
        {items.map((c) => {
          const oauth = c.auth_type === "oauth" && !c.readonly;
          const linked = c.status !== "not_connected";
          const canTools = !oauth || linked;
          return (
            <li key={c.connector_id} className={c.enabled ? "" : "cn-disabled"}>
              <div className="cn-item">
                <span className="cn-icon">
                  <Icon name="plug" />
                </span>
                <div className="cn-main">
                  <div className="cn-name">{title(c)}</div>
                  <div className="cn-sub" title={c.description || undefined}>
                    {host(c.url)}
                    {c.description && <> · {c.description}</>}
                  </div>
                </div>
                <div className="cn-side">
                  {checking.includes(c.connector_id) ? (
                    <span className="cn-badge">Đang kiểm tra…</span>
                  ) : (
                    <span className={`cn-badge cn-${c.enabled ? c.status : "off"}`}>
                      {c.enabled ? STATUS_LABEL[c.status] ?? c.status : "Đã tắt"}
                    </span>
                  )}
                  {oauth && c.enabled && c.status !== "connected" && (
                    <button onClick={() => doConnect(c)}>{linked ? "Kết nối lại" : "Kết nối"}</button>
                  )}
                  <div className="cn-menu-wrap">
                    <button
                      className="cn-more"
                      aria-label="Tùy chọn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenu(menu === c.connector_id ? null : c.connector_id);
                      }}
                    >
                      ⋯
                    </button>
                    {menu === c.connector_id && (
                      <div className="cn-menu" role="menu">
                        {canTools && (
                          <button onClick={() => setOpenTools(openTools === c.connector_id ? null : c.connector_id)}>
                            {openTools === c.connector_id ? "Ẩn quyền tool" : "Quyền tool"}
                          </button>
                        )}
                        {!c.readonly && canTools && (
                          <button onClick={() => act(async () => (await testConnector(c.connector_id, userId)).message)}>
                            Kiểm tra kết nối
                          </button>
                        )}
                        {!c.readonly && <button onClick={() => setForm({ editing: c })}>Sửa</button>}
                        {!c.readonly && (
                          <button
                            onClick={() => act(async () => void (await updateConnector(c.connector_id, { enabled: !c.enabled }, userId)))}
                          >
                            {c.enabled ? "Tắt" : "Bật"}
                          </button>
                        )}
                        {oauth && linked && (
                          <button onClick={() => act(async () => (await disconnect(c.connector_id, userId)).message)}>
                            Ngắt kết nối
                          </button>
                        )}
                        {!c.readonly && (
                          <button
                            className="cn-danger"
                            onClick={() => {
                              if (window.confirm(`Xóa connector ${title(c)}?`))
                                act(() => deleteConnector(c.connector_id, userId));
                            }}
                          >
                            Xóa
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </div>
              {openTools === c.connector_id && (
                <div className="cn-tools-wrap">
                  <ToolList connector={c} userId={userId} onFail={() => recheck(c)} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
