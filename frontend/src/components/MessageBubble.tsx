import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useRef, useState } from "react";
import type { ConfirmRequest, Message, ToolRun } from "../types";
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

function formatSec(ms: number): string {
  return `${(ms / 1000).toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} giây`;
}

/** Các bước tool: đang chạy thì mở; xong thì thu gọn thành 1 dòng, bấm để xổ ra / ẩn đi */
function ToolSteps({ tools, finished, waiting }: { tools: ToolRun[]; finished: boolean; waiting?: boolean }) {
  const [open, setOpen] = useState(false);
  const list = (
    <ul className="tools">
      {tools.map((t, i) => (
        <li key={i} className={t.done ? (t.ok ? "ok" : "fail") : waiting ? "waiting" : "running"}>
          <span className="tool-mark">
            {t.done ? (
              <Icon name={t.ok ? "check" : "x"} size={14} label={t.ok ? "Xong" : "Lỗi"} />
            ) : waiting ? (
              <Icon name="clock" size={14} label="Chờ cho phép" />
            ) : (
              <Spinner size={12} />
            )}
          </span>
          {TOOL_LABEL[t.tool] ?? t.tool}
          {!t.done && waiting && <span className="tool-summary"> – chờ bạn cho phép</span>}
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

/** Chép tin nhắn: có htmlRef → kèm bản HTML (dán vào Word/Excel giữ bảng, in đậm) + bản markdown thô */
function CopyButton({ text, htmlRef, title = "Sao chép câu trả lời" }: {
  text: string;
  htmlRef?: React.RefObject<HTMLDivElement>;
  title?: string;
}) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    const html = htmlRef?.current?.innerHTML;
    try {
      if (html && typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([text], { type: "text/plain" }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(text);
      }
    } catch {
      // iframe thiếu quyền clipboard / trình duyệt cũ → cách cũ qua textarea tạm
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setDone(true);
    window.setTimeout(() => setDone(false), 1500);
  };
  return (
    <button type="button" className="msg-copy" onClick={copy} title={title}>
      <Icon name={done ? "check" : "copy"} size={14} />
      {done ? "Đã chép" : "Sao chép"}
    </button>
  );
}

/** Giá trị tham số hiển thị gọn: chuỗi / số / bool nguyên văn, còn lại JSON 1 dòng */
function argText(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v || "(trống)";
  if (typeof v === "number" || typeof v === "boolean") return v === true ? "có" : v === false ? "không" : String(v);
  return JSON.stringify(v);
}

/** Thẻ xin phép chạy thao tác trên hệ thống ngoài (tool MCP ghi / chưa rõ đọc-ghi) */
function ConfirmCard({ c, onConfirm }: { c: ConfirmRequest; onConfirm?: Props["onConfirm"] }) {
  const [sent, setSent] = useState<boolean | null>(null);
  const args = Object.entries(c.args ?? {});
  const decide = (approve: boolean) => {
    if (sent !== null) return;
    setSent(approve);
    onConfirm?.(c.confirm_id, approve);
  };
  return (
    <section className="confirm" aria-labelledby={`cf-${c.confirm_id}`}>
      <div className="confirm-head">
        <span className="confirm-icon" aria-hidden>
          <Icon name="shield" size={18} />
        </span>
        <div>
          <h4 id={`cf-${c.confirm_id}`} className="confirm-title">
            Cần bạn cho phép
          </h4>
          <p className="confirm-sub">Trợ lý muốn chạy một thao tác trên hệ thống ngoài. Kiểm tra lại trước khi đồng ý.</p>
        </div>
      </div>
      <dl className="confirm-args">
        <div>
          <dt>Thao tác</dt>
          <dd>
            <code>{c.tool}</code>
          </dd>
        </div>
        {args.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{argText(v)}</dd>
          </div>
        ))}
        {args.length === 0 && (
          <div>
            <dt>Tham số</dt>
            <dd className="muted">Không có</dd>
          </div>
        )}
      </dl>
      {args.some(([, v]) => v !== null && typeof v === "object") && (
        <details className="confirm-raw">
          <summary>Xem tham số đầy đủ</summary>
          <pre>{JSON.stringify(c.args, null, 2)}</pre>
        </details>
      )}
      <div className="confirm-actions">
        <button type="button" className="confirm-yes" disabled={sent !== null} onClick={() => decide(true)}>
          <Icon name="check" size={16} />
          {sent === true ? "Đang chạy…" : "Cho phép"}
        </button>
        <button type="button" className="confirm-no" disabled={sent !== null} onClick={() => decide(false)}>
          Từ chối
        </button>
      </div>
    </section>
  );
}

interface Props {
  m: Message;
  onConfirm?: (confirmId: string, approve: boolean) => void;
}

export default function MessageBubble({ m, onConfirm }: Props) {
  const mdRef = useRef<HTMLDivElement>(null);
  if (m.role === "user")
    return (
      <div className="msg-user-wrap">
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
        {m.content && (
          <div className="msg-user-actions">
            <CopyButton text={m.content} title="Sao chép câu hỏi" />
          </div>
        )}
      </div>
    );
  const waiting = m.status === "WAITING_CONFIRM";
  // Chờ xác nhận: thẻ xin phép đã nói rõ – không cần thêm nhãn
  const badge = m.status && !waiting ? STATUS_LABEL[m.status] : undefined;
  // Bước không có nhãn (tên kỹ thuật nội bộ) → không lộ ra UI, dùng câu chung
  const step = m.step ? (STEP_LABEL[m.step] ?? STEP_LABEL.agent) : null;
  // Có câu trả lời cuối → thu gọn danh sách bước tool (giữ mở khi chờ xác nhận hoặc lỗi)
  const finished = !m.step && !!m.content && m.status !== "WAITING_CONFIRM" && !m.error;
  return (
    <div className="msg assistant">
      <span className="msg-avatar" aria-hidden>
        <Icon name="spark" size={16} />
      </span>
      <div className="msg-body">
      {badge && <span className={"badge " + m.status}>{badge}</span>}
      {m.tools && m.tools.length > 0 && <ToolSteps tools={m.tools} finished={finished} waiting={waiting} />}
      {m.content && (
        <div className="md" ref={mdRef}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>
      )}
      {step && !(m.content && m.step === "generate") && (
        <div className="step">
          <Spinner size={14} /> {step}
        </div>
      )}
      {m.confirms?.map((c) => (
        <ConfirmCard key={c.confirm_id} c={c} onConfirm={onConfirm} />
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
      {((finished && m.content) || (m.timing?.total != null && !waiting)) && (
        <div className="msg-timing">
          {finished && m.content && <CopyButton text={m.content} htmlRef={mdRef} />}
          {m.timing?.first != null && <span>Chữ đầu: {formatSec(m.timing.first)}</span>}
          {m.timing?.total != null && !waiting && <span>Nhận xong: {formatSec(m.timing.total)}</span>}
        </div>
      )}
      </div>
    </div>
  );
}
