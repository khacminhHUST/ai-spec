import { useState } from "react";
import { createProject, updateProject, type Project } from "../../knowledgeApi";
import Modal from "./Modal";

interface Props {
  project?: Project | null; // có → sửa; không → tạo mới
  onSaved: (p: Project) => void;
  onCancel: () => void;
  onError?: (e: unknown) => void;
}

const split = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/** Hộp thoại tạo / sửa dự án: chỉ bắt buộc tên; ngày bắt đầu / kết thúc dự kiến tùy chọn (sửa sau được).
 *  Nâng cao (thu gọn): mã dự án (trống → tự sinh từ tên), tên gọi khác để chat nhận ra dự án. */
export default function ProjectForm({ project, onSaved, onCancel }: Props) {
  const [name, setName] = useState(project?.name ?? "");
  const [start, setStart] = useState(project?.start_date ?? "");
  const [end, setEnd] = useState(project?.end_date ?? "");
  const [code, setCode] = useState("");
  const [aliases, setAliases] = useState((project?.aliases ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null); // lỗi hiện trong hộp thoại (banner trang bị nền mờ che)

  const badRange = !!start && !!end && end < start;
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const dates = { start_date: start || null, end_date: end || null };
      const saved = project
        ? await updateProject(project.project_id, { name: name.trim(), aliases: split(aliases), ...dates })
        : await createProject({
            name: name.trim(),
            aliases: split(aliases),
            ...(code.trim() ? { project_id: code.trim() } : {}),
            ...dates,
          });
      onSaved(saved);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={project ? "Sửa dự án" : "Dự án mới"} onClose={onCancel}>
    <form
      className="kn-form kp-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim() && !badRange) void save();
      }}
    >
      <label className="kn-field">
        <span>Tên dự án *</span>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="VD DA Song Việt P1.14" />
      </label>
      <div className="kn-grid">
        <label className="kn-field">
          <span>Ngày bắt đầu</span>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="kn-field">
          <span>Dự kiến kết thúc</span>
          <input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {badRange && <div className="kn-err small">Ngày kết thúc dự kiến phải sau ngày bắt đầu.</div>}
      <details className="kp-advanced">
        <summary>Nâng cao</summary>
        {project ? (
          <div className="small muted">Mã dự án: {project.project_id}</div>
        ) : (
          <label className="kn-field">
            <span>Mã dự án</span>
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Để trống: tự sinh từ tên" />
          </label>
        )}
        <label className="kn-field">
          <span>Tên gọi khác (phân cách dấu phẩy) – để chat nhận ra dự án</span>
          <input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="Song Việt, P1.14" />
        </label>
      </details>
      {err && (
        <div className="kn-err small" role="alert">
          {err}
        </div>
      )}
      <div className="kn-row kp-modal-actions">
        <span className="small muted">Ngày để trống được, cập nhật sau.</span>
        <span className="kn-actions">
          <button type="button" className="kn-btn" disabled={busy} onClick={onCancel}>
            Hủy
          </button>
          <button type="submit" className="kn-btn primary" disabled={busy || !name.trim() || badRange}>
            {busy ? "Đang lưu…" : project ? "Lưu" : "Tạo dự án"}
          </button>
        </span>
      </div>
    </form>
    </Modal>
  );
}
