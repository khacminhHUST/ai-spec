import { useCallback, useEffect, useState } from "react";
import { getOptions, listProjects, type KnowledgeOptions, type Project } from "../../knowledgeApi";
import JobQueue from "./JobQueue";
import JobReview from "./JobReview";
import ProjectsView from "./ProjectsView";
import RepoView from "./RepoView";
import UploadForm from "./UploadForm";
import "./knowledge.css";

type View = "repo" | "upload" | "queue" | "projects";

const TABS: { id: View; label: string }[] = [
  { id: "repo", label: "Kho tài liệu" },
  { id: "upload", label: "Upload" },
  { id: "queue", label: "Hàng chờ xử lý" },
  { id: "projects", label: "Dự án" },
];

interface Props {
  userId: string;
  onClose: () => void;
}

/** Tab "Kho tri thức": điều hướng bằng state (không router). */
export default function KnowledgePage({ userId, onClose }: Props) {
  const [view, setView] = useState<View>("repo");
  const [jobId, setJobId] = useState<string | null>(null);
  const [options, setOptions] = useState<KnowledgeOptions | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);

  const onError = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), []);
  const reloadProjects = useCallback(() => {
    listProjects().then(setProjects).catch(onError);
  }, [onError]);

  useEffect(() => {
    getOptions().then(setOptions).catch(onError);
    reloadProjects();
  }, [onError, reloadProjects]);

  const go = (v: View) => {
    setError(null);
    setJobId(null);
    setView(v);
  };

  return (
    <main className="main kn">
      <header className="topbar">
        <span className="topbar-title">Kho tri thức</span>
        <nav className="kn-tabs">
          {TABS.map((t) => (
            <button key={t.id} className={view === t.id && !jobId ? "active" : ""} onClick={() => go(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <button className="kn-link" onClick={onClose}>
          ← Hỏi đáp
        </button>
      </header>
      {error && (
        <div className="banner-error" onClick={() => setError(null)} title="Bấm để ẩn">
          {error}
        </div>
      )}
      <div className="kn-body">
        {jobId ? (
          <JobReview jobId={jobId} userId={userId} onBack={() => setJobId(null)} onError={onError} />
        ) : view === "repo" ? (
          <RepoView userId={userId} onError={onError} />
        ) : view === "upload" ? (
          options && (
            <UploadForm
              userId={userId}
              options={options}
              projects={projects}
              onUploaded={() => go("queue")}
              onError={onError}
            />
          )
        ) : view === "queue" ? (
          <JobQueue onOpen={setJobId} onError={onError} />
        ) : (
          <ProjectsView projects={projects} streams={options?.streams ?? []} onChanged={reloadProjects} onError={onError} />
        )}
      </div>
    </main>
  );
}
