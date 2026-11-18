import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getTree,
  streamLabel,
  uploadDocument,
  type KnowledgeOptions,
  type Project,
  type SeriesNode,
  type UploadMeta,
  type UploadPreset,
} from "../../knowledgeApi";

interface Props {
  preset?: UploadPreset | null;
  userId: string;
  options: KnowledgeOptions;
  projects: Project[];
  onProjectsChanged: () => Promise<unknown>;
  onUploaded: () => void;
  onError: (e: unknown) => void;
}

const NEW = "__new__";

/** Upload gọn (docs/knowledge-admin.md mục 2.1): File + Dự án + Luồng. Loại tài liệu, phân hệ, revision, ngày ban
 *  hành… hệ thống tự nhận diện khi xử lý, kiểm tra / sửa ở bước duyệt. Mặc định là tài liệu mới; tùy chọn chọn
 *  "bản sửa đổi của tài liệu đã có" để bản mới thay bản cũ. */
export default function UploadForm({ preset, userId, options, projects, onProjectsChanged, onUploaded, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [projectId, setProjectId] = useState(preset?.project_id ?? projects[0]?.project_id ?? "");
  const [stream, setStream] = useState(preset?.stream ?? "DESIGN_CONSULTANT");
  const [isRevision, setIsRevision] = useState(!!preset?.doc_code);
  const [seriesCode, setSeriesCode] = useState(preset?.doc_code ?? "");
  const [series, setSeries] = useState<(SeriesNode & { stream: string })[]>([]);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  const creating = projectId === NEW;
  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].project_id);
  }, [projects, projectId]);
  useEffect(() => {
    if (!projectId || creating) {
      setSeries([]);
      return;
    }
    getTree(projectId)
      .then((t) => setSeries((t[0]?.streams ?? []).flatMap((st) => st.series.map((s) => ({ ...s, stream: st.stream })))))
      .catch(onError);
  }, [projectId, creating, onError]);

  const chosen = useMemo(() => series.find((s) => s.doc_code === seriesCode), [series, seriesCode]);
  useEffect(() => {
    if (isRevision && chosen) setStream(chosen.stream); // bản sửa đổi giữ luồng của tài liệu gốc
  }, [isRevision, chosen]);

  const pickFile = (f: File | null) => {
    setFile(f);
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) onError("Chỉ nhận file PDF.");
    else if (f.size > options.max_upload_mb * 1024 * 1024) onError(`File vượt giới hạn ${options.max_upload_mb} MB.`);
  };

  const createNew = async () => {
    setBusy(true);
    try {
      const p = await createProject({ name: newName.trim() });
      await onProjectsChanged();
      setProjectId(p.project_id);
      setNewName("");
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const missing = [
    !file && "file PDF",
    (!projectId || creating) && "dự án",
    isRevision && !seriesCode && "tài liệu cần thay",
  ].filter(Boolean) as string[];

  const submit = async () => {
    if (!file || missing.length) return;
    const meta: UploadMeta = {
      project_id: projectId,
      stream,
      series: isRevision ? { mode: "revision", doc_code: seriesCode } : { mode: "new" },
    };
    setBusy(true);
    try {
      await uploadDocument(file, meta, userId);
      onUploaded();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="kn-card kn-form kp-upload"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h3>Tải tài liệu lên</h3>
      <label className="kn-field">
        <span>File PDF *</span>
        <input type="file" accept="application/pdf,.pdf" onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
      </label>
      <div className="kn-grid">
        <label className="kn-field">
          <span>Dự án *</span>
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.length === 0 && <option value="">(chưa có dự án)</option>}
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
            <option value={NEW}>+ Tạo dự án mới…</option>
          </select>
        </label>
        <label className="kn-field">
          <span>Luồng xử lý *</span>
          <select value={stream} disabled={isRevision && !!chosen} onChange={(e) => setStream(e.target.value)}>
            {options.streams.map((s) => (
              <option key={s.value} value={s.value}>
                {streamLabel(s.value)}
              </option>
            ))}
          </select>
          <small className="muted">{options.streams.find((s) => s.value === stream)?.label}</small>
        </label>
      </div>

      {creating && (
        <div className="kn-card kn-inline">
          <strong>Tạo dự án mới</strong>
          <div className="kn-row">
            <input
              value={newName}
              placeholder="Tên dự án"
              aria-label="Tên dự án mới"
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (newName.trim()) void createNew();
                }
              }}
            />
            <button type="button" className="kn-btn small primary" disabled={busy || !newName.trim()} onClick={createNew}>
              Tạo
            </button>
          </div>
          <span className="small muted">Ngày bắt đầu / kết thúc dự kiến bổ sung sau ở trang dự án.</span>
        </div>
      )}

      <div className="kn-field">
        <label className="kp-check">
          <input
            type="checkbox"
            checked={isRevision}
            disabled={series.length === 0}
            onChange={(e) => setIsRevision(e.target.checked)}
          />
          Đây là bản sửa đổi (revision mới) của tài liệu đã có trong dự án
        </label>
        {isRevision && (
          <select value={seriesCode} onChange={(e) => setSeriesCode(e.target.value)} aria-label="Tài liệu cần thay">
            <option value="">(chọn tài liệu cần thay)</option>
            {series.map((s) => (
              <option key={s.doc_code} value={s.doc_code}>
                {s.title || s.doc_code} · {streamLabel(s.stream)} ({s.n_revisions} rev)
              </option>
            ))}
          </select>
        )}
        {!isRevision && <small className="muted">Không chọn → lưu thành tài liệu mới.</small>}
      </div>

      <p className="small muted">
        Loại tài liệu, phân hệ, revision, ngày ban hành, tiêu đề: hệ thống tự nhận diện khi xử lý. Kiểm tra và sửa ở
        bước duyệt (Hàng chờ xử lý) trước khi phát hành; sau khi phát hành vẫn sửa được trong dự án.
      </p>
      <div className="kn-row">
        <span className="small muted">{missing.length > 0 && `Còn thiếu: ${missing.join(", ")}`}</span>
        <button type="submit" className="kn-btn primary" disabled={busy || missing.length > 0}>
          {busy ? "Đang tải lên…" : "Tải lên"}
        </button>
      </div>
    </form>
  );
}
