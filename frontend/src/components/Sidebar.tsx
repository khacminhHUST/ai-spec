import { useEffect, useState } from "react";
import type { ConversationSummary } from "../types";

interface Props {
  userId: string;
  onUserChange: (u: string) => void;
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export default function Sidebar({ userId, onUserChange, conversations, activeId, onSelect, onNew }: Props) {
  const [draft, setDraft] = useState(userId);
  useEffect(() => setDraft(userId), [userId]);

  const commit = () => {
    const u = draft.trim();
    if (u && u !== userId) onUserChange(u);
    else setDraft(userId);
  };

  return (
    <aside className="sidebar">
      <button className="btn-new" onClick={onNew}>
        + Hội thoại mới
      </button>
      <nav className="conv-list">
        {conversations.length === 0 && <div className="muted small pad">Chưa có hội thoại</div>}
        {conversations.map((c) => (
          <button
            key={c.conversation_id}
            className={"conv-item" + (c.conversation_id === activeId ? " active" : "")}
            onClick={() => onSelect(c.conversation_id)}
            title={c.title ?? ""}
          >
            <span className="conv-title">{c.title || "(không tiêu đề)"}</span>
            <span className="conv-date">{new Date(c.updated_at).toLocaleString("vi-VN")}</span>
          </button>
        ))}
      </nav>
      <form
        className="user-box"
        onSubmit={(e) => {
          e.preventDefault();
          commit();
        }}
      >
        <label className="small muted" htmlFor="uid">
          User ID
        </label>
        <input id="uid" value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} />
      </form>
    </aside>
  );
}
