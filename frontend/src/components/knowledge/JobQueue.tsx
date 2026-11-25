import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ACTIVE_STATUSES,
  CANCEL_CONFIRM,
  canCancel,
  countJobs,
  fmtTime,
  jobAction,
  listJobs,
  STATUS_LABEL,
  streamLabel,
  type Job,
  type JobStatus,
  type Project,
} from "../../knowledgeApi";

interface Props {
  userId: string;
  projects: Project[];
  onOpen: (jobId: string) => void;
  onChanged?: () => void;
  onError: (e: unknown) => void;
}

const POLL_MS = 3000;

type Filter = "draft" | "running" | "failed" | "published" | "all";
// statuses: lọc ở server (danh sách tối đa 200 dòng); số trên nút lọc lấy từ /jobs/counts (toàn bộ hàng chờ)
const FILTERS: { id: Filter; label: string; statuses: JobStatus[] | null }[] = [
  { id: "draft", label: "Chờ duyệt", statuses: ["DRAFT"] },
  { id: "running", label: "Đang xử lý", statuses: ACTIVE_STATUSES },
  { id: "failed", label: "Lỗi", statuses: ["FAILED"] },
  { id: "published", label: "Đã phát hành", statuses: ["PUBLISHED"] },
  { id: "all", label: "Tất cả", statuses: null },
];

/** Hàng chờ xử lý: lọc nhanh theo trạng thái (mặc định Chờ duyệt), phát hành / chạy lại ngay trên dòng,
 *  chọn nhiều để phát hành hàng loạt; tự làm mới khi có job đang chạy. Bấm dòng → trang duyệt chi tiết. */
export default function JobQueue({ userId, projects, onOpen, onChanged, onError }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [byStatus, setByStatus] = useState<Partial<Record<JobStatus, number>> | null>(null);
  const [filter, setFilter] = useState<Filter | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null); // job_id đang thao tác / "bulk"
  const projectName = (id: string) => projects.find((p) => p.project_id === id)?.name ?? id;

  const counts = useMemo(() => {
    const n = (st: JobStatus[] | null) =>
      Object.entries(byStatus ?? {}).reduce((a, [k, v]) => a + (!st || st.includes(k as JobStatus) ? v ?? 0 : 0), 0);
    return Object.fromEntries(FILTERS.map((f) => [f.id, n(f.statuses)])) as Record<Filter, number>;
  }, [byStatus]);
  // lần đầu: có file chờ duyệt → mở thẳng "Chờ duyệt", không thì "Tất cả"
  const cur: Filter = filter ?? (byStatus && counts.draft === 0 ? "all" : "draft");
  const statuses = FILTERS.find((f) => f.id === cur)!.statuses;

  const load = useCallback(() => {
    Promise.all([countJobs(), listJobs(statuses?.join(","))])
      .then(([c, js]) => {
        setByStatus(c);
        setJobs(js);
      })
      .catch(onError);
  }, [onError, statuses]);

  useEffect(load, [load]);
  const running = counts.running > 0;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [running, load]);

  const shown = jobs;
  const drafts = shown.filter((j) => j.status === "DRAFT");
  const pickedDrafts = drafts.filter((j) => picked.has(j.job_id));

  const run = async (id: string, f: () => Promise<unknown>) => {
    setBusy(id);
    try {
      await f();
      load();
      onChanged?.();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(null);
    }
  };
  const publish = (j: Job) => run(j.job_id, () => jobAction(j.job_id, "publish", userId));
  const retry = (j: Job) => run(j.job_id, () => jobAction(j.job_id, "retry", userId));
  const cancel = (j: Job) => {
    if (!window.confirm(`${CANCEL_CONFIRM}\n\n${j.file_name}`)) return;
    return run(j.job_id, () => jobAction(j.job_id, "cancel", userId));
  };
  const publishPicked = () => {
    if (!window.confirm(`Phát hành ${pickedDrafts.length} tài liệu đã chọn vào kho tìm kiếm?`)) return;
    return run("bulk", async () => {
      for (const j of pickedDrafts) await jobAction(j.job_id, "publish", userId);
      setPicked(new Set());
    });
  };
  const toggle = (id: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const allPicked = drafts.length > 0 && pickedDrafts.length === drafts.length;

  return (
    <div className="kn-card kq">
      <div className="kn-row">
        <strong>Hàng chờ xử lý</strong>
        <button className="kn-btn small" onClick={load}>
          Làm mới
        </button>
      </div>
      <div className="kq-filters" role="tablist" aria-label="Lọc theo trạng thái">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            role="tab"
            aria-selected={cur === f.id}
            className={"kq-chip" + (cur === f.id ? " active" : "") + (f.id === "draft" && counts.draft ? " hot" : "")}
            onClick={() => {
              setFilter(f.id);
              setPicked(new Set());
            }}
          >
            {f.label}
            <span className="kq-num">{counts[f.id]}</span>
          </button>
        ))}
      </div>

      {drafts.length > 0 && (
        <div className="kq-bulk">
          <label className="kp-check">
            <input
              type="checkbox"
              checked={allPicked}
              onChange={() => setPicked(allPicked ? new Set() : new Set(drafts.map((j) => j.job_id)))}
            />
            Chọn tất cả {drafts.length} file chờ duyệt
          </label>
          <button
            className="kn-btn primary"
            disabled={pickedDrafts.length === 0 || busy !== null}
            onClick={publishPicked}
          >
            {busy === "bulk" ? "Đang phát hành…" : `Phát hành đã chọn (${pickedDrafts.length})`}
          </button>
        </div>
      )}

      <div className="table-wrap">
        <table className="kn-table stack">
          <thead>
            <tr>
              <th aria-label="Chọn"></th>
              <th>File</th>
              <th>Dự án · luồng</th>
              <th>Phân loại (AI)</th>
              <th>Trạng thái</th>
              <th>Cập nhật</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={7} className="muted stack-full">
                  {cur === "draft" ? "Không có file chờ duyệt" : "Không có file"}
                </td>
              </tr>
            )}
            {shown.map((j) => (
              <tr key={j.job_id} className="kn-click" onClick={() => onOpen(j.job_id)}>
                <td className="kq-pick" onClick={(e) => e.stopPropagation()}>
                  {j.status === "DRAFT" && (
                    <input
                      type="checkbox"
                      aria-label={`Chọn ${j.file_name}`}
                      checked={picked.has(j.job_id)}
                      onChange={() => toggle(j.job_id)}
                    />
                  )}
                </td>
                <td data-label="File" className="stack-title" title={j.job_id}>
                  {j.file_name}
                  {j.uploaded_by && <div className="small muted">{j.uploaded_by}</div>}
                </td>
                <td data-label="Dự án · luồng">
                  {projectName(j.project_id)}
                  <div className="small muted">{streamLabel(j.stream)}</div>
                </td>
                <td data-label="Phân loại (AI)" className="small">
                  {j.doc_type ?? "–"}
                  {j.disciplines.length > 0 && <div className="muted">{j.disciplines.join(", ")}</div>}
                </td>
                <td data-label="Trạng thái">
                  <span className={`kn-status ${j.status.toLowerCase()}`}>{STATUS_LABEL[j.status]}</span>
                  {j.step && ACTIVE_STATUSES.includes(j.status) && <span className="small muted"> · {j.step}</span>}
                  {j.error && (
                    <div className="small kn-err" title={j.error}>
                      {j.error.slice(0, 80)}
                    </div>
                  )}
                </td>
                <td data-label="Cập nhật" className="small">
                  {fmtTime(j.updated_at)}
                </td>
                <td className="kn-actions kq-actions" onClick={(e) => e.stopPropagation()}>
                  {j.status === "DRAFT" && (
                    <button className="kn-btn small primary" disabled={busy !== null} onClick={() => publish(j)}>
                      {busy === j.job_id ? "…" : "Phát hành"}
                    </button>
                  )}
                  {j.status === "FAILED" && (
                    <button className="kn-btn small" disabled={busy !== null} onClick={() => retry(j)}>
                      {busy === j.job_id ? "…" : "Chạy lại"}
                    </button>
                  )}
                  <button className="kn-btn small" onClick={() => onOpen(j.job_id)}>
                    {j.status === "DRAFT" ? "Xem & sửa" : "Xem"}
                  </button>
                  {canCancel(j) && (
                    <button className="kn-btn small danger" disabled={busy !== null} onClick={() => cancel(j)}>
                      Hủy
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {counts[cur] > shown.length && (
        <p className="small muted">
          Hiển thị {shown.length}/{counts[cur]} file
          {cur === "running" ? " (theo thứ tự xử lý – đang xử lý trước, rồi file chờ lâu nhất)" : " mới nhất"}.
        </p>
      )}
    </div>
  );
}
