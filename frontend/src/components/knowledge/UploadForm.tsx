import { useEffect, useMemo, useState } from "react";
import {
  getTree,
  suggestFromName,
  uploadDocument,
  type KnowledgeOptions,
  type Project,
  type TreeProject,
  type UploadMeta,
} from "../../knowledgeApi";

interface Props {
  userId: string;
  options: KnowledgeOptions;
  projects: Project[];
  onUploaded: () => void;
  onError: (e: unknown) => void;
}

interface SeriesChoice {
  doc_code: string;
  label: string;
  building: string;
  stream: string;
  discipline: string;
}

function flattenSeries(tree: TreeProject[]): SeriesChoice[] {
  return tree.flatMap((p) =>
    p.buildings.flatMap((b) =>
      b.streams.flatMap((st) =>
        st.disciplines.flatMap((d) =>
          d.series.map((s) => ({
            doc_code: s.doc_code,
            label: `${s.title || s.doc_code} (${s.n_revisions} rev)`,
            building: b.building,
            stream: st.stream,
            discipline: d.discipline,
          })),
        ),
      ),
    ),
  );
}

/** Form upload (mục 2.1): chỉ PDF, metadata bắt buộc + tùy chọn (gợi ý từ tên file), chọn bộ tài liệu. */
export default function UploadForm({ userId, options, projects, onUploaded, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [projectId, setProjectId] = useState(projects[0]?.project_id ?? "");
  const [building, setBuilding] = useState("");
  const [stream, setStream] = useState("DESIGN_CONSULTANT");
  const [disciplines, setDisciplines] = useState<string[]>([]);
  const [docType, setDocType] = useState("");
  const [seriesMode, setSeriesMode] = useState<"new" | "revision">("new");
  const [seriesCode, setSeriesCode] = useState("");
  const [opt, setOpt] = useState({ revision: "", issue_date: "", stage: "", title: "", issuer: "" });
  const [series, setSeries] = useState<SeriesChoice[]>([]);
  const [busy, setBusy] = useState(false);

  const project = projects.find((p) => p.project_id === projectId);
  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].project_id);
  }, [projects, projectId]);
  useEffect(() => {
    setBuilding(project?.buildings[0]?.building ?? "");
    if (!projectId) return;
    getTree(projectId)
      .then((t) => setSeries(flattenSeries(t)))
      .catch(onError);
  }, [projectId, project, onError]);

  const seriesInBuilding = useMemo(() => series.filter((s) => s.building === building), [series, building]);

  const pickFile = async (f: File | null) => {
    setFile(f);
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      onError("Chỉ nhận file PDF (parse hiện chỉ hỗ trợ PDF).");
      return;
    }
    if (f.size > options.max_upload_mb * 1024 * 1024) onError(`File vượt giới hạn ${options.max_upload_mb} MB.`);
    try {
      const s = await suggestFromName(f.name);
      setOpt((o) => ({
        ...o,
        revision: o.revision || s.revision || "",
        issue_date: o.issue_date || s.issue_date || "",
        stage: o.stage || s.stage || "",
      }));
    } catch (e) {
      onError(e);
    }
  };

  const pickSeries = (code: string) => {
    setSeriesCode(code);
    const s = series.find((x) => x.doc_code === code);
    if (!s) return;
    setStream(s.stream);
    if (!disciplines.includes(s.discipline)) setDisciplines([s.discipline, ...disciplines]);
    const dt = options.doc_types.find((d) => code === d.value || code.includes(`_${d.value}`));
    if (dt) setDocType(dt.value);
  };

  const toggleDisc = (d: string) =>
    setDisciplines((ds) => (ds.includes(d) ? ds.filter((x) => x !== d) : [...ds, d]));

  const missing = [
    !file && "file PDF",
    !projectId && "dự án",
    !building && "công trình",
    !stream && "luồng",
    disciplines.length === 0 && "phân hệ",
    !docType && "loại tài liệu",
    seriesMode === "revision" && !seriesCode && "bộ tài liệu",
  ].filter(Boolean) as string[];

  const submit = async () => {
    if (!file || missing.length) return;
    const meta: UploadMeta = {
      project_id: projectId,
      building,
      stream,
      disciplines,
      doc_type: docType,
      ...Object.fromEntries(Object.entries(opt).filter(([, v]) => v.trim())),
      series: seriesMode === "new" ? { mode: "new" } : { mode: "revision", doc_code: seriesCode },
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

  const docTypeLabel = options.doc_types.find((d) => d.value === docType)?.label;

  return (
    <div className="kn-card kn-form">
      <h3>Upload tài liệu</h3>
      <label className="kn-field">
        <span>File PDF *</span>
        <input type="file" accept="application/pdf,.pdf" onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
      </label>
      <div className="kn-grid">
        <label className="kn-field">
          <span>Dự án *</span>
          <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.project_id} – {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="kn-field">
          <span>Công trình *</span>
          <select value={building} onChange={(e) => setBuilding(e.target.value)}>
            {(project?.buildings ?? []).map((b) => (
              <option key={b.building}>{b.building}</option>
            ))}
          </select>
        </label>
        <label className="kn-field">
          <span>Luồng *</span>
          <select value={stream} onChange={(e) => setStream(e.target.value)}>
            {options.streams.map((s) => (
              <option key={s.value} value={s.value} title={s.label}>
                {s.value}
              </option>
            ))}
          </select>
        </label>
        <label className="kn-field">
          <span>Loại tài liệu *</span>
          <select value={docType} onChange={(e) => setDocType(e.target.value)}>
            <option value="">(chọn)</option>
            {options.doc_types.map((d) => (
              <option key={d.value} value={d.value}>
                {d.value}
              </option>
            ))}
          </select>
          {docTypeLabel && <small className="muted">{docTypeLabel}</small>}
        </label>
      </div>
      <div className="kn-field">
        <span>Phân hệ * (phân hệ đầu tiên là phân hệ chính, dùng trong mã bộ)</span>
        <div className="kn-checks">
          {options.disciplines.map((d) => (
            <label key={d}>
              <input type="checkbox" checked={disciplines.includes(d)} onChange={() => toggleDisc(d)} /> {d}
            </label>
          ))}
        </div>
      </div>
      <div className="kn-field">
        <span>Bộ tài liệu *</span>
        <label>
          <input type="radio" checked={seriesMode === "new"} onChange={() => setSeriesMode("new")} /> Tài liệu mới
        </label>
        <label>
          <input
            type="radio"
            checked={seriesMode === "revision"}
            disabled={seriesInBuilding.length === 0}
            onChange={() => setSeriesMode("revision")}
          />{" "}
          Revision mới của:{" "}
          <select value={seriesCode} disabled={seriesMode !== "revision"} onChange={(e) => pickSeries(e.target.value)}>
            <option value="">(chọn bộ đã có)</option>
            {seriesInBuilding.map((s) => (
              <option key={s.doc_code} value={s.doc_code}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="kn-grid">
        <label className="kn-field">
          <span>Revision</span>
          <input value={opt.revision} onChange={(e) => setOpt({ ...opt, revision: e.target.value })} />
        </label>
        <label className="kn-field">
          <span>Ngày ban hành</span>
          <input type="date" value={opt.issue_date} onChange={(e) => setOpt({ ...opt, issue_date: e.target.value })} />
        </label>
        <label className="kn-field">
          <span>Giai đoạn</span>
          <select value={opt.stage} onChange={(e) => setOpt({ ...opt, stage: e.target.value })}>
            <option value="">(tự suy)</option>
            {options.stages.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="kn-field">
          <span>Đơn vị ban hành</span>
          <input value={opt.issuer} onChange={(e) => setOpt({ ...opt, issuer: e.target.value })} />
        </label>
      </div>
      <label className="kn-field">
        <span>Tiêu đề</span>
        <input value={opt.title} onChange={(e) => setOpt({ ...opt, title: e.target.value })} />
      </label>
      <p className="small muted">Trường tùy chọn để trống → hệ thống tự suy từ trang bìa khi xử lý.</p>
      <div className="kn-row">
        <span className="small muted">{missing.length > 0 && `Còn thiếu: ${missing.join(", ")}`}</span>
        <button className="kn-btn primary" disabled={busy || missing.length > 0} onClick={submit}>
          {busy ? "Đang tải lên…" : "Upload"}
        </button>
      </div>
    </div>
  );
}
