import Icon from "../Icon";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  fmtDate,
  fmtTime,
  getStored,
  getStoredSection,
  getStoredSections,
  streamLabel,
  type StoredDetail,
  type StoredSection,
  type StoredSectionDetail,
} from "../../knowledgeApi";

interface Props {
  docId: string;
  onError: (e: unknown) => void;
}

type Tab = "overview" | "sections";

const DOC_FIELDS: [string, string][] = [
  ["doc_id", "doc_id"],
  ["doc_code", "Bộ tài liệu (doc_code)"],
  ["title", "Tiêu đề"],
  ["issuer", "Đơn vị ban hành"],
  ["revision", "Revision"],
  ["issue_date", "Ngày ban hành"],
  ["stage", "Giai đoạn"],
  ["project_id", "Dự án"],
  ["building", "Công trình"],
  ["stream", "Luồng"],
  ["disciplines", "Phân hệ"],
  ["languages", "Ngôn ngữ"],
  ["is_bilingual", "Song ngữ"],
  ["is_latest", "Bản hiệu lực"],
  ["file_hash", "file_hash"],
  ["ingested_at", "Lưu vào hệ thống"],
];

const PARENT_SKIP = new Set(["content_md", "doc_id", "parent_id"]);
const CHILD_SKIP = new Set(["text", "raw_text"]);

function show(key: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "–";
  if (typeof v === "boolean") return v ? "có" : "không";
  if (key === "issue_date") return fmtDate(String(v));
  if (key === "ingested_at" || key.endsWith("_at")) return fmtTime(String(v));
  if (key === "stream") return `${streamLabel(String(v))} (${v})`;
  return String(v);
}

/** Dữ liệu đã lưu của 1 revision – đọc thẳng Postgres (documents, parent_chunks) và Qdrant (payload child),
 *  để soát lỗi sau xử lý AI: metadata, thống kê, kiểm tra đồng bộ, cây mục, nội dung parent và từng child. */
export default function DocumentInspector({ docId, onError }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [detail, setDetail] = useState<StoredDetail | null>(null);

  useEffect(() => {
    setDetail(null);
    getStored(docId).then(setDetail).catch(onError);
  }, [docId, onError]);

  if (!detail) return <div className="muted pad">Đang tải dữ liệu đã lưu…</div>;
  const errors = detail.checks.filter((c) => c.level === "error").length;

  return (
    <div className="ki">
      <div className="ki-tabs" role="tablist" aria-label="Dữ liệu đã lưu">
        <button role="tab" aria-selected={tab === "overview"} className={tab === "overview" ? "active" : ""}
          onClick={() => setTab("overview")}>
          Tổng quan
          {detail.checks.length > 0 && (
            <span className={`kn-tag ${errors ? "err" : "warn"}`}>{detail.checks.length}</span>
          )}
        </button>
        <button role="tab" aria-selected={tab === "sections"} className={tab === "sections" ? "active" : ""}
          onClick={() => setTab("sections")}>
          Mục & chunk
        </button>
      </div>
      {tab === "overview" ? <Overview d={detail} /> : <Sections docId={docId} langs={detail.langs.map((l) => l.lang)} onError={onError} />}
    </div>
  );
}

function Overview({ d }: { d: StoredDetail }) {
  const doc = d.document;
  const job = d.job;
  const cls = job?.report?.classification;
  return (
    <div className="ki-overview">
      <section className="ki-block" aria-label="Kiểm tra">
        <h4>Kiểm tra</h4>
        {d.checks.length === 0 ? (
          <p className="ki-check ok">
            <Icon name="check" size={16} /> Không phát hiện lỗi: metadata đủ, Postgres và Qdrant khớp nhau.
          </p>
        ) : (
          <ul className="ki-checks">
            {d.checks.map((c, i) => (
              <li key={i} className={`ki-check ${c.level}`}>
                <span className={`kn-tag ${c.level === "error" ? "err" : "warn"}`}>{c.level === "error" ? "Lỗi" : "Lưu ý"}</span>
                {c.message}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="ki-block" aria-label="Metadata">
        <h4>Metadata (bảng documents)</h4>
        <dl className="kn-meta">
          {DOC_FIELDS.map(([k, label]) => (
            <div key={k} className="ki-meta-row">
              <dt>{label}</dt>
              <dd className={k.endsWith("_id") || k === "file_hash" || k === "doc_code" ? "kn-code" : ""}>{show(k, doc[k])}</dd>
            </div>
          ))}
        </dl>
        {doc.warnings?.length > 0 && (
          <details className="ki-details">
            <summary>{doc.warnings.length} cảnh báo khi xử lý</summary>
            <ul className="small kn-warnings">
              {doc.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="ki-block" aria-label="Nguồn">
        <h4>Nguồn xử lý</h4>
        {job ? (
          <dl className="kn-meta">
            <div className="ki-meta-row"><dt>File</dt><dd>{job.file_name}</dd></div>
            <div className="ki-meta-row"><dt>Loại tài liệu</dt><dd>{job.doc_type ?? "–"}{cls?.source && <span className="small muted"> · phân loại: {cls.source}</span>}</dd></div>
            <div className="ki-meta-row"><dt>Upload</dt><dd>{job.uploaded_by ?? "?"} · {fmtTime(job.created_at)}</dd></div>
            <div className="ki-meta-row"><dt>Phát hành</dt><dd>{job.published_by ?? "?"} · {fmtTime(job.published_at)}</dd></div>
            <div className="ki-meta-row"><dt>Job</dt><dd className="kn-code">{job.job_id}</dd></div>
          </dl>
        ) : (
          <p className="small muted">Nạp bằng CLI (không qua hàng chờ) – không có thông tin upload.</p>
        )}
      </section>

      <section className="ki-block" aria-label="Thống kê">
        <h4>Thống kê đã lưu</h4>
        <div className="table-wrap">
          <table className="kn-table">
            <thead>
              <tr>
                <th>Ngôn ngữ</th>
                <th>Mục</th>
                <th>Bảng</th>
                <th>Token parent</th>
                <th>Child (text / bảng)</th>
                <th>Mục không child</th>
                <th>Child mồ côi</th>
                <th>Payload lệch</th>
              </tr>
            </thead>
            <tbody>
              {d.langs.map((l) => {
                const mis = Object.entries(l.payload_mismatch);
                return (
                  <tr key={l.lang}>
                    <td>{l.lang.toUpperCase()}</td>
                    <td>{l.n_sections}</td>
                    <td>{l.n_tables}</td>
                    <td>{l.parent_tokens.toLocaleString("vi-VN")}</td>
                    <td>
                      {l.n_children} ({l.n_children_text} / {l.n_children_table})
                    </td>
                    <td className={l.n_empty_parents ? "kn-warn" : ""} title={l.empty_parents.join(", ")}>
                      {l.n_empty_parents}
                    </td>
                    <td className={l.orphan_children ? "kn-err" : ""}>{l.orphan_children}</td>
                    <td className={mis.length ? "kn-err" : ""}>{mis.length ? mis.map(([f, n]) => `${f}: ${n}`).join(", ") : 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Sections({ docId, langs, onError }: { docId: string; langs: string[]; onError: (e: unknown) => void }) {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [lang, setLang] = useState(langs.includes("vi") ? "vi" : langs[0] ?? "vi");
  const [onlyEmpty, setOnlyEmpty] = useState(false);
  const [rows, setRows] = useState<StoredSection[] | null>(null);
  const [sel, setSel] = useState<StoredSectionDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    setRows(null);
    getStoredSections(docId, query)
      .then(setRows)
      .catch((e) => {
        setRows([]);
        onError(e);
      });
  }, [docId, query, onError]);

  const open = (s: StoredSection) => {
    setLoading(true);
    getStoredSection(docId, s.lang, s.section_key)
      .then(setSel)
      .catch(onError)
      .finally(() => setLoading(false));
  };

  const list = (rows ?? []).filter((r) => r.lang === lang && (!onlyEmpty || r.n_children === 0));
  const selKey = sel ? `${sel.parent.lang}/${sel.parent.section_key}` : null;

  return (
    <div className={"ki-split" + (sel ? " has-detail" : "")}>
      <section className="ki-list" aria-label="Danh sách mục">
        <div className="ki-search">
          <Icon name="search" size={16} />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Tìm số mục, tiêu đề, nội dung…"
            aria-label="Tìm trong tài liệu"
          />
        </div>
        <div className="ki-list-tools">
          <span className="ki-seg" role="group" aria-label="Ngôn ngữ">
            {langs.map((l) => (
              <button key={l} className={l === lang ? "active" : ""} aria-pressed={l === lang} onClick={() => setLang(l)}>
                {l.toUpperCase()}
              </button>
            ))}
          </span>
          <label className="small">
            <input type="checkbox" checked={onlyEmpty} onChange={(e) => setOnlyEmpty(e.target.checked)} /> Chỉ mục không child
          </label>
        </div>
        <div className="small muted ki-count">{rows === null ? "Đang tải…" : `${list.length} mục`}</div>
        <div className="ki-rows">
          {list.map((s) => (
            <button
              key={s.section_key}
              className={"kn-section" + (s.type === "TABLE" ? " table" : "") + (selKey === `${s.lang}/${s.section_key}` ? " active" : "")}
              onClick={() => open(s)}
              title={s.section_key}
            >
              <span className="ki-sec-title">
                {s.type === "TABLE" && <span className="kn-tag">Bảng</span>} {s.number ?? ""} {s.title ?? s.section_key}
              </span>
              <span className={"small ki-sec-meta" + (s.n_children === 0 ? " kn-warn" : " muted")}>
                tr.{s.page_start ?? "?"}
                {s.page_end && s.page_end !== s.page_start ? `–${s.page_end}` : ""} · {s.n_children} child
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="ki-detail" aria-live="polite">
        {sel ? (
          <SectionDetail d={sel} loading={loading} onBack={() => setSel(null)} />
        ) : (
          <div className="muted pad">{loading ? "Đang tải…" : "Chọn một mục để xem parent (Postgres) và các child (Qdrant)."}</div>
        )}
      </section>
    </div>
  );
}

function SectionDetail({ d, loading, onBack }: { d: StoredSectionDetail; loading: boolean; onBack: () => void }) {
  const [raw, setRaw] = useState(false);
  const p = d.parent;
  return (
    <div className={"ki-sec" + (loading ? " ki-loading" : "")}>
      <button className="kn-link ki-back" onClick={onBack}>
        <Icon name="arrow-left" size={16} /> Danh sách mục
      </button>
      <div className="small muted">{p.breadcrumb || p.section_key}</div>
      <div className="kn-row">
        <strong>
          Parent · {p.type} {p.number ?? ""} {p.title ?? ""}
        </strong>
        <span className="ki-seg" role="group" aria-label="Kiểu hiển thị">
          <button className={!raw ? "active" : ""} aria-pressed={!raw} onClick={() => setRaw(false)}>Hiển thị</button>
          <button className={raw ? "active" : ""} aria-pressed={raw} onClick={() => setRaw(true)}>Markdown gốc</button>
        </span>
      </div>
      {raw ? <pre className="ki-pre">{p.content_md}</pre> : (
        <div className="md ki-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{p.content_md}</ReactMarkdown>
        </div>
      )}
      <details className="ki-details">
        <summary>Các cột parent_chunks</summary>
        <dl className="kn-meta ki-kv">
          {Object.entries(p)
            .filter(([k]) => !PARENT_SKIP.has(k))
            .map(([k, v]) => (
              <div key={k} className="ki-meta-row">
                <dt className="kn-code">{k}</dt>
                <dd className="kn-code">{show(k, v)}</dd>
              </div>
            ))}
        </dl>
      </details>

      <h4 className="ki-h">{d.children.length} child trên Qdrant</h4>
      {d.children.length === 0 && (
        <p className="ki-check warn small">Mục này không có child → không tìm được bằng tìm kiếm vector/BM25.</p>
      )}
      {d.children.map((c) => (
        <article key={c.child_id} className="ki-child">
          <div className="ki-child-head small">
            <span className="kn-code">#{c.child_id.split("#").pop()}</span>
            <span className="kn-tag">{c.type}</span>
            {c.topic && <span className="muted">{c.topic}</span>}
            <span className="muted">tr.{c.page_start ?? "?"}{c.page_end && c.page_end !== c.page_start ? `–${c.page_end}` : ""}</span>
          </div>
          <pre className="ki-pre">{c.raw_text}</pre>
          <details className="ki-details">
            <summary>Text dùng để embed + payload</summary>
            <pre className="ki-pre small">{c.text}</pre>
            <dl className="kn-meta ki-kv">
              {Object.entries(c)
                .filter(([k]) => !CHILD_SKIP.has(k))
                .map(([k, v]) => (
                  <div key={k} className="ki-meta-row">
                    <dt className="kn-code">{k}</dt>
                    <dd className="kn-code">{show(k, v)}</dd>
                  </div>
                ))}
            </dl>
          </details>
        </article>
      ))}
    </div>
  );
}
