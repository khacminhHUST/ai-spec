import type { Citation, ConversationDetail, ConversationSummary, DoneEvent } from "./types";

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const listConversations = (userId: string) =>
  fetch(`/conversations?user_id=${encodeURIComponent(userId)}`).then((r) => json<ConversationSummary[]>(r));

export const getConversation = (id: string) =>
  fetch(`/conversations/${encodeURIComponent(id)}`).then((r) => json<ConversationDetail>(r));

export interface ChatHandlers {
  onStatus?: (step: string, data: Record<string, unknown>) => void;
  onToken?: (text: string) => void;
  onCitations?: (c: Citation[]) => void;
  onDone?: (d: DoneEvent) => void;
}

/** POST /chat rồi đọc SSE (status · token · citations · done · error). */
export async function streamChat(
  body: { user_id: string; question: string; conversation_id?: string | null },
  h: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`${r.status} ${await r.text()}`);

  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value.replace(/\r\n/g, "\n");
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      dispatch(buf.slice(0, i), h);
      buf = buf.slice(i + 2);
    }
  }
  if (buf.trim()) dispatch(buf, h);
}

function dispatch(block: string, h: ChatHandlers) {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  const raw = data.join("\n");
  switch (event) {
    case "token":
      h.onToken?.(raw);
      break;
    case "status": {
      const d = JSON.parse(raw);
      h.onStatus?.(d.step, d);
      break;
    }
    case "citations":
      h.onCitations?.(JSON.parse(raw));
      break;
    case "done":
      h.onDone?.(JSON.parse(raw));
      break;
    case "error":
      throw new Error(JSON.parse(raw).error);
  }
}
