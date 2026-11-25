import { useCallback, useEffect, useRef, useState } from "react";
import { confirmChat, getConversation, listConversations, streamChat, uploadFile, type ChatHandlers } from "./api";
import ChatView from "./components/ChatView";
import Composer from "./components/Composer";
import KnowledgePage from "./components/knowledge/KnowledgePage";
import Icon from "./components/Icon";
import Sidebar, { type AppView } from "./components/Sidebar";
import ConnectorsView from "./components/connectors/ConnectorsView";
import { followsHost, isDarkNow, useTheme } from "./theme";
import type { ConversationSummary, Message } from "./types";

const USER_KEY = "mep.user_id";
const DEFAULT_USER = "test_engineer";

function newId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
}

function loadUser(): string {
  try {
    return localStorage.getItem(USER_KEY) || DEFAULT_USER;
  } catch {
    return DEFAULT_USER;
  }
}

export default function App() {
  const [userId, setUserId] = useState(loadUser);
  const [view, setView] = useState<AppView>(() => {
    const v = new URLSearchParams(window.location.search).get("view");
    return v === "connectors" || v === "knowledge" ? v : "chat";
  });
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  // id cấp trước cho hội thoại mới có file đính kèm (phải upload trước khi gửi câu hỏi đầu tiên)
  const draftIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    listConversations(userId)
      .then(setConversations)
      .catch((e) => setError(`Không kết nối được backend: ${e}`));
  }, [userId]);

  useEffect(() => {
    try {
      localStorage.setItem(USER_KEY, userId);
    } catch {
      /* bỏ qua */
    }
    refresh();
  }, [userId, refresh]);

  const reset = () => {
    abortRef.current?.abort();
    setBusy(false);
    setError(null);
    setAttachments([]);
    draftIdRef.current = null;
  };

  const nav = (v: AppView) => {
    setView(v);
    setMenuOpen(false);
  };

  const openConversation = async (id: string) => {
    reset();
    nav("chat");
    setActiveId(id);
    try {
      const c = await getConversation(id);
      setMessages(c.messages);
      setProjects(c.active_projects);
    } catch (e) {
      setError(String(e));
    }
  };

  const newConversation = () => {
    reset();
    nav("chat");
    setActiveId(null);
    setMessages([]);
    setProjects([]);
  };

  const patchLast = (f: (m: Message) => Message) =>
    setMessages((ms) => [...ms.slice(0, -1), f(ms[ms.length - 1])]);

  // Đo thời gian lượt hiện tại (tính từ lúc gửi, phía client)
  const t0Ref = useRef(0);
  const elapsed = () => Math.round(performance.now() - t0Ref.current);

  const handlers: ChatHandlers = {
    onStatus: (step) => patchLast((m) => ({ ...m, step })),
    onToken: (t) =>
      patchLast((m) => ({
        ...m,
        content: m.content + t,
        timing: m.timing?.first != null ? m.timing : { ...m.timing, first: elapsed() },
      })),
    onTokenReset: () => patchLast((m) => ({ ...m, content: "" })),
    onCitations: (citations) => patchLast((m) => ({ ...m, citations })),
    onToolStart: (d) =>
      patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), { tool: d.tool, args_preview: d.args_preview, done: false }] })),
    onToolEnd: (d) =>
      patchLast((m) => {
        const tools = [...(m.tools ?? [])];
        const i = tools.findIndex((t) => t.tool === d.tool && !t.done);
        const done = { tool: d.tool, done: true, ok: d.ok, summary: d.summary };
        if (i >= 0) tools[i] = done;
        else tools.push(done);
        return { ...m, tools };
      }),
    onFile: (f) => patchLast((m) => ({ ...m, files: [...(m.files ?? []), f] })),
    onConfirm: (c) =>
      patchLast((m) =>
        m.confirms?.some((x) => x.confirm_id === c.confirm_id) ? m : { ...m, confirms: [...(m.confirms ?? []), c] },
      ),
    onDone: (d) => {
      patchLast((m) => ({ ...m, status: d.status, step: null, timing: { ...m.timing, total: elapsed() } }));
      setActiveId(d.conversation_id);
      setProjects(d.active_projects);
    },
  };

  const run = async (call: (h: ChatHandlers, signal: AbortSignal) => Promise<void>) => {
    setError(null);
    setBusy(true);
    t0Ref.current = performance.now();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await call(handlers, ctrl.signal);
      refresh();
    } catch (e) {
      if (!ctrl.signal.aborted) patchLast((m) => ({ ...m, step: null, error: String(e) }));
    } finally {
      if (!ctrl.signal.aborted) setBusy(false);
    }
  };

  const send = (question: string) => {
    const files = attachments;
    const conversation_id = activeId ?? draftIdRef.current;
    setAttachments([]);
    draftIdRef.current = null;
    setMessages((ms) => [
      ...ms.map((m) => (m.confirms ? { ...m, confirms: undefined } : m)), // lượt mới bỏ xác nhận đang chờ
      { role: "user", content: question, attachments: files },
      { role: "assistant", content: "", step: "gate", citations: [] },
    ]);
    return run((h, signal) =>
      streamChat({ user_id: userId, question, conversation_id, attachments: files }, h, signal),
    );
  };

  const confirm = (confirmId: string, approve: boolean) => {
    if (!activeId) return;
    patchLast((m) => ({
      ...m,
      step: "agent",
      status: null,
      timing: undefined, // đo lại cho phần chạy sau xác nhận
      confirms: m.confirms?.filter((c) => c.confirm_id !== confirmId),
    }));
    return run((h, signal) =>
      confirmChat({ conversation_id: activeId, user_id: userId, confirm_id: confirmId, approve }, h, signal),
    );
  };

  const attach = async (file: File) => {
    setError(null);
    setUploading(true);
    try {
      const cid = activeId ?? (draftIdRef.current ??= newId());
      const { name } = await uploadFile(cid, userId, file);
      setAttachments((a) => (a.includes(name) ? a : [...a, name]));
    } catch (e) {
      setError(`Không tải được file: ${e}`);
    } finally {
      setUploading(false);
    }
  };

  const title = view === "knowledge" ? "Kho tri thức" : view === "connectors" ? "Kết nối" : "Hỏi đáp";

  return (
    <div className="layout">
      <Sidebar
        userId={userId}
        onUserChange={(u) => {
          newConversation();
          setUserId(u);
        }}
        conversations={conversations}
        activeId={activeId}
        onSelect={openConversation}
        onNew={newConversation}
        view={view}
        onNav={nav}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        theme={theme}
        onTheme={setTheme}
      />
      <div className="shell">
        <header className="mobilebar">
          <button className="icon-btn" onClick={() => setMenuOpen(true)} aria-label="Mở menu">
            <Icon name="menu" />
          </button>
          <span className="mobilebar-title">{title}</span>
          {!followsHost && (
            <button
              className="icon-btn"
              onClick={() => setTheme(isDarkNow(theme) ? "light" : "dark")}
              aria-label={isDarkNow(theme) ? "Chuyển giao diện sáng" : "Chuyển giao diện tối"}
              title={isDarkNow(theme) ? "Giao diện sáng" : "Giao diện tối"}
            >
              <Icon name={isDarkNow(theme) ? "sun" : "moon"} />
            </button>
          )}
          {view === "chat" && (
            <button className="icon-btn" onClick={newConversation} aria-label="Hội thoại mới">
              <Icon name="plus" />
            </button>
          )}
        </header>
        {view === "knowledge" ? (
          <KnowledgePage userId={userId} onClose={() => nav("chat")} />
        ) : (
          <main className="main">
            <header className="topbar">
              <span className="topbar-title">{view === "connectors" ? "Kết nối nguồn ngoài" : "Hỏi đáp dự án"}</span>
              {view === "chat" && projects.length > 0 && (
                <span className="projects">
                  <span className="projects-label">Dự án</span>
                  {projects.map((p) => (
                    <span key={p} className="chip">
                      {p}
                    </span>
                  ))}
                </span>
              )}
            </header>
            {error && (
              <div className="banner-error" role="alert">
                <Icon name="alert" size={16} />
                {error}
              </div>
            )}
            {view === "connectors" && <ConnectorsView userId={userId} />}
            {view === "chat" && (
              <>
                <ChatView messages={messages} onExample={send} onConfirm={confirm} />
                <Composer
                  disabled={busy}
                  attachments={attachments}
                  uploading={uploading}
                  onSend={send}
                  onAttach={attach}
                  onRemoveAttachment={(name) => setAttachments((a) => a.filter((x) => x !== name))}
                />
              </>
            )}
          </main>
        )}
      </div>
    </div>
  );
}
