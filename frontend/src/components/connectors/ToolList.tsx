import { useEffect, useState } from "react";
import { listTools, setToolAccess, type Access, type Connector, type McpTool } from "../../mcpApi";
import Icon from "../Icon";

const ACCESS_LABEL: Record<Access, string> = { read: "Luôn cho phép", write: "Cần xác nhận", disabled: "Tắt" };
const ACCESSES = Object.keys(ACCESS_LABEL) as Access[];

// Nhóm theo mức mặc định server khai báo (annotations): chỉ đọc / ghi-xóa
const GROUPS = [
  { key: "read", label: "Tool chỉ đọc", match: (t: McpTool) => t.default_access === "read" },
  { key: "write", label: "Tool ghi/xóa", match: (t: McpTool) => t.default_access !== "read" },
];

/** Ghi đè = null khi chọn đúng mức mặc định của tool */
const overrideFor = (t: McpTool, a: Access): Access | null => (a === t.default_access ? null : a);

function AccessSelect({
  value,
  onChange,
  readonly,
}: {
  value: Access | "mixed";
  onChange: (a: Access) => void;
  readonly?: boolean;
}) {
  if (readonly) return <span className="cn-access">{value === "mixed" ? "Tùy chỉnh" : ACCESS_LABEL[value]}</span>;
  return (
    <select className="cn-access" value={value} onChange={(e) => onChange(e.target.value as Access)}>
      {value === "mixed" && (
        <option value="mixed" disabled>
          Tùy chỉnh
        </option>
      )}
      {ACCESSES.map((a) => (
        <option key={a} value={a}>
          {ACCESS_LABEL[a]}
        </option>
      ))}
    </select>
  );
}

interface Props {
  connector: Connector;
  userId: string;
  /** Không lấy được danh sách tool → cha kiểm tra lại kết nối để cập nhật trạng thái (vd hết hạn đăng nhập) */
  onFail?: () => void;
}

export default function ToolList({ connector, userId, onFail }: Props) {
  const [tools, setTools] = useState<McpTool[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    listTools(connector.connector_id, userId)
      .then(setTools)
      .catch((e) => {
        setError(String((e as Error).message ?? e));
        onFail?.();
      });
  };
  useEffect(load, [connector.connector_id, userId]);

  /** Đặt cùng 1 mức cho cả nhóm (hoặc 1 tool) */
  const apply = async (items: McpTool[], a: Access) => {
    try {
      await Promise.all(
        items
          .filter((t) => (t.override ?? null) !== overrideFor(t, a))
          .map((t) => setToolAccess(connector.connector_id, t.name, overrideFor(t, a))),
      );
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
    load();
  };

  if (error) return <div className="banner-error">{error}</div>;
  if (!tools) return <div className="muted small">Đang tải danh sách tool…</div>;
  if (!tools.length) return <div className="muted small">Không có tool.</div>;
  return (
    <div className="cn-perm">
      <div className="cn-label">Quyền dùng tool</div>
      <div className="cn-hint">Chọn khi nào agent được dùng các tool này. "Cần xác nhận": agent hỏi bạn trước khi chạy.</div>
      {GROUPS.map((g) => {
        const items = tools.filter(g.match);
        if (!items.length) return null;
        const first = items[0].access;
        const value = items.every((t) => t.access === first) ? first : "mixed";
        const isOpen = open === g.key;
        return (
          <div key={g.key} className="cn-group">
            <div className="cn-group-head">
              <button
                type="button"
                className={`cn-group-toggle${isOpen ? " open" : ""}`}
                onClick={() => setOpen(isOpen ? null : g.key)}
              >
                <Icon name="chevron-right" />
                {g.label}
                <span className="cn-count">{items.length}</span>
              </button>
              <AccessSelect value={value} onChange={(a) => apply(items, a)} readonly={connector.readonly} />
            </div>
            {isOpen && (
              <ul className="cn-group-tools">
                {items.map((t) => (
                  <li key={t.name}>
                    <div className="cn-tool-main">
                      <div className="cn-tool-name" title={t.full_name}>
                        {t.name}
                      </div>
                      {t.description && <div className="cn-tool-desc">{t.description}</div>}
                    </div>
                    <AccessSelect
                      value={t.access}
                      onChange={(a) => apply([t], a)}
                      readonly={connector.readonly || t.readonly}
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
