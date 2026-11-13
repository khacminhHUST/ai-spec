import { useEffect, useState } from "react";
import { listTools, setToolAccess, type Access, type Connector, type McpTool } from "../../mcpApi";

const ACCESS_LABEL: Record<Access, string> = { read: "Đọc", write: "Ghi (cần xác nhận)", disabled: "Tắt" };

export default function ToolList({ connector, userId }: { connector: Connector; userId: string }) {
  const [tools, setTools] = useState<McpTool[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    listTools(connector.connector_id, userId)
      .then(setTools)
      .catch((e) => setError(String((e as Error).message ?? e)));
  };
  useEffect(load, [connector.connector_id, userId]);

  const change = async (t: McpTool, value: string) => {
    const access = value === "" ? null : (value as Access);
    try {
      await setToolAccess(connector.connector_id, t.name, access);
      load();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  if (error) return <div className="banner-error">{error}</div>;
  if (!tools) return <div className="muted small">Đang tải danh sách tool…</div>;
  if (!tools.length) return <div className="muted small">Không có tool.</div>;
  return (
    <div className="table-wrap">
    <table className="cn-tools stack">
      <thead>
        <tr>
          <th>Tool</th>
          <th>Mô tả</th>
          <th>Quyền</th>
        </tr>
      </thead>
      <tbody>
        {tools.map((t) => (
          <tr key={t.name}>
            <td data-label="Tool" className="stack-title">
              <code>{t.full_name}</code>
            </td>
            <td data-label="Mô tả" className="small">{t.description}</td>
            <td data-label="Quyền">
              {connector.readonly || t.readonly ? (
                ACCESS_LABEL[t.access] ?? t.access
              ) : (
                <select value={t.override ?? ""} onChange={(e) => change(t, e.target.value)}>
                  <option value="">Mặc định: {ACCESS_LABEL[t.default_access as Access] ?? t.default_access}</option>
                  {(Object.keys(ACCESS_LABEL) as Access[]).map((a) => (
                    <option key={a} value={a}>
                      {ACCESS_LABEL[a]}
                    </option>
                  ))}
                </select>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}
