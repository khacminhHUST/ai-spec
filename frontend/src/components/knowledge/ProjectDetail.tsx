import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../Icon";
import {
  fmtDate,
  projectStatus,
  streamLabel,
  type KnowledgeOptions,
  type Project,
  type TreeProject,
  type UploadPreset,
} from "../../knowledgeApi";
import ProjectForm from "./ProjectForm";
import SeriesHistory from "./SeriesHistory";
import UploadForm from "./UploadForm";

interface Props {
  project: Project | null;
  node: TreeProject | null;
  userId: string;
  options: KnowledgeOptions | null;
  projects: Project[];
  onBack: () => void;
  onOpenQueue: () => void;
  onChanged: () => void;
  onError: (e: unknown) => void;
}

const norm = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/gi, "d")
    .toLowerCase();

/** Tài liệu của 1 dự án, chia theo luồng. Khung bên cạnh: chọn 1 bộ → lịch sử revision, sửa / chuyển dự án, xóa;
 *  hoặc tải tài liệu lên dự án này. */
export default function ProjectDetail({
  project,
  node,
  userId,
  options,
  projects,
  onBack,
  onOpenQueue,
  onChanged,
  onError,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null); // doc_code đang xem
  const [upload, setUpload] = useState<UploadPreset | null>(null); // đang mở khung tải lên
  const hasPanel = !!(selected || upload);
  const onUpload = (preset: UploadPreset) => {
    setSelected(null);
    setUpload(preset);
  };
  const openSeries = (code: string | null) => {
    setUpload(null);
    setSelected(code);
  };
  const detailRef = useRef<HTMLElement>(null);

  // Khung hẹp (điện thoại, iframe hẹp): chi tiết nằm dưới danh sách → cuộn tới khi chọn tài liệu
  useEffect(() => {
    if (hasPanel && window.matchMedia("(max-width: 1179px)").matches)
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [selected, upload, hasPanel]);

  const streams = useMemo(() => {
    const q = norm(query.trim());
    return (node?.streams ?? [])
      .map((st) => ({
        ...st,
        series: q ? st.series.filter((s) => norm(`${s.title ?? ""} ${s.doc_code}`).includes(q)) : st.series,
      }))
      .filter((st) => st.series.length > 0);
  }, [node, query]);

  if (!project || !node) return <div className="muted pad">Đang tải…</div>;
  const st = projectStatus(project.start_date, project.end_date);
  const pid = project.project_id;

  return (
    <div className="kp">
      <button className="kn-link kp-back" onClick={onBack}>
        <Icon name="arrow-left" size={16} /> Tất cả dự án
      </button>

      <div className="kn-card kp-hero">
        <div className="kp-hero-main">
          <div className="kp-card-top">
            <h2 className="kp-title">{project.name}</h2>
            <span className={`kp-badge ${st.tone}`}>{st.label}</span>
          </div>
          <div className="kp-hero-meta muted">
            <span>Mã {pid}</span>
            <span>
              <Icon name="calendar" size={15} /> Bắt đầu: {fmtDate(project.start_date)}
            </span>
            <span>
              <Icon name="calendar" size={15} /> Dự kiến kết thúc: {fmtDate(project.end_date)}
            </span>
            <span>
              <Icon name="file" size={15} /> {node.n_series} bộ tài liệu
            </span>
            {node.active_jobs > 0 && (
              <button className="kn-link" onClick={onOpenQueue}>
                <Icon name="clock" size={15} /> {node.active_jobs} file đang xử lý / chờ duyệt
              </button>
            )}
          </div>
        </div>
        <div className="kn-actions">
          <button className="kn-btn" onClick={() => setEditing((v) => !v)}>
            <Icon name="pencil" size={15} /> Sửa dự án
          </button>
          <button className="kn-btn primary" onClick={() => onUpload({ project_id: pid })}>
            <Icon name="upload" size={15} /> Tải tài liệu lên
          </button>
        </div>
      </div>

      {editing && (
        <ProjectForm
          project={project}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
          onError={onError}
        />
      )}

      <div className={"kp-docs" + (hasPanel ? " has-detail" : "")}>
        <section className="kp-doclist">
          {node.n_series > 0 && (
            <label className="kp-search">
              <Icon name="search" size={18} />
              <input
                type="search"
                value={query}
                placeholder="Tìm tài liệu trong dự án…"
                aria-label="Tìm tài liệu trong dự án"
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          )}
          {node.n_series === 0 ? (
            <div className="kp-empty">
              <Icon name="folder" size={28} />
              <p>Dự án chưa có tài liệu.</p>
              <button className="kn-btn primary" onClick={() => onUpload({ project_id: pid })}>
                <Icon name="upload" size={15} /> Tải tài liệu lên
              </button>
            </div>
          ) : streams.length === 0 ? (
            <div className="muted pad">Không có tài liệu khớp “{query}”.</div>
          ) : (
            streams.map((g) => (
              <div key={g.stream} className="kp-stream">
                <div className="kp-stream-head">
                  <strong>{streamLabel(g.stream)}</strong>
                  <span className="muted small">{g.series.length}</span>
                  <button
                    className="kn-link small kp-stream-add"
                    onClick={() => onUpload({ project_id: pid, stream: g.stream })}
                    title={`Tải tài liệu ${streamLabel(g.stream)} lên`}
                  >
                    + thêm
                  </button>
                </div>
                {g.series.map((s) => (
                  <button
                    key={s.doc_code}
                    className={"kp-doc" + (selected === s.doc_code ? " active" : "")}
                    onClick={() => openSeries(s.doc_code === selected ? null : s.doc_code)}
                    aria-expanded={selected === s.doc_code}
                  >
                    <Icon name="file" size={18} className="kp-doc-icon" />
                    <span className="kp-doc-body">
                      <span className="kp-doc-title">{s.title || s.doc_code}</span>
                      <span className="kp-doc-meta">
                        {s.unpublished ? (
                          <span className="kn-tag warn">Đã gỡ khỏi tìm kiếm</span>
                        ) : (
                          <span>Rev {s.latest?.revision ?? "?"}</span>
                        )}
                        {s.issue_date && <span>{fmtDate(s.issue_date)}</span>}
                        {s.n_revisions > 1 && <span>{s.n_revisions} revision</span>}
                        {s.disciplines.map((d) => (
                          <span key={d} className="kp-chip">
                            {d}
                          </span>
                        ))}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            ))
          )}
        </section>
        {upload && options && (
          <section className="kp-docdetail" ref={detailRef}>
            <UploadForm
              key={JSON.stringify(upload)}
              projectId={pid}
              preset={upload}
              userId={userId}
              options={options}
              onUploaded={() => {
                setUpload(null);
                onChanged(); // dòng "N file đang xử lý / chờ duyệt" ở đầu trang cập nhật
              }}
              onClose={() => setUpload(null)}
              onError={onError}
            />
          </section>
        )}
        {selected && (
          <section className="kp-docdetail" ref={detailRef}>
            <SeriesHistory
              docCode={selected}
              userId={userId}
              options={options}
              projects={projects}
              onAddRevision={onUpload}
              onChanged={onChanged}
              onDeleted={() => {
                setSelected(null);
                onChanged();
              }}
              onMoved={() => {
                setSelected(null);
                onChanged();
              }}
              onClose={() => setSelected(null)}
              onError={onError}
            />
          </section>
        )}
      </div>
    </div>
  );
}
