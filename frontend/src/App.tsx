import { useCallback, useEffect, useRef, useState } from "react";
import { getConversation, listConversations, streamChat } from "./api";
import ChatView from "./components/ChatView";
import Composer from "./components/Composer";
import Sidebar from "./components/Sidebar";
import type { ConversationSummary, Message } from "./types";

const USER_KEY = "mep.user_id";
const DEFAULT_USER = "test_engineer";

function loadUser(): string {
  try {
    return localStorage.getItem(USER_KEY) || DEFAULT_USER;
  } catch {
    return DEFAULT_USER;
  }
}

export default function App() {
  const [userId, setUserId] = useState(loadUser);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  const send = async (question: string) => {
    setError(null);
    setBusy(true);
    setMessages((ms) => [
      ...ms,
      { role: "user", content: question },
      { role: "assistant", content: "", step: "load", citations: [] },
    ]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await streamChat(
        { user_id: userId, question, conversation_id: activeId },
        {
          onStatus: (step) => patchLast((m) => ({ ...m, step })),
          onToken: (t) => patchLast((m) => ({ ...m, content: m.content + t })),
          onCitations: (citations) => patchLast((m) => ({ ...m, citations })),
          onDone: (d) => {
            patchLast((m) => ({ ...m, status: d.status, step: null }));
            setActiveId(d.conversation_id);
            setProjects(d.active_projects);
          },
        },
        ctrl.signal,
      );
      refresh();
    } catch (e) {
      if (!ctrl.signal.aborted) patchLast((m) => ({ ...m, step: null, error: String(e) }));
    } finally {
      if (!ctrl.signal.aborted) setBusy(false);
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
      <main className="main">
        <header className="topbar">
          <span className="topbar-title">MEP Project Assistant</span>
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
        <ChatView messages={messages} onExample={send} />
        <Composer disabled={busy} onSend={send} />
      </main>
    </div>
  );
}
