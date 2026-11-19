import { useCallback, useEffect, useState } from "react";
import {
  getOptions,
  getTree,
  listProjects,
  type KnowledgeOptions,
  type Project,
  type TreeProject,
} from "../../knowledgeApi";
import JobQueue from "./JobQueue";
import JobReview from "./JobReview";
import ProjectDetail from "./ProjectDetail";
import ProjectsBoard from "./ProjectsBoard";
import "./knowledge.css";

type View = "projects" | "queue";

const TABS: { id: View; label: string }[] = [
  { id: "projects", label: "Dự án" },
  { id: "queue", label: "Hàng chờ xử lý" },
];

interface Props {
  userId: string;
  onClose: () => void;
}

/** Tab "Kho tri thức" (docs/knowledge-admin.md mục 7): tài liệu quản lý theo dự án – lưới dự án → chi tiết dự án
 *  (tài liệu theo luồng, sửa / chuyển dự án, tải lên ở khung bên cạnh); hàng chờ xử lý + duyệt. Điều hướng bằng state. */
export default function KnowledgePage({ userId }: Props) {
  const [view, setView] = useState<View>("projects");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [options, setOptions] = useState<KnowledgeOptions | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tree, setTree] = useState<TreeProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const onError = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), []);
  const reload = useCallback(
    () =>
      Promise.all([listProjects().then(setProjects), getTree().then(setTree)])
        .catch(onError)
        .finally(() => setLoading(false)),
    [onError],
  );

  useEffect(() => {
    getOptions().then(setOptions).catch(onError);
    void reload();
  }, [onError, reload]);

  const pending = tree.reduce((n, p) => n + p.active_jobs, 0); // job chưa xong (chờ / đang xử lý / chờ duyệt / lỗi)

  const go = (v: View) => {
    setError(null);
    setJobId(null);
    setView(v);
    if (v === "projects") void reload();
  };

  return (
    <main className="main kn">
      <header className="topbar">
        <span className="topbar-title">Kho tri thức</span>
        <nav className="kn-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={view === t.id && !jobId ? "active" : ""}
              onClick={() => {
                if (t.id === "projects") setProjectId(null);
                go(t.id);
              }}
            >
              {t.label}
              {t.id === "queue" && pending > 0 && <span className="kq-tabnum">{pending}</span>}
            </button>
          ))}
        </nav>
      </header>
      {error && (
        <div className="banner-error" role="alert" onClick={() => setError(null)} title="Bấm để ẩn">
          {error}
        </div>
      )}
      <div className="kn-body">
        {jobId ? (
          <JobReview
            jobId={jobId}
            userId={userId}
            options={options}
            projects={projects}
            onBack={() => {
              setJobId(null);
              void reload();
            }}
            onError={onError}
          />
        ) : view === "projects" ? (
          projectId ? (
            <ProjectDetail
              project={projects.find((p) => p.project_id === projectId) ?? null}
              node={tree.find((t) => t.project_id === projectId) ?? null}
              userId={userId}
              options={options}
              projects={projects}
              onBack={() => setProjectId(null)}
              onOpenQueue={() => go("queue")}
              onChanged={() => void reload()}
              onError={onError}
            />
          ) : (
            <ProjectsBoard
              tree={tree}
              loading={loading}
              onOpen={setProjectId}
              onCreated={(p) => {
                void reload().then(() => setProjectId(p.project_id));
              }}
              onError={onError}
            />
          )
        ) : (
          <JobQueue userId={userId} projects={projects} onOpen={setJobId} onChanged={() => void reload()} onError={onError} />
        )}
      </div>
    </main>
  );
}
