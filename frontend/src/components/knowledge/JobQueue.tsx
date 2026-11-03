import { useCallback, useEffect, useState } from "react";
import { ACTIVE_STATUSES, fmtTime, listJobs, STATUS_LABEL, type Job, type JobStatus } from "../../knowledgeApi";

interface Props {
  onOpen: (jobId: string) => void;
  onError: (e: unknown) => void;
}

const POLL_MS = 3000;

/** Hàng chờ xử lý: trạng thái + bước hiện tại; tự làm mới khi có job đang chạy. */
export default function JobQueue({ onOpen, onError }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState("");

  const load = useCallback(() => {
    listJobs(status || undefined)
      .then(setJobs)
      .catch(onError);
  }, [status, onError]);

  useEffect(load, [load]);
  const running = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [running, load]);

  return (
    <div className="kn-card">
      <div className="kn-row">
        <strong>Hàng chờ xử lý</strong>
        <span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">(mọi trạng thái)</option>
            {(Object.keys(STATUS_LABEL) as JobStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>{" "}
          <button className="kn-btn small" onClick={load}>
            Làm mới
          </button>
        </span>
      </div>
      <table className="kn-table">
        <thead>
          <tr>
            <th>File</th>
            <th>Dự án / CT</th>
            <th>Bộ tài liệu</th>
            <th>Trạng thái</th>
            <th>Người upload</th>
            <th>Cập nhật</th>
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                Chưa có job
              </td>
            </tr>
          )}
          {jobs.map((j) => (
            <tr key={j.job_id} className="kn-click" onClick={() => onOpen(j.job_id)}>
              <td title={j.job_id}>{j.file_name}</td>
              <td>
                {j.project_id} / {j.building}
              </td>
              <td className="small">
                {j.series_doc_code} {j.revision && `· Rev ${j.revision}`}
              </td>
              <td>
                <span className={`kn-status ${j.status.toLowerCase()}`}>{STATUS_LABEL[j.status]}</span>
                {j.step && ACTIVE_STATUSES.includes(j.status) && <span className="small muted"> · {j.step}</span>}
                {j.error && (
                  <div className="small kn-err" title={j.error}>
                    {j.error.slice(0, 80)}
                  </div>
                )}
              </td>
              <td>{j.uploaded_by ?? "–"}</td>
              <td className="small">{fmtTime(j.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
