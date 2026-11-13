import { useEffect, useRef } from "react";
import type { Message } from "../types";
import Icon from "./Icon";
import MessageBubble from "./MessageBubble";

const EXAMPLES: { q: string; icon: string; tag: string }[] = [
  { q: "Bán kính uốn cong tối thiểu của cáp điện tòa S5 là bao nhiêu?", icon: "zap", tag: "Spec Điện" },
  { q: "Lưu lượng gió tươi cho phòng khách sạn theo thuyết minh thiết kế?", icon: "book", tag: "Thuyết minh" },
  { q: "Thời gian bảo hành hệ thống điện là bao lâu?", icon: "shield", tag: "Bảo hành" },
  { q: "Xuất Excel bảng gió tươi tòa S5 (Thuyết minh Bảng 4.3), có cột Nguồn", icon: "table", tag: "Xuất file" },
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
        <div className="hero">
          <span className="hero-mark">
            <Icon name="spark" size={26} />
          </span>
          <h1>Hỏi đáp tài liệu dự án cơ điện</h1>
          <p>Thuyết minh, Spec, Chỉ dẫn kỹ thuật, tiêu chuẩn vận hành. Câu trả lời luôn kèm trích dẫn nguồn.</p>
        </div>
        <div className="examples">
          {EXAMPLES.map((e) => (
            <button key={e.q} className="example" onClick={() => onExample(e.q)}>
              <span className="example-icon">
                <Icon name={e.icon} />
              </span>
              <span className="example-body">
                <span className="example-tag">{e.tag}</span>
                <span>{e.q}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    );

  return (
    <div className="chat" role="log" aria-live="polite">
      <div className="chat-inner">
        {messages.map((m, i) => (
          <MessageBubble key={i} m={m} onConfirm={onConfirm} />
        ))}
        <div ref={end} />
      </div>
    </div>
  );
}
