import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState } from "react";
import type { Message, ToolRun } from "../types";
import Citations from "./Citations";
import Icon, { Spinner } from "./Icon";

const STEP_LABEL: Record<string, string> = {
  agent: "Đang xử lý yêu cầu…",
  load: "Đang tải hội thoại…",
  router: "Đang phân tích câu hỏi…",
  resolve: "Đang xác định dự án…",
  rerank: "Đang tìm & xếp hạng tài liệu…",
  context: "Đang dựng ngữ cảnh…",
  grade: "Đang kiểm tra đủ thông tin…",
  targeted_fetch: "Đang tra thêm mục được tham chiếu…",
  generate: "Đang soạn câu trả lời…",
  ask_clarify: "Cần làm rõ…",
  save: "Đang lưu…",
};

const STATUS_LABEL: Record<string, string> = {
  NOT_FOUND: "Không tìm thấy trong tài liệu",
  ASK_CLARIFY: "Cần làm rõ",
  WAITING_CONFIRM: "Chờ xác nhận",
};

const TOOL_LABEL: Record<string, string> = {
  search_project_docs: "Tra cứu tài liệu",
  run_code: "Chạy xử lý",
  read_file: "Đọc file",
  write_file: "Ghi file",
  list_files: "Xem danh sách file",
  load_skill: "Nạp hướng dẫn",
  open_source_tools: "Mở nguồn",
};

function formatSize(n: number): string {
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Các bước tool: đang chạy thì mở; xong thì thu gọn thành 1 dòng, bấm để xổ ra / ẩn đi */
function ToolSteps({ tools, finished }: { tools: ToolRun[]; finished: boolean }) {
  const [open, setOpen] = useState(false);
  const list = (
    <ul className="tools">
      {tools.map((t, i) => (
        <li key={i} className={t.done ? (t.ok ? "ok" : "fail") : "running"}>
          <span className="tool-mark">
            {t.done ? <Icon name={t.ok ? "check" : "x"} size={14} label={t.ok ? "Xong" : "Lỗi"} /> : <Spinner size={12} />}
          </span>
          {TOOL_LABEL[t.tool] ?? t.tool}
          {t.summary && <span className="tool-summary"> – {t.summary}</span>}
        </li>
      ))}
    </ul>
  );
  if (!finished) return list;
  return (
    <div className="tools-done">
      <button type="button" className={`tools-toggle${open ? " open" : ""}`} onClick={() => setOpen(!open)}>
        <Icon name="chevron-right" size={14} />
        Đã dùng {tools.length} công cụ
      </button>
      {open && list}
    </div>
  );
}

interface Props {
  m: Message;
  onConfirm?: (confirmId: string, approve: boolean) => void;
}

export default function MessageBubble({ m, onConfirm }: Props) {
  if (m.role === "user")
    return (
      <div className="msg user">
        {m.content}
        {m.attachments && m.attachments.length > 0 && (
          <div className="attachments">
            {m.attachments.map((a) => (
              <span key={a} className="chip">
                {a}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  const badge = m.status ? STATUS_LABEL[m.status] : undefined;
  const step = m.step ? (STEP_LABEL[m.step] ?? m.step) : null;
  // Có câu trả lời cuối → thu gọn danh sách bước tool (giữ mở khi chờ xác nhận hoặc lỗi)
  const finished = !m.step && !!m.content && m.status !== "WAITING_CONFIRM" && !m.error;
  return (
    <div className="msg assistant">
      <span className="msg-avatar" aria-hidden>
        <Icon name="spark" size={16} />
      </span>
      <div className="msg-body">
      {badge && <span className={"badge " + m.status}>{badge}</span>}
      {m.tools && m.tools.length > 0 && <ToolSteps tools={m.tools} finished={finished} />}
      {m.content && (
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>
      )}
      {step && !(m.content && m.step === "generate") && (
        <div className="step">
          <Spinner size={14} /> {step}
        </div>
      )}
      {m.confirms?.map((c) => (
        <div key={c.confirm_id} className="confirm">
          <div className="confirm-title">
            <Icon name="alert" size={16} />
            Cho phép thực hiện <code>{c.tool}</code> trên hệ thống ngoài?
          </div>
          <pre>{JSON.stringify(c.args, null, 2)}</pre>
          <div className="confirm-actions">
            <button onClick={() => onConfirm?.(c.confirm_id, true)}>Đồng ý</button>
            <button className="secondary" onClick={() => onConfirm?.(c.confirm_id, false)}>
              Từ chối
            </button>
          </div>
        </div>
      ))}
      {m.files && m.files.length > 0 && (
        <div className="files">
          {m.files.map((f) => (
            <a key={f.url} className="file" href={f.url} download={f.name}>
              <span className="file-icon">
                <Icon name="file" />
              </span>
              <span className="file-meta">
                <span className="file-name">{f.name}</span>
                <span className="file-size">{formatSize(f.size)}</span>
              </span>
              <Icon name="download" className="file-dl" />
            </a>
          ))}
        </div>
      )}
      {m.error && <div className="msg-error">Lỗi: {m.error}</div>}
      {m.citations && m.citations.length > 0 && <Citations items={m.citations} />}
      </div>
    </div>
  );
}
