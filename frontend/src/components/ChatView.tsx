import { useEffect, useRef } from "react";
import type { Message } from "../types";
import MessageBubble from "./MessageBubble";

const EXAMPLES = [
  "Bán kính uốn cong tối thiểu của cáp điện tòa S5 là bao nhiêu?",
  "Lưu lượng gió tươi cho phòng khách sạn theo thuyết minh thiết kế?",
  "Thời gian bảo hành hệ thống điện là bao lâu?",
  "Xuất Excel bảng gió tươi tòa S5 (Thuyết minh Bảng 4.3), có cột Nguồn",
];

interface Props {
  messages: Message[];
  onExample: (q: string) => void;
  onConfirm: (confirmId: string, approve: boolean) => void;
}

export default function ChatView({ messages, onExample, onConfirm }: Props) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  if (messages.length === 0)
    return (
      <div className="chat empty">
        <h2>Hỏi đáp tài liệu dự án cơ điện</h2>
        <p className="muted">Thuyết minh, Spec, Chỉ dẫn kỹ thuật, tiêu chuẩn vận hành – trả lời kèm trích dẫn nguồn.</p>
        <div className="examples">
          {EXAMPLES.map((q) => (
            <button key={q} onClick={() => onExample(q)}>
              {q}
            </button>
          ))}
        </div>
      </div>
    );

  return (
    <div className="chat">
      {messages.map((m, i) => (
        <MessageBubble key={i} m={m} onConfirm={onConfirm} />
      ))}
      <div ref={end} />
    </div>
  );
}
