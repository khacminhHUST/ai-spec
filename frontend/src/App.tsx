import { useCallback, useEffect, useRef, useState } from "react";
import { confirmChat, getConversation, listConversations, streamChat, uploadFile, type ChatHandlers } from "./api";
import ChatView from "./components/ChatView";
import Composer from "./components/Composer";
import KnowledgePage from "./components/knowledge/KnowledgePage";
import Sidebar from "./components/Sidebar";
import ConnectorsView from "./components/connectors/ConnectorsView";
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
  const [view, setView] = useState(() => (new URLSearchParams(window.location.search).get("view") === "connectors" ? "connectors" : "chat"));
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [tab, setTab] = useState<"chat" | "knowledge">("chat");
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

  const openConversation = async (id: string) => {
    reset();
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
    setActiveId(null);
    setMessages([]);
    setProjects([]);
  };

  const patchLast = (f: (m: Message) => Message) =>
    setMessages((ms) => [...ms.slice(0, -1), f(ms[ms.length - 1])]);

  const handlers: ChatHandlers = {
    onStatus: (step) => patchLast((m) => ({ ...m, step })),
    onToken: (t) => patchLast((m) => ({ ...m, content: m.content + t })),
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
      patchLast((m) => ({ ...m, status: d.status, step: null }));
      setActiveId(d.conversation_id);
      setProjects(d.active_projects);
    },
  };

  const run = async (call: (h: ChatHandlers, signal: AbortSignal) => Promise<void>) => {
    setError(null);
    setBusy(true);
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
      />
      {tab === "knowledge" ? (
        <KnowledgePage userId={userId} onClose={() => setTab("chat")} />
      ) : (
      <main className="main">
        <header className="topbar">
          <span className="topbar-title">MEP Project Assistant</span>
          <nav className="app-tabs">
            <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}>Hội thoại</button>
            <button className={view === "connectors" ? "active" : ""} onClick={() => setView("connectors")}>Kết nối</button>
          </nav>
          <button className="kn-link topbar-link" onClick={() => setTab("knowledge")}>
            Kho tri thức
          </button>
          {projects.length > 0 && (
            <span className="projects">
              Dự án:
              {projects.map((p) => (
                <span key={p} className="chip">
                  {p}
                </span>
              ))}
            </span>
          )}
        </header>
        {error && <div className="banner-error">{error}</div>}
        {view === "connectors" && <ConnectorsView userId={userId} />}
        {view === "chat" && (<>
        <ChatView messages={messages} onExample={send} onConfirm={confirm} />
        <Composer
          disabled={busy}
          attachments={attachments}
          uploading={uploading}
          onSend={send}
          onAttach={attach}
          onRemoveAttachment={(name) => setAttachments((a) => a.filter((x) => x !== name))}
        />
        </>)}
      </main>
      )}
    </div>
  );
}
