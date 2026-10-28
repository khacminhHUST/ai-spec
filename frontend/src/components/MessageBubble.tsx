import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import Citations from "./Citations";

const STEP_LABEL: Record<string, string> = {
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
};

export default function MessageBubble({ m }: { m: Message }) {
  if (m.role === "user") return <div className="msg user">{m.content}</div>;
  const badge = m.status ? STATUS_LABEL[m.status] : undefined;
  const step = m.step ? (STEP_LABEL[m.step] ?? m.step) : null;
  return (
    <div className="msg assistant">
      {badge && <span className={"badge " + m.status}>{badge}</span>}
      {m.content && (
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>
      )}
      {step && !(m.content && m.step === "generate") && <div className="step">{step}</div>}
      {m.error && <div className="msg-error">Lỗi: {m.error}</div>}
      {m.citations && m.citations.length > 0 && <Citations items={m.citations} />}
    </div>
  );
}
