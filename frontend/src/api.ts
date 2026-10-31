import type {
  Citation,
  ConfirmRequest,
  ConversationDetail,
  ConversationSummary,
  DoneEvent,
  ResultFile,
} from "./types";

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const listConversations = (userId: string) =>
  fetch(`/conversations?user_id=${encodeURIComponent(userId)}`).then((r) => json<ConversationSummary[]>(r));

export const getConversation = (id: string) =>
  fetch(`/conversations/${encodeURIComponent(id)}`).then((r) => json<ConversationDetail>(r));

export function uploadFile(conversationId: string, userId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const url = `/conversations/${encodeURIComponent(conversationId)}/files?user_id=${encodeURIComponent(userId)}`;
  return fetch(url, { method: "POST", body: form }).then((r) => json<{ name: string; size: number }>(r));
}

export interface ChatHandlers {
  onStatus?: (step: string, data: Record<string, unknown>) => void;
  onToken?: (text: string) => void;
  onCitations?: (c: Citation[]) => void;
  onToolStart?: (d: { tool: string; args_preview?: string }) => void;
  onToolEnd?: (d: { tool: string; ok: boolean; summary: string }) => void;
  onFile?: (f: ResultFile) => void;
  onConfirm?: (c: ConfirmRequest) => void;
  onDone?: (d: DoneEvent) => void;
}

/** POST /chat rồi đọc SSE. */
export function streamChat(
  body: { user_id: string; question: string; conversation_id?: string | null; attachments?: string[] },
  h: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return postSse("/chat", body, h, signal);
}

/** POST /chat/confirm: đồng ý / từ chối tool write, đọc tiếp SSE của lượt. */
export function confirmChat(
  body: { conversation_id: string; user_id: string; confirm_id: string; approve: boolean },
  h: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return postSse("/chat/confirm", body, h, signal);
}

async function postSse(url: string, body: unknown, h: ChatHandlers, signal?: AbortSignal): Promise<void> {
  const r = await fetch(url, {
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
    case "tool_start":
      h.onToolStart?.(JSON.parse(raw));
      break;
    case "tool_end":
      h.onToolEnd?.(JSON.parse(raw));
      break;
    case "file":
      h.onFile?.(JSON.parse(raw));
      break;
    case "confirm_required":
      h.onConfirm?.(JSON.parse(raw));
      break;
    case "done":
      h.onDone?.(JSON.parse(raw));
      break;
    case "error":
      throw new Error(JSON.parse(raw).error);
  }
}
