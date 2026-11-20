import { useState } from "react";
import { createConnector, discover, testConnector, updateConnector, type Connector } from "../../mcpApi";
import Modal from "../knowledge/Modal";

interface Props {
  userId: string;
  editing: Connector | null; // null = thêm mới
  existingNames: string[];
  /** popup: cửa sổ đã mở sẵn trong sự kiện bấm (tránh bị chặn) để đăng nhập OAuth ngay sau khi thêm */
  onSaved: (c: Connector | null, popup: Window | null) => void;
  onCancel: () => void;
}

/** Mã connector (tiền tố tên tool, a-z0-9_): lấy từ tên, không được thì từ tên miền, tránh trùng */
function makeCode(name: string, url: string, taken: string[]): string {
  const slug = (s: string) =>
    s
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/đ/gi, "d")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 28);
  let host = "";
  try {
    host = new URL(url).hostname.split(".")[0] ?? "";
  } catch {
    /* URL sai – backend báo lỗi */
  }
  let base = slug(name);
  if (base.length < 2) base = slug(host);
  if (base.length < 2) base = "mcp";
  let code = base;
  for (let i = 2; taken.includes(code); i++) code = `${base}_${i}`;
  return code;
}

export default function ConnectorForm({ userId, editing, existingNames, onSaved, onCancel }: Props) {
  const [name, setName] = useState(editing ? (editing.display_name && !editing.display_name.includes("@") ? editing.display_name : editing.name) : "");
  const [url, setUrl] = useState(editing?.url ?? "");
  const [description, setDescription] = useState(editing?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const u = url.trim();
    // Mở cửa sổ đăng nhập ngay trong sự kiện bấm; không cần OAuth thì đóng lại
    const popup = editing ? null : window.open("about:blank", "mep-oauth", "width=520,height=720");
    setBusy(true);
    try {
      if (editing) {
        const patch: Parameters<typeof updateConnector>[1] = {
          display_name: name.trim(),
          description: description.trim(),
        };
        if (u !== editing.url) {
          patch.url = u;
          patch.auth_type = (await discover(u)).auth_type;
        }
        await updateConnector(editing.connector_id, patch, userId);
        onSaved(null, null);
        return;
      }
      const d = await discover(u);
      const c = await createConnector(
        {
          name: makeCode(name, u, existingNames),
          display_name: name.trim() || undefined,
          description: description.trim(),
          url: u,
          transport: "streamable_http",
          auth_type: d.auth_type,
          enabled: true,
        },
        userId,
      );
      if (c.auth_type !== "oauth") {
        popup?.close();
        // Không cần đăng nhập: thử ngay để server trả danh sách tool → tự điền mô tả nếu đang trống
        await testConnector(c.connector_id, userId).catch(() => undefined);
        onSaved(c, null);
      } else onSaved(c, popup);
    } catch (err) {
      popup?.close();
      setError(String((err as Error).message ?? err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={editing ? "Sửa connector" : "Thêm connector"} onClose={onCancel}>
      <form className="cn-form" onSubmit={save}>
        <label className="cn-field">
          <span className="cn-label">Tên</span>
          <span className="cn-hint">Hiển thị trong danh sách connector, ví dụ ai-hr.</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            name="mcp-connector-label"
            autoComplete="off"
            autoFocus
            required
          />
        </label>
        <label className="cn-field">
          <span className="cn-label">URL MCP server</span>
          <span className="cn-hint">Địa chỉ HTTPS của MCP server, ví dụ https://kho.hawee.com.vn/mcp.</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            name="mcp-connector-url"
            type="url"
            autoComplete="off"
            required
          />
        </label>
        <label className="cn-field">
          <span className="cn-label">
            Mô tả <span className="cn-optional">(không bắt buộc)</span>
          </span>
          <span className="cn-hint">
            Nguồn này chứa dữ liệu gì, tối đa 300 ký tự. Để trống thì hệ thống tự viết sau khi kết nối.
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            name="mcp-connector-desc"
            rows={3}
            maxLength={300}
          />
          <span className="cn-count-text">{description.length}/300</span>
        </label>
        {error && <div className="banner-error">{error}</div>}
        <div className="cn-form-actions">
          <button type="button" onClick={onCancel}>
            Hủy
          </button>
          <button type="submit" className="cn-primary" disabled={busy || !name.trim() || !url.trim()}>
            {busy ? "Đang xử lý…" : editing ? "Lưu" : "Tiếp tục"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
