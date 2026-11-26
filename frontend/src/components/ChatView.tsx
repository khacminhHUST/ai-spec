import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Message } from "../types";
import Icon from "./Icon";
import MessageBubble from "./MessageBubble";

const EXAMPLES: { q: string; icon: string; tag: string }[] = [
  { q: "Bán kính uốn cong tối thiểu của cáp điện tòa S5 là bao nhiêu?", icon: "zap", tag: "Spec Điện" },
  { q: "Lưu lượng gió tươi cho phòng khách sạn theo thuyết minh thiết kế?", icon: "book", tag: "Thuyết minh" },
  { q: "Thời gian bảo hành hệ thống điện là bao lâu?", icon: "shield", tag: "Bảo hành" },
  { q: "Xuất Excel bảng gió tươi tòa S5 (Thuyết minh Bảng 4.3), có cột Nguồn", icon: "table", tag: "Xuất file" },
];

/** Cách đáy ≤ ngưỡng này (px) thì coi như đang "bám đáy" → tự cuộn theo nội dung mới. */
const STICK_PX = 80;

interface Props {
  messages: Message[];
  onExample: (q: string) => void;
  onConfirm: (confirmId: string, approve: boolean) => void;
}

export default function ChatView({ messages, onExample, onConfirm }: Props) {
  const box = useRef<HTMLDivElement>(null);
  // Bám đáy: chỉ tự cuộn khi người dùng đang ở cuối. Kéo lên đọc → ngừng bám, không giành lại cuộn.
  const stick = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const userCount = messages.filter((m) => m.role === "user").length;

  const toBottom = useCallback(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight; // tức thời: không animate theo từng token (gây giật)
  }, []);

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_PX;
    stick.current = atBottom;
    setShowJump(!atBottom);
  };

  // Người dùng gửi câu hỏi mới / mở hội thoại khác → bám đáy lại.
  useLayoutEffect(() => {
    stick.current = true;
    setShowJump(false);
    toBottom();
  }, [userCount, toBottom]);

  // Nội dung mới (token, tool, trích dẫn) → chỉ theo khi đang bám đáy; gom vào 1 frame.
  useLayoutEffect(() => {
    if (!stick.current) return;
    const id = requestAnimationFrame(toBottom);
    return () => cancelAnimationFrame(id);
  }, [messages, toBottom]);

  // Ảnh/bảng đổi chiều cao sau khi render → vẫn giữ đáy nếu đang bám.
  useEffect(() => {
    const inner = box.current?.firstElementChild;
    if (!inner || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => stick.current && toBottom());
    ro.observe(inner);
    return () => ro.disconnect();
  }, [messages.length > 0, toBottom]);

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

  // Bôi chọn để chép: nhấn chuột lên đoạn đang bôi thì trình duyệt hiểu là KÉO đoạn chữ (con trỏ "cấm") thay vì bôi
  // lại → bỏ vùng chọn cũ trước khi trình duyệt xử lý mousedown (giữ Shift = nới vùng chọn như thường)
  const freshSelection = (e: React.MouseEvent) => {
    if (e.button !== 0 || e.shiftKey || e.detail > 1) return; // double/triple click: chọn từ / đoạn như thường
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) sel.removeAllRanges();
  };
  // Không kéo-thả chữ trong khung chat (vẫn kéo được liên kết / ảnh)
  const noTextDrag = (e: React.DragEvent) => {
    if (!(e.target instanceof Element) || !e.target.closest("a, img")) e.preventDefault();
  };

  const streaming = messages[messages.length - 1]?.role === "assistant" && !!messages[messages.length - 1]?.step;

  return (
    <div className="chat-scroll">
      <div className="chat" ref={box} onScroll={onScroll} role="log" aria-live="polite" aria-busy={streaming}>
        <div className="chat-inner" onMouseDown={freshSelection} onDragStart={noTextDrag}>
          {messages.map((m, i) => (
            <MessageBubble key={i} m={m} onConfirm={onConfirm} />
          ))}
        </div>
      </div>
      {showJump && (
        <button
          className="jump-latest"
          onClick={() => {
            // nhảy tức thời: cuộn mượt bị nội dung đang stream "chạy trốn" → onScroll tưởng người dùng kéo lên
            stick.current = true;
            setShowJump(false);
            toBottom();
          }}
          aria-label="Cuộn xuống tin mới nhất"
        >
          <Icon name="arrowDown" size={16} />
          {streaming ? "Đang trả lời" : "Mới nhất"}
        </button>
      )}
    </div>
  );
}
