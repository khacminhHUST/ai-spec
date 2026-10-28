export type Status = "OK" | "NOT_FOUND" | "ASK_CLARIFY" | string;

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

export interface Message {
  role: "user" | "assistant";
  content: string;
  status?: Status | null;
  citations?: Citation[];
  /** chỉ phía client: bước pipeline đang chạy khi stream */
  step?: string | null;
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
