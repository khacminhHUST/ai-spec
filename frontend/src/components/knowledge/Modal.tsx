import { useEffect, useId, useRef, type ReactNode } from "react";
import Icon from "../Icon";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/** Hộp thoại nổi dùng <dialog> gốc của trình duyệt (showModal): khóa nền, giữ focus trong hộp, Esc để đóng.
 *  Bấm ra ngoài (vùng nền mờ) cũng đóng. Điện thoại: trượt lên từ đáy (CSS .kp-modal). */
export default function Modal({ title, onClose, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);

  return (
    <dialog
      ref={ref}
      className="kp-modal"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault(); // Esc: để React đóng (gỡ component) thay vì trình duyệt tự đóng
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // bấm vào nền mờ quanh hộp
      }}
    >
      <div className="kp-modal-box">
        <header className="kp-modal-head">
          <h3 id={titleId}>{title}</h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Đóng">
            <Icon name="x" />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
