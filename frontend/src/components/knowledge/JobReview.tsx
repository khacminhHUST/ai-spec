import Icon from "../Icon";
import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ACTIVE_STATUSES,
  fmtDate,
  fmtTime,
  getJob,
  getSection,
  getSections,
  jobAction,
  STATUS_LABEL,
  type Job,
  type SectionPreview,
  type SectionRow,
} from "../../knowledgeApi";

interface Props {
  jobId: string;
  userId: string;
  onBack: () => void;
  onError: (e: unknown) => void;
}

const POLL_MS = 3000;

/** Trang duyệt job (mục 2.3): metadata, thống kê report, cảnh báo, cây mục + xem trước, Phát hành / Hủy / Chạy lại. */
export default function JobReview({ jobId, userId, onBack, onError }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [sections, setSections] = useState<SectionRow[]>([]);
  const [lang, setLang] = useState("vi");
  const [preview, setPreview] = useState<SectionPreview | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getJob(jobId)
      .then((j) => {
        setJob(j);
        if (j.status === "DRAFT" || j.status === "PUBLISHED") {
          getSections(jobId)
            .then((s) => {
              setSections(s);
              if (s.length && !s.some((x) => x.lang === "vi")) setLang(s[0].lang);
            })
            .catch(() => setSections([]));
        } else setSections([]);
      })
      .catch(onError);
  }, [jobId, onError]);

  useEffect(load, [load]);
  const running = job ? ACTIVE_STATUSES.includes(job.status) : false;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [running, load]);

  const act = async (action: "publish" | "cancel" | "retry") => {
    if (action === "cancel" && !window.confirm("Hủy job? Kết quả nháp sẽ bị xóa (giữ file gốc).")) return;
    setBusy(true);
    try {
      setJob(await jobAction(jobId, action, userId));
      setPreview(null);
      load();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const open = (s: SectionRow) => getSection(jobId, s.lang, s.section_key).then(setPreview).catch(onError);

  if (!job) return <div className="muted pad">Đang tải…</div>;
  const rep = job.report ?? null;
  const md = (rep?.metadata ?? {}) as Record<string, any>;
  const langs: [string, Record<string, any>][] = rep ? Object.entries(rep.langs ?? {}) : [];
  const warnings: string[] = rep?.warnings ?? job.warnings ?? [];
  const langList = Array.from(new Set(sections.map((s) => s.lang)));

  return (
    <div className="kn-review">
      <div className="kn-row">
        <button className="kn-link" onClick={onBack}>
          <Icon name="arrowLeft" size={16} /> Quay lại
        </button>
        <span>
          <span className={`kn-status ${job.status.toLowerCase()}`}>{STATUS_LABEL[job.status]}</span>
          {job.step && running && <span className="small muted"> · bước {job.step}</span>}
        </span>
        <span className="kn-actions">
          {job.status === "DRAFT" && (
            <button className="kn-btn primary" disabled={busy} onClick={() => act("publish")}>
              Phát hành
            </button>
          )}
          {job.status === "FAILED" && (
            <button className="kn-btn" disabled={busy} onClick={() => act("retry")}>
              Chạy lại
            </button>
          )}
          {["QUEUED", "DRAFT", "FAILED"].includes(job.status) && (
            <button className="kn-btn danger" disabled={busy} onClick={() => act("cancel")}>
              Hủy
            </button>
          )}
        </span>
      </div>
      {job.error && <div className="banner-error">{job.error}</div>}

      <div className="kn-card">
        <h3>{job.file_name}</h3>
        <dl className="kn-meta">
          <dt>Dự án / công trình</dt>
          <dd>
            {job.project_id} / {job.building}
          </dd>
          <dt>Luồng · phân hệ · loại</dt>
          <dd>
            {job.stream} · {job.disciplines.join(", ")} · {job.doc_type}
          </dd>
          <dt>Bộ tài liệu (doc_code)</dt>
          <dd className="kn-code">{job.series_doc_code}</dd>
          <dt>Bản sẽ bị thay</dt>
          <dd>
            {job.replaces
              ? `Rev ${job.replaces.revision ?? "?"} (${fmtDate(job.replaces.issue_date)}) – nếu bản mới mới hơn`
              : "(không có – bộ mới hoặc đang gỡ)"}
          </dd>
          <dt>Revision · ngày ban hành · giai đoạn</dt>
          <dd>
            {md.revision ?? job.revision ?? "?"} · {fmtDate(md.issue_date ?? job.issue_date)} · {md.stage ?? job.stage ?? "?"}
          </dd>
          <dt>Tiêu đề · đơn vị ban hành</dt>
          <dd>
            {md.title ?? job.title ?? "?"} · {md.issuer ?? job.issuer ?? "?"}
          </dd>
          <dt>Upload</dt>
          <dd>
            {job.uploaded_by ?? "?"} · {fmtTime(job.created_at)}
          </dd>
          {job.published_at && (
            <>
              <dt>Phát hành</dt>
              <dd>
                {job.published_by ?? "?"} · {fmtTime(job.published_at)} · {job.doc_id}
              </dd>
            </>
          )}
        </dl>
      </div>

      {rep && (
        <div className="kn-card">
          <h3>Thống kê</h3>
          <table className="kn-table">
            <thead>
              <tr>
                <th>Ngôn ngữ</th>
                <th>Mục</th>
                <th>Bảng</th>
                <th>Parent</th>
                <th>Child (text / bảng)</th>
                <th>Toàn vẹn</th>
                <th>Child quá dài</th>
              </tr>
            </thead>
            <tbody>
              {langs.map(([lg, x]) => (
                <tr key={lg}>
                  <td>{lg.toUpperCase()}</td>
                  <td>{x.n_sections}</td>
                  <td>{x.n_tables}</td>
                  <td>{x.n_parents}</td>
                  <td>
                    {x.n_children} ({x.n_children_text} / {x.n_children_table})
                  </td>
                  <td className={x.integrity_fail?.length ? "kn-err" : ""}>
                    {x.integrity_pass} đạt{x.integrity_fail?.length ? `, ${x.integrity_fail.length} lỗi` : ""}
                  </td>
                  <td>{x.oversized_children?.length ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small">
            Kiểm tra trang: {rep.page_check?.ok ?? "?"}/{rep.page_check?.sampled ?? "?"} đạt · Song ngữ:{" "}
            {rep.language?.bilingual ? "có" : "không"} · OCR: {rep.parse?.ocr ? "có" : "không"} · {rep.parse?.n_blocks ?? "?"} block
          </p>
          {warnings.length > 0 && (
            <details>
              <summary>{warnings.length} cảnh báo</summary>
              <ul className="small kn-warnings">
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {sections.length > 0 && (
        <div className="kn-split">
          <section className="kn-tree">
            <div className="kn-row">
              <strong>Cây mục</strong>
              <span>
                {langList.map((l) => (
                  <button key={l} className={"kn-btn small" + (l === lang ? " primary" : "")} onClick={() => setLang(l)}>
                    {l.toUpperCase()}
                  </button>
                ))}
              </span>
            </div>
            {sections
              .filter((s) => s.lang === lang)
              .map((s) => (
                <button
                  key={s.section_key}
                  className={"kn-section" + (s.type === "TABLE" ? " table" : "") +
                    (preview?.parent.section_key === s.section_key ? " active" : "")}
                  onClick={() => open(s)}
                  title={s.section_key}
                >
                  <span>
                    {s.type === "TABLE" ? "▦ " : ""}
                    {s.number ?? ""} {s.title ?? s.section_key}
                  </span>
                  <span className="small muted">
                    tr.{s.page_start ?? "?"}
                    {s.page_end && s.page_end !== s.page_start ? `–${s.page_end}` : ""} · {s.n_children}
                  </span>
                </button>
              ))}
          </section>
          <section className="kn-detail">
            {preview ? (
              <div className="kn-card">
                <div className="small muted">{preview.parent.breadcrumb}</div>
                <div className="md">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.parent.content_md}</ReactMarkdown>
                </div>
                <h4>{preview.children.length} child</h4>
                {preview.children.map((c) => (
                  <div key={c.child_id} className="kn-child">
                    <div className="small muted">
                      {c.child_id.split("#").pop()} · {c.type} {c.topic && `· ${c.topic}`} · tr.{c.page_start}
                    </div>
                    <pre>{c.raw_text}</pre>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted pad">Chọn một mục để xem trước nội dung (parent + các child).</div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
