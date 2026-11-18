import { useEffect, useState } from "react";
import type { ConversationSummary } from "../types";
import Icon from "./Icon";
import { followsHost, type ThemePref } from "../theme";
import { authMode } from "../deploy";

export type AppView = "chat" | "connectors" | "knowledge";

const NAV: { id: AppView; label: string; icon: string }[] = [
  { id: "chat", label: "Hỏi đáp", icon: "chat" },
  { id: "knowledge", label: "Kho tri thức", icon: "database" },
  { id: "connectors", label: "Kết nối", icon: "plug" },
];

interface Props {
  userId: string;
  onUserChange: (u: string) => void;
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  view: AppView;
  onNav: (v: AppView) => void;
  open: boolean;
  onClose: () => void;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
}

const THEMES: { id: ThemePref; label: string; icon: string }[] = [
  { id: "light", label: "Sáng", icon: "sun" },
  { id: "dark", label: "Tối", icon: "moon" },
  { id: "system", label: "Tự động", icon: "monitor" },
];

function shortDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString()
    ? d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" });
}

export default function Sidebar(p: Props) {
  const { userId, onUserChange, conversations, activeId, onSelect, onNew, view, onNav, open, onClose, theme, onTheme } = p;
  const [draft, setDraft] = useState(userId);
  useEffect(() => {
    setDraft(userId);
  }, [userId]);

  const commit = () => {
    const u = draft.trim();
    if (u && u !== userId) onUserChange(u);
    else setDraft(userId);
  };

  return (
    <>
      <div className={"scrim" + (open ? " show" : "")} onClick={onClose} aria-hidden />
      <aside className={"sidebar" + (open ? " open" : "")} aria-label="Điều hướng">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="zap" size={18} />
          </span>
          <span className="brand-text">
            <strong>MEP Assistant</strong>
            <span>Tài liệu dự án cơ điện</span>
          </span>
          <button className="icon-btn sidebar-close" onClick={onClose} aria-label="Đóng menu">
            <Icon name="x" />
          </button>
        </div>

        <button className="btn-new" onClick={onNew}>
          <Icon name="plus" />
          Hội thoại mới
        </button>

        <nav className="nav">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={"nav-item" + (view === n.id ? " active" : "")}
              aria-current={view === n.id ? "page" : undefined}
              onClick={() => onNav(n.id)}
            >
              <Icon name={n.icon} />
              {n.label}
            </button>
          ))}
        </nav>

        <div className="section-label">Gần đây</div>
        <nav className="conv-list" aria-label="Hội thoại gần đây">
          {conversations.length === 0 && <div className="conv-empty">Chưa có hội thoại</div>}
          {conversations.map((c) => (
            <button
              key={c.conversation_id}
              className={"conv-item" + (c.conversation_id === activeId && view === "chat" ? " active" : "")}
              onClick={() => onSelect(c.conversation_id)}
              title={c.title ?? ""}
            >
              <span className="conv-title">{c.title || "(không tiêu đề)"}</span>
              <span className="conv-date">{shortDate(c.updated_at)}</span>
            </button>
          ))}
        </nav>

        {authMode ? (
          // Nhúng trong web Tri thức: tài khoản lấy từ token đăng nhập, không sửa tay
          <div className="user-box">
            <span className="avatar" aria-hidden>
              {(userId[0] ?? "?").toUpperCase()}
            </span>
            <div className="user-field">
              <span>Tài khoản Tri thức</span>
              <strong title={userId}>{userId}</strong>
            </div>
          </div>
        ) : (
          <form
            className="user-box"
            onSubmit={(e) => {
              e.preventDefault();
              commit();
            }}
          >
            <span className="avatar" aria-hidden>
              {(userId[0] ?? "?").toUpperCase()}
            </span>
            <label className="user-field">
              <span>User ID</span>
              <input value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} />
            </label>
          </form>
        )}

        {!followsHost && (
          <div className="theme-switch" role="group" aria-label="Giao diện">
            {THEMES.map((t) => (
              <button key={t.id} aria-pressed={theme === t.id} onClick={() => onTheme(t.id)} title={`Giao diện ${t.label}`}>
                <Icon name={t.icon} size={14} />
                {t.label}
              </button>
            ))}
          </div>
        )}
      </aside>
    </>
  );
}
