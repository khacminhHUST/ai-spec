import { useLayoutEffect, useRef, useState } from "react";
import Icon, { Spinner } from "./Icon";

interface Props {
  disabled: boolean;
  attachments: string[];
  uploading: boolean;
  onSend: (q: string) => void;
  onAttach: (f: File) => void;
  onRemoveAttachment: (name: string) => void;
}

export default function Composer({ disabled, attachments, uploading, onSend, onAttach, onRemoveAttachment }: Props) {
  const [text, setText] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // textarea tự giãn theo nội dung (tối đa ~8 dòng, phần còn lại cuộn)
  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }, [text]);

  const submit = () => {
    const q = text.trim();
    if (!q || disabled || uploading) return;
    onSend(q);
    setText("");
  };

  return (
    <div className="composer-wrap">
      <div className="composer">
        {(attachments.length > 0 || uploading) && (
          <div className="attachments">
            {attachments.map((a) => (
              <span key={a} className="chip file-chip">
                <Icon name="file" size={14} />
                {a}
                <button className="chip-x" aria-label={`Bỏ ${a}`} onClick={() => onRemoveAttachment(a)}>
                  <Icon name="x" size={14} />
                </button>
              </span>
            ))}
            {uploading && (
              <span className="step">
                <Spinner size={14} /> Đang tải file lên…
              </span>
            )}
          </div>
        )}
        <textarea
          ref={taRef}
          value={text}
          aria-label="Câu hỏi"
          placeholder="Hỏi về dự án, hoặc yêu cầu xuất file…"
          rows={1}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="composer-bar">
          <button
            className="icon-btn"
            title="Đính kèm file"
            aria-label="Đính kèm file"
            disabled={disabled || uploading}
            onClick={() => fileRef.current?.click()}
          >
            <Icon name="paperclip" />
          </button>
          <input
            ref={fileRef}
            type="file"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onAttach(f);
              e.target.value = "";
            }}
          />
          <span className="composer-hint">Enter để gửi · Shift+Enter xuống dòng</span>
          <button
            className="send-btn"
            onClick={submit}
            disabled={disabled || uploading || !text.trim()}
            aria-label={disabled ? "Đang trả lời" : "Gửi"}
          >
            {disabled ? <Spinner size={16} /> : <Icon name="send" />}
            <span className="send-label">{disabled ? "Đang trả lời…" : "Gửi"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
