import { useState } from "react";

export default function Composer({ disabled, onSend }: { disabled: boolean; onSend: (q: string) => void }) {
  const [text, setText] = useState("");

  const submit = () => {
    const q = text.trim();
    if (!q || disabled) return;
    onSend(q);
    setText("");
  };

  return (
    <div className="composer">
      <textarea
        value={text}
        placeholder="Hỏi về dự án… (Enter để gửi, Shift+Enter xuống dòng)"
        rows={2}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button onClick={submit} disabled={disabled || !text.trim()}>
        {disabled ? "Đang trả lời…" : "Gửi"}
      </button>
    </div>
  );
}
