import { useEffect, useMemo, useState } from "react";
import {
  addBuilding,
  createProject,
  getTree,
  uploadDocument,
  type KnowledgeOptions,
  type Project,
  type TreeProject,
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
// Thứ tự ưu tiên nguồn mặc định cho dự án tạo nhanh (giống config/projects.yaml); sửa sau ở tab "Dự án".
const DEFAULT_PRIORITY = ["CONTRACT", "DESIGN_CONSULTANT", "OPERATOR_STANDARD", "LEGAL_STANDARD"];
const splitList = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

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

/** Form upload (mục 2.1): chỉ PDF, chỉ các trường bắt buộc; revision / ngày / giai đoạn / tiêu đề / đơn vị ban hành
 *  để hệ thống tự suy từ tên file và trang bìa khi xử lý. */
export default function UploadForm({
  preset,
  userId,
  options,
  projects,
  onProjectsChanged,
  onUploaded,
  onError,
}: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [projectId, setProjectId] = useState(preset?.project_id ?? projects[0]?.project_id ?? "");
  const [building, setBuilding] = useState(preset?.building ?? "");
  const [presetApplied, setPresetApplied] = useState(!preset?.doc_code);
  const [stream, setStream] = useState("DESIGN_CONSULTANT");
  const [disciplines, setDisciplines] = useState<string[]>([]);
  const [docType, setDocType] = useState("");
  const [seriesMode, setSeriesMode] = useState<"new" | "revision">("new");
  const [seriesCode, setSeriesCode] = useState("");
  const [series, setSeries] = useState<SeriesChoice[]>([]);
  const [busy, setBusy] = useState(false);
  // Tạo nhanh dự án / công trình ngay trong form
  const [newProject, setNewProject] = useState({ project_id: "", name: "", aliases: "", building: "" });
  const [newBuilding, setNewBuilding] = useState("");

  const creatingProject = projectId === NEW;
  const creatingBuilding = building === NEW;
  const project = projects.find((p) => p.project_id === projectId);
  useEffect(() => {
    if (!projectId && projects[0]) setProjectId(projects[0].project_id);
  }, [projects, projectId]);
  useEffect(() => {
    // giữ công trình đang chọn nếu vẫn còn (vd sau khi tải lại danh sách dự án)
    setBuilding((cur) =>
      cur === NEW || project?.buildings.some((b) => b.building === cur) ? cur : project?.buildings[0]?.building ?? "",
    );
    if (!projectId || creatingProject) {
      setSeries([]);
      return;
    }
    getTree(projectId)
      .then((t) => setSeries(flattenSeries(t)))
      .catch(onError);
  }, [projectId, project, creatingProject, onError]);

  const runCreate = async (f: () => Promise<void>) => {
    setBusy(true);
    try {
      await f();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const submitNewProject = () =>
    runCreate(async () => {
      const id = newProject.project_id.trim();
      const b = newProject.building.trim();
      await createProject({
        project_id: id,
        name: newProject.name.trim(),
        aliases: splitList(newProject.aliases),
        owner_aliases: [],
        source_priority: DEFAULT_PRIORITY,
        governing_language: "vi",
        buildings: [{ building: b, aliases: [] }],
      });
      await onProjectsChanged();
      setProjectId(id);
      setBuilding(b);
      setNewProject({ project_id: "", name: "", aliases: "", building: "" });
    });

  const submitNewBuilding = () =>
    runCreate(async () => {
      const b = newBuilding.trim();
      await addBuilding(projectId, { building: b, aliases: [] });
      await onProjectsChanged();
      setBuilding(b);
      setNewBuilding("");
    });

  const seriesInBuilding = useMemo(() => series.filter((s) => s.building === building), [series, building]);

  // "+ Thêm revision" từ kho: khi danh sách bộ đã tải → chọn sẵn "Revision mới của" bộ đó
  useEffect(() => {
    if (presetApplied || !preset?.doc_code || !series.some((s) => s.doc_code === preset.doc_code)) return;
    setSeriesMode("revision");
    pickSeries(preset.doc_code);
    setPresetApplied(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, preset, presetApplied]);

  const pickFile = (f: File | null) => {
    setFile(f);
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      onError("Chỉ nhận file PDF (parse hiện chỉ hỗ trợ PDF).");
      return;
    }
    if (f.size > options.max_upload_mb * 1024 * 1024) onError(`File vượt giới hạn ${options.max_upload_mb} MB.`);
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
    (!projectId || creatingProject) && "dự án",
    (!building || creatingBuilding) && "công trình",
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
            <option value={NEW}>+ Tạo dự án mới…</option>
          </select>
        </label>
        <label className="kn-field">
          <span>Công trình *</span>
          <select value={building} disabled={creatingProject} onChange={(e) => setBuilding(e.target.value)}>
            {!creatingProject && (project?.buildings ?? []).length === 0 && <option value="">(chưa có)</option>}
            {(project?.buildings ?? []).map((b) => (
              <option key={b.building}>{b.building}</option>
            ))}
            {!creatingProject && project && <option value={NEW}>+ Thêm công trình…</option>}
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
      {creatingProject && (
        <div className="kn-card kn-inline">
          <strong>Tạo dự án mới</strong>
          <div className="kn-grid">
            <label className="kn-field">
              <span>Mã dự án *</span>
              <input
                value={newProject.project_id}
                placeholder="VD 25DA001 (chữ, số, '.', '-')"
                onChange={(e) => setNewProject({ ...newProject, project_id: e.target.value })}
              />
            </label>
            <label className="kn-field">
              <span>Tên dự án *</span>
              <input value={newProject.name} onChange={(e) => setNewProject({ ...newProject, name: e.target.value })} />
            </label>
            <label className="kn-field">
              <span>Alias (phân cách dấu phẩy)</span>
              <input
                value={newProject.aliases}
                placeholder="Sola Park, Sola"
                onChange={(e) => setNewProject({ ...newProject, aliases: e.target.value })}
              />
            </label>
            <label className="kn-field">
              <span>Công trình đầu tiên *</span>
              <input
                value={newProject.building}
                placeholder="VD G1, Tháp A"
                onChange={(e) => setNewProject({ ...newProject, building: e.target.value })}
              />
            </label>
          </div>
          <div className="kn-row">
            <span className="small muted">Ưu tiên nguồn, alias chủ đầu tư… chỉnh thêm ở tab "Dự án".</span>
            <button
              className="kn-btn small primary"
              disabled={busy || !newProject.project_id.trim() || !newProject.name.trim() || !newProject.building.trim()}
              onClick={submitNewProject}
            >
              Tạo dự án
            </button>
          </div>
        </div>
      )}
      {creatingBuilding && (
        <div className="kn-card kn-inline">
          <strong>Thêm công trình cho {projectId}</strong>
          <div className="kn-row">
            <input value={newBuilding} placeholder="Mã công trình (VD S6)" onChange={(e) => setNewBuilding(e.target.value)} />
            <button className="kn-btn small primary" disabled={busy || !newBuilding.trim()} onClick={submitNewBuilding}>
              Thêm
            </button>
          </div>
        </div>
      )}
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
      <p className="small muted">
        Revision, ngày ban hành, giai đoạn, tiêu đề, đơn vị ban hành: hệ thống tự suy từ tên file và trang bìa khi xử
        lý (sửa được sau trong kho).
      </p>
      <div className="kn-row">
        <span className="small muted">{missing.length > 0 && `Còn thiếu: ${missing.join(", ")}`}</span>
        <button className="kn-btn primary" disabled={busy || missing.length > 0} onClick={submit}>
          {busy ? "Đang tải lên…" : "Upload"}
        </button>
      </div>
    </div>
  );
}
