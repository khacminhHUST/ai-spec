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
 *  sửa ở bước duyệt. Mặc định là tài liệu mới; chọn "bản sửa đổi" để bản mới thay bản cũ.
 *  Chọn nhiều file → tải lần lượt (cùng luồng / phân hệ), file trùng bỏ qua, file lỗi liệt kê lại cuối cùng. */

interface Result {
  name: string;
  status: "ok" | "dup" | "err";
  msg?: string;
}
const isDuplicate = (msg: string) => /đã có trong kho|đã có job/.test(msg);
export default function UploadForm({ projectId, preset, userId, options, onUploaded, onClose, onError }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [done, setDone] = useState(0);
  const [results, setResults] = useState<Result[]>([]);
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

  const multi = files.length > 1;
  const pickFiles = (list: FileList | null) => {
    const all = Array.from(list ?? []);
    const bad = all.filter((f) => !f.name.toLowerCase().endsWith(".pdf") || f.size > options.max_upload_mb * 1024 * 1024);
    const ok = all.filter((f) => !bad.includes(f));
    setFiles(ok);
    setResults([]);
    setDone(0);
    if (ok.length > 1) setIsRevision(false); // nhiều file: luôn là tài liệu mới
    if (bad.length)
      onError(`Bỏ qua ${bad.length} file không phải PDF hoặc vượt ${options.max_upload_mb} MB: ${bad.map((f) => f.name).join(", ")}`);
  };
  const toggleDisc = (d: string) =>
    setDisciplines((v) => (v.includes(d) ? v.filter((x) => x !== d) : [...v, d]));

  const missing = [!files.length && "file PDF", isRevision && !seriesCode && "tài liệu cần thay"].filter(Boolean) as string[];
  const failed = results.filter((r) => r.status === "err");

  const submit = async () => {
    if (!files.length || missing.length) return;
    const meta: UploadMeta = {
      project_id: projectId,
      stream,
      series: isRevision ? { mode: "revision", doc_code: seriesCode } : { mode: "new" },
    };
    if (!isRevision && disciplines.length) meta.disciplines = disciplines; // bản sửa đổi: lấy phân hệ theo bộ
    setBusy(true);
    setResults([]);
    const out: Result[] = [];
    for (const [i, f] of files.entries()) {
      try {
        await uploadDocument(f, meta, userId);
        out.push({ name: f.name, status: "ok" });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        out.push({ name: f.name, status: isDuplicate(msg) ? "dup" : "err", msg });
        if (!multi) onError(e);
      }
      setDone(i + 1);
      setResults([...out]);
    }
    setBusy(false);
    if (!multi) {
      if (out[0]?.status === "ok") onUploaded();
    } else if (!out.some((r) => r.status === "err")) onUploaded();
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
        <span>File PDF * (chọn được nhiều file)</span>
        <input type="file" accept="application/pdf,.pdf" multiple disabled={busy} onChange={(e) => pickFiles(e.target.files)} />
        {multi && <small className="muted">Đã chọn {files.length} file – tải lần lượt, mỗi file là 1 tài liệu mới.</small>}
      </label>

      <div className="kn-field">
        <label className="kp-check">
          <input
            type="checkbox"
            checked={isRevision}
            disabled={series.length === 0 || multi}
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
          <span>
            Phân hệ (tùy chọn – để trống thì hệ thống tự nhận diện{multi ? " từng file" : ""}; chọn thì áp cho mọi file)
          </span>
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
      {multi && results.length > 0 && (
        <div className="small">
          <p>
            {done}/{files.length} file · thành công {results.filter((r) => r.status === "ok").length} · đã có trong
            kho {results.filter((r) => r.status === "dup").length} · lỗi {failed.length}
          </p>
          {!busy && failed.length > 0 && (
            <ul className="muted">
              {failed.map((r) => (
                <li key={r.name}>
                  {r.name}: {r.msg}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="kn-row">
        <span className="small muted">{missing.length > 0 && `Còn thiếu: ${missing.join(", ")}`}</span>
        {!busy && multi && failed.length > 0 ? (
          <button type="button" className="kn-btn primary" onClick={onUploaded}>
            Xong
          </button>
        ) : (
          <button type="submit" className="kn-btn primary" disabled={busy || missing.length > 0}>
            {busy ? (multi ? `Đang tải ${done + 1}/${files.length}…` : "Đang tải lên…") : "Tải lên"}
          </button>
        )}
      </div>
    </form>
  );
}
