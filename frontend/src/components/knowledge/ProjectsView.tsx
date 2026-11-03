import { useEffect, useState } from "react";
import {
  addBuilding,
  createProject,
  deleteBuilding,
  updateBuilding,
  updateProject,
  type Option,
  type Project,
} from "../../knowledgeApi";

interface Props {
  projects: Project[];
  streams: Option[];
  onChanged: () => void;
  onError: (e: unknown) => void;
}

const split = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
const join = (xs: string[]) => xs.join(", ");

interface Form {
  project_id: string;
  name: string;
  aliases: string;
  owner_aliases: string;
  source_priority: string;
  governing_language: string;
}

const EMPTY: Form = { project_id: "", name: "", aliases: "", owner_aliases: "", source_priority: "", governing_language: "vi" };

const toForm = (p: Project): Form => ({
  project_id: p.project_id,
  name: p.name,
  aliases: join(p.aliases),
  owner_aliases: join(p.owner_aliases),
  source_priority: join(p.source_priority),
  governing_language: p.governing_language,
});

/** Dự án: tạo/sửa (mã, tên, alias, alias chủ đầu tư, ưu tiên nguồn, ngôn ngữ ưu tiên) và công trình (mã, alias). */
export default function ProjectsView({ projects, streams, onChanged, onError }: Props) {
  const [selected, setSelected] = useState<string | null>(projects[0]?.project_id ?? null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [bAliases, setBAliases] = useState<Record<string, string>>({});
  const [newB, setNewB] = useState({ building: "", aliases: "" });
  const [busy, setBusy] = useState(false);

  const project = projects.find((p) => p.project_id === selected) ?? null;
  useEffect(() => {
    setForm(project ? toForm(project) : EMPTY);
    setBAliases(Object.fromEntries((project?.buildings ?? []).map((b) => [b.building, join(b.aliases)])));
  }, [project]);

  const run = async (f: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await f();
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const fields = {
    name: form.name,
    aliases: split(form.aliases),
    owner_aliases: split(form.owner_aliases),
    source_priority: split(form.source_priority),
    governing_language: form.governing_language,
  };
  const save = () =>
    run(async () => {
      if (project) await updateProject(project.project_id, fields);
      else {
        await createProject({ project_id: form.project_id.trim(), ...fields });
        setSelected(form.project_id.trim());
      }
    });

  const text = (k: keyof Form, label: string, hint?: string) => (
    <label className="kn-field">
      <span>{label}</span>
      <input value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} placeholder={hint} />
    </label>
  );

  return (
    <div className="kn-split">
      <section className="kn-tree">
        <div className="kn-row">
          <strong>Dự án</strong>
          <button className="kn-btn small" onClick={() => setSelected(null)}>
            + Dự án mới
          </button>
        </div>
        {projects.map((p) => (
          <button
            key={p.project_id}
            className={"kn-series" + (p.project_id === selected ? " active" : "")}
            onClick={() => setSelected(p.project_id)}
          >
            <span className="kn-series-title">{p.project_id}</span>
            <span className="small muted">
              {p.name} · {p.buildings.length} công trình
            </span>
          </button>
        ))}
      </section>
      <section className="kn-detail">
        <div className="kn-card kn-form">
          <h3>{project ? `Sửa dự án ${project.project_id}` : "Tạo dự án"}</h3>
          {!project && text("project_id", "Mã dự án *", "VD 26DA06 (chữ, số, '.', '-')")}
          {text("name", "Tên dự án *")}
          {text("aliases", "Alias (phân cách dấu phẩy)", "Quảng Trường Biển, Bãi Đất Đỏ")}
          {text("owner_aliases", "Alias chủ đầu tư")}
          {text("source_priority", "Thứ tự ưu tiên nguồn (cao → thấp)", streams.map((s) => s.value).join(", "))}
          <label className="kn-field">
            <span>Ngôn ngữ ưu tiên</span>
            <select value={form.governing_language} onChange={(e) => setForm({ ...form, governing_language: e.target.value })}>
              <option value="vi">vi</option>
              <option value="en">en</option>
            </select>
          </label>
          <div className="kn-row">
            <span className="small muted">Sửa alias → dùng được ngay trong chat.</span>
            <button className="kn-btn primary" disabled={busy || !form.name.trim()} onClick={save}>
              {project ? "Lưu" : "Tạo dự án"}
            </button>
          </div>
        </div>
        {project && (
          <div className="kn-card kn-form">
            <h3>Công trình</h3>
            {project.buildings.map((b) => (
              <div key={b.building} className="kn-row kn-building">
                <strong>{b.building}</strong>
                <input
                  value={bAliases[b.building] ?? ""}
                  onChange={(e) => setBAliases({ ...bAliases, [b.building]: e.target.value })}
                  placeholder="alias, phân cách dấu phẩy"
                />
                <button
                  className="kn-btn small"
                  disabled={busy}
                  onClick={() => run(() => updateBuilding(project.project_id, b.building, split(bAliases[b.building] ?? "")))}
                >
                  Lưu
                </button>
                <button
                  className="kn-btn small danger"
                  disabled={busy}
                  onClick={() =>
                    window.confirm(`Xóa công trình ${b.building}?`) &&
                    run(() => deleteBuilding(project.project_id, b.building))
                  }
                >
                  Xóa
                </button>
              </div>
            ))}
            <div className="kn-row kn-building">
              <input
                value={newB.building}
                onChange={(e) => setNewB({ ...newB, building: e.target.value })}
                placeholder="Mã công trình (VD S6)"
              />
              <input
                value={newB.aliases}
                onChange={(e) => setNewB({ ...newB, aliases: e.target.value })}
                placeholder="alias, phân cách dấu phẩy"
              />
              <button
                className="kn-btn small primary"
                disabled={busy || !newB.building.trim()}
                onClick={() =>
                  run(async () => {
                    await addBuilding(project.project_id, { building: newB.building.trim(), aliases: split(newB.aliases) });
                    setNewB({ building: "", aliases: "" });
                  })
                }
              >
                Thêm
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
