import { useEffect, useMemo, useState } from "react";
import Icon from "../Icon";
import {
  getTree,
  streamLabel,
  uploadDocument,
  type KnowledgeOptions,
  type SeriesNode,
  type UploadMeta,
  type UploadPreset,
} from "../../knowledgeApi";

interface Props {
  projectId: string;
  preset?: UploadPreset | null;
  userId: string;
  options: KnowledgeOptions;
  onUploaded: () => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}

/** Tải tài liệu lên 1 dự án (khung bên cạnh trong trang dự án, docs/knowledge-admin.md mục 2.1): File + Luồng;
 *  phân hệ tùy chọn (để trống → hệ thống tự nhận diện). Loại tài liệu, revision, ngày ban hành… tự nhận diện,
 *  sửa ở bước duyệt. Mặc định là tài liệu mới; chọn "bản sửa đổi" để bản mới thay bản cũ. */
export default function UploadForm({ projectId, preset, userId, options, onUploaded, onClose, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [stream, setStream] = useState(preset?.stream ?? "DESIGN_CONSULTANT");
  const [disciplines, setDisciplines] = useState<string[]>([]);
  const [isRevision, setIsRevision] = useState(!!preset?.doc_code);
  const [seriesCode, setSeriesCode] = useState(preset?.doc_code ?? "");
  const [series, setSeries] = useState<(SeriesNode & { stream: string })[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getTree(projectId)
      .then((t) => setSeries((t[0]?.streams ?? []).flatMap((st) => st.series.map((s) => ({ ...s, stream: st.stream })))))
      .catch(onError);
  }, [projectId, onError]);

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
  const toggleDisc = (d: string) =>
    setDisciplines((v) => (v.includes(d) ? v.filter((x) => x !== d) : [...v, d]));

  const missing = [!file && "file PDF", isRevision && !seriesCode && "tài liệu cần thay"].filter(Boolean) as string[];

  const submit = async () => {
    if (!file || missing.length) return;
    const meta: UploadMeta = {
      project_id: projectId,
      stream,
      series: isRevision ? { mode: "revision", doc_code: seriesCode } : { mode: "new" },
    };
    if (!isRevision && disciplines.length) meta.disciplines = disciplines; // bản sửa đổi: lấy phân hệ theo bộ
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
      className="kn-card kn-form kp-upload kn-series"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <button type="button" className="icon-btn kn-series-close" onClick={onClose} aria-label="Đóng">
        <Icon name="x" />
      </button>
      <h3 className="kp-series-head">{isRevision ? "Thêm revision" : "Tải tài liệu lên"}</h3>
      <label className="kn-field">
        <span>File PDF *</span>
        <input type="file" accept="application/pdf,.pdf" onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
      </label>

      <div className="kn-field">
        <label className="kp-check">
          <input
            type="checkbox"
            checked={isRevision}
            disabled={series.length === 0}
            onChange={(e) => setIsRevision(e.target.checked)}
          />
          Bản sửa đổi (revision mới) của tài liệu đã có
        </label>
        {isRevision ? (
          <select value={seriesCode} onChange={(e) => setSeriesCode(e.target.value)} aria-label="Tài liệu cần thay">
            <option value="">(chọn tài liệu cần thay)</option>
            {series.map((s) => (
              <option key={s.doc_code} value={s.doc_code}>
                {s.title || s.doc_code} · {streamLabel(s.stream)} ({s.n_revisions} rev)
              </option>
            ))}
          </select>
        ) : (
          <small className="muted">Không chọn → lưu thành tài liệu mới.</small>
        )}
      </div>

      <label className="kn-field">
        <span>Luồng *</span>
        <select value={stream} disabled={isRevision && !!chosen} onChange={(e) => setStream(e.target.value)}>
          {options.streams.map((s) => (
            <option key={s.value} value={s.value}>
              {streamLabel(s.value)}
            </option>
          ))}
        </select>
        <small className="muted">{options.streams.find((s) => s.value === stream)?.label}</small>
      </label>

      {!isRevision && (
        <div className="kn-field">
          <span>Phân hệ (tùy chọn – để trống thì hệ thống tự nhận diện)</span>
          <div className="kn-checks">
            {options.disciplines.map((d) => (
              <label key={d}>
                <input type="checkbox" checked={disciplines.includes(d)} onChange={() => toggleDisc(d)} /> {d}
              </label>
            ))}
          </div>
        </div>
      )}

      <p className="small muted">
        Tải lên xong hệ thống tự xử lý và đưa vào kho (theo dõi ở Hàng chờ xử lý). Loại tài liệu, revision, ngày ban
        hành, tiêu đề được tự nhận diện; nếu sai thì sửa trong trang dự án.
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
