export type Status = "OK" | "NOT_FOUND" | "ASK_CLARIFY" | "WAITING_CONFIRM" | string;

export interface Citation {
  n: number;
  doc_id: string;
  doc_title?: string | null;
  revision?: string | null;
  section_key: string;
  number?: string | null;
  title?: string | null;
  page?: number | null;
  page_end?: number | null;
  lang: string[];
}

export interface ResultFile {
  name: string;
  size: number;
  url: string;
}

/** chỉ phía client: tool agent đang/đã chạy trong lượt */
export interface ToolRun {
  tool: string;
  args_preview?: string;
  done: boolean;
  ok?: boolean;
  summary?: string;
}

export interface ConfirmRequest {
  confirm_id: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  status?: Status | null;
  citations?: Citation[];
  files?: ResultFile[];
  attachments?: string[];
  /** chỉ phía client: bước pipeline đang chạy khi stream */
  step?: string | null;
  tools?: ToolRun[];
  confirms?: ConfirmRequest[];
  error?: string | null;
}

export interface ConversationSummary {
  conversation_id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[];
  active_projects: string[];
}

export interface DoneEvent {
  conversation_id: string;
  status: Status | null;
  active_projects: string[];
}
