import { useRef, useState } from "react";

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

  const submit = () => {
    const q = text.trim();
    if (!q || disabled || uploading) return;
    onSend(q);
    setText("");
  };

  return (
    <div className="composer-wrap">
      {(attachments.length > 0 || uploading) && (
        <div className="attachments">
          {attachments.map((a) => (
            <span key={a} className="chip">
              {a}
              <button className="chip-x" aria-label={`Bỏ ${a}`} onClick={() => onRemoveAttachment(a)}>
                ×
              </button>
            </span>
          ))}
          {uploading && <span className="step">Đang tải file lên…</span>}
        </div>
      )}
      <div className="composer">
        <button
          className="attach"
          title="Đính kèm file"
          aria-label="Đính kèm file"
          disabled={disabled || uploading}
          onClick={() => fileRef.current?.click()}
        >
          📎
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
        <textarea
          value={text}
          placeholder="Hỏi về dự án, hoặc yêu cầu xuất file… (Enter để gửi, Shift+Enter xuống dòng)"
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button onClick={submit} disabled={disabled || uploading || !text.trim()}>
          {disabled ? "Đang trả lời…" : "Gửi"}
        </button>
      </div>
    </div>
  );
}
