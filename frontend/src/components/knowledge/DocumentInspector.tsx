import Icon from "../Icon";
import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  addStoredChild,
  canEditKnowledge,
  deleteStoredChild,
  deleteStoredSection,
  fmtDate,
  fmtTime,
  getStored,
  getStoredSection,
  getStoredSections,
  streamLabel,
  updateDocument,
  updateStoredChild,
  updateStoredSection,
  type DocumentPatch,
  type StoredDetail,
  type StoredSection,
  type StoredSectionDetail,
} from "../../knowledgeApi";

interface Props {
  docId: string;
  userId: string;
  onError: (e: unknown) => void;
  /** Dữ liệu đã đổi (metadata) → bên ngoài tải lại lịch sử revision. */
  onChanged?: () => void;
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

const numOrNull = (s: string) => (s.trim() === "" ? null : Number(s));
const pageText = (a: number | null | undefined, b: number | null | undefined) =>
  `tr.${a ?? "?"}${b && b !== a ? `–${b}` : ""}`;

/** Dữ liệu đã lưu của 1 revision – đọc thẳng Postgres (documents, parent_chunks) và Qdrant (payload child),
 *  để soát và sửa lỗi sau xử lý AI: metadata, kiểm tra đồng bộ, cây mục, nội dung parent và từng child. */
export default function DocumentInspector({ docId, userId, onError, onChanged }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [detail, setDetail] = useState<StoredDetail | null>(null);
  const canEdit = canEditKnowledge(userId);

  const load = useCallback(() => {
    getStored(docId).then(setDetail).catch(onError);
  }, [docId, onError]);
  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);

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
      {tab === "overview" ? (
        <Overview d={detail} canEdit={canEdit} onError={onError}
          onSaved={() => { load(); onChanged?.(); }} />
      ) : (
        <Sections docId={docId} userId={userId} canEdit={canEdit} langs={detail.langs.map((l) => l.lang)}
          onError={onError} onDataChanged={load} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- tổng quan

function Overview({ d, canEdit, onError, onSaved }: {
  d: StoredDetail; canEdit: boolean; onError: (e: unknown) => void; onSaved: () => void;
}) {
  const doc = d.document;
  const job = d.job;
  const cls = job?.report?.classification;
  const [form, setForm] = useState<Required<{ [K in keyof DocumentPatch]: string }> | null>(null);
  const [busy, setBusy] = useState(false);

  const startEdit = () =>
    setForm({
      title: doc.title ?? "",
      issuer: doc.issuer ?? "",
      revision: doc.revision ?? "",
      issue_date: doc.issue_date ?? "",
      stage: doc.stage ?? "",
    });
  const save = async () => {
    if (!form) return;
    const patch: DocumentPatch = {};
    (Object.keys(form) as (keyof DocumentPatch)[]).forEach((k) => {
      const v = form[k].trim();
      if (v !== (doc[k] ?? "")) patch[k] = v || null;
    });
    if (Object.keys(patch).length === 0) return setForm(null);
    setBusy(true);
    try {
      await updateDocument(doc.doc_id, patch);
      setForm(null);
      onSaved();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

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
        <div className="kn-row ki-head">
          <h4>Metadata (bảng documents)</h4>
          {canEdit && !form && (
            <button className="kn-btn small" onClick={startEdit}>
              <Icon name="pencil" size={14} /> Sửa metadata
            </button>
          )}
        </div>
        {form && (
          <div className="kn-card kn-inline kn-form">
            <div className="kn-grid">
              {([
                ["title", "Tiêu đề", "text"],
                ["issuer", "Đơn vị ban hành", "text"],
                ["revision", "Revision", "text"],
                ["issue_date", "Ngày ban hành", "date"],
                ["stage", "Giai đoạn", "text"],
              ] as const).map(([k, label, type]) => (
                <label key={k} className={"kn-field" + (k === "title" ? " ki-wide" : "")}>
                  <span>{label}</span>
                  <input type={type} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
                </label>
              ))}
            </div>
            <div className="kn-row">
              <span className="small muted">Dự án, luồng, phân hệ: sửa bằng "Sửa / chuyển dự án" của cả bộ.</span>
              <span className="kn-actions">
                <button className="kn-btn small" disabled={busy} onClick={() => setForm(null)}>Hủy</button>
                <button className="kn-btn small primary" disabled={busy} onClick={save}>{busy ? "Đang lưu…" : "Lưu"}</button>
              </span>
            </div>
          </div>
        )}
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

// ---------------------------------------------------------------- danh sách mục

function Sections({ docId, userId, canEdit, langs, onError, onDataChanged }: {
  docId: string; userId: string; canEdit: boolean; langs: string[];
  onError: (e: unknown) => void; onDataChanged: () => void;
}) {
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

  const loadRows = useCallback(() => {
    getStoredSections(docId, query)
      .then(setRows)
      .catch((e) => {
        setRows([]);
        onError(e);
      });
  }, [docId, query, onError]);
  useEffect(() => {
    setRows(null);
    loadRows();
  }, [loadRows]);

  const open = (s: StoredSection) => {
    setLoading(true);
    getStoredSection(docId, s.lang, s.section_key)
      .then(setSel)
      .catch(onError)
      .finally(() => setLoading(false));
  };

  /** Sau khi sửa: cập nhật mục đang xem, số child ở danh sách, thống kê/kiểm tra ở tab Tổng quan. */
  const saved = (d: StoredSectionDetail | null) => {
    setSel(d);
    loadRows();
    onDataChanged();
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
                {pageText(s.page_start, s.page_end)} · {s.n_children} child
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="ki-detail" aria-live="polite">
        {sel ? (
          <SectionDetail key={selKey} d={sel} docId={docId} userId={userId} canEdit={canEdit} loading={loading}
            onBack={() => setSel(null)} onSaved={saved} onError={onError} />
        ) : (
          <div className="muted pad">{loading ? "Đang tải…" : "Chọn một mục để xem parent (Postgres) và các child (Qdrant)."}</div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- 1 mục: xem + sửa

interface ParentForm {
  number: string;
  title: string;
  type: "SECTION" | "TABLE";
  page_start: string;
  page_end: string;
  breadcrumb: string;
  content_md: string;
}
interface ChildForm {
  raw_text: string;
  topic: string;
  page_start: string;
  page_end: string;
}

function SectionDetail({ d, docId, userId, canEdit, loading, onBack, onSaved, onError }: {
  d: StoredSectionDetail; docId: string; userId: string; canEdit: boolean; loading: boolean;
  onBack: () => void; onSaved: (d: StoredSectionDetail | null) => void; onError: (e: unknown) => void;
}) {
  const [raw, setRaw] = useState(false);
  const [pForm, setPForm] = useState<ParentForm | null>(null);
  const [cEdit, setCEdit] = useState<{ id: string | "new"; form: ChildForm } | null>(null);
  const [busy, setBusy] = useState(false);
  const p = d.parent;
  const lang = p.lang as string;
  const key = p.section_key as string;

  const run = async (f: () => Promise<StoredSectionDetail | null>, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      onSaved(await f());
      setPForm(null);
      setCEdit(null);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  const editParent = () =>
    setPForm({
      number: p.number ?? "",
      title: p.title ?? "",
      type: p.type,
      page_start: p.page_start?.toString() ?? "",
      page_end: p.page_end?.toString() ?? "",
      breadcrumb: p.breadcrumb ?? "",
      content_md: p.content_md ?? "",
    });
  const saveParent = () =>
    pForm &&
    run(() =>
      updateStoredSection(docId, lang, key, {
        number: pForm.number.trim() || null,
        title: pForm.title.trim() || null,
        type: pForm.type,
        page_start: numOrNull(pForm.page_start),
        page_end: numOrNull(pForm.page_end),
        breadcrumb: pForm.breadcrumb,
        content_md: pForm.content_md,
      }, userId),
    );
  const removeParent = () =>
    run(
      async () => {
        await deleteStoredSection(docId, lang, key, userId);
        return null;
      },
      `Xóa mục "${p.number ?? ""} ${p.title ?? key}" và ${d.children.length} child? Không hoàn tác được.`,
    );

  const childForm = (c?: StoredSectionDetail["children"][number]): ChildForm => ({
    raw_text: c?.raw_text ?? "",
    topic: c?.topic ?? "",
    page_start: (c?.page_start ?? p.page_start)?.toString() ?? "",
    page_end: (c?.page_end ?? p.page_end)?.toString() ?? "",
  });
  const saveChild = () => {
    if (!cEdit) return;
    const body = {
      raw_text: cEdit.form.raw_text,
      topic: cEdit.form.topic.trim() || null,
      page_start: numOrNull(cEdit.form.page_start),
      page_end: numOrNull(cEdit.form.page_end),
    };
    return run(() =>
      cEdit.id === "new"
        ? addStoredChild(docId, lang, key, body, userId)
        : updateStoredChild(docId, lang, cEdit.id, body, userId),
    );
  };

  const childEditor = cEdit && (
    <div className="kn-card kn-inline kn-form ki-edit">
      <strong>{cEdit.id === "new" ? "Thêm child" : "Sửa child"}</strong>
      <label className="kn-field">
        <span>Nội dung (raw_text) – lưu sẽ dựng lại text embed và embed lại</span>
        <textarea className="ki-textarea" rows={8} value={cEdit.form.raw_text}
          onChange={(e) => setCEdit({ ...cEdit, form: { ...cEdit.form, raw_text: e.target.value } })} />
      </label>
      {cEdit.id === "new" && (
        <button className="kn-link small ki-fill" type="button"
          onClick={() => setCEdit({ ...cEdit, form: { ...cEdit.form, raw_text: p.content_md } })}>
          Lấy toàn bộ nội dung mục
        </button>
      )}
      <div className="kn-grid">
        <label className="kn-field">
          <span>Chủ đề (topic)</span>
          <input value={cEdit.form.topic} onChange={(e) => setCEdit({ ...cEdit, form: { ...cEdit.form, topic: e.target.value } })} />
        </label>
        <label className="kn-field">
          <span>Trang đầu</span>
          <input inputMode="numeric" value={cEdit.form.page_start}
            onChange={(e) => setCEdit({ ...cEdit, form: { ...cEdit.form, page_start: e.target.value.replace(/\D/g, "") } })} />
        </label>
        <label className="kn-field">
          <span>Trang cuối</span>
          <input inputMode="numeric" value={cEdit.form.page_end}
            onChange={(e) => setCEdit({ ...cEdit, form: { ...cEdit.form, page_end: e.target.value.replace(/\D/g, "") } })} />
        </label>
      </div>
      <div className="kn-row ki-form-actions">
        <button className="kn-btn small" disabled={busy} onClick={() => setCEdit(null)}>Hủy</button>
        <button className="kn-btn small primary" disabled={busy || !cEdit.form.raw_text.trim()} onClick={saveChild}>
          {busy ? "Đang lưu & embed…" : "Lưu"}
        </button>
      </div>
    </div>
  );

  return (
    <div className={"ki-sec" + (loading || busy ? " ki-loading" : "")}>
      <button className="kn-link ki-back" onClick={onBack}>
        <Icon name="arrow-left" size={16} /> Danh sách mục
      </button>
      <div className="small muted">{p.breadcrumb || key}</div>
      <div className="kn-row ki-head">
        <strong>
          Parent · {p.type} {p.number ?? ""} {p.title ?? ""}
        </strong>
        <span className="kn-actions">
          {!pForm && (
            <span className="ki-seg" role="group" aria-label="Kiểu hiển thị">
              <button className={!raw ? "active" : ""} aria-pressed={!raw} onClick={() => setRaw(false)}>Hiển thị</button>
              <button className={raw ? "active" : ""} aria-pressed={raw} onClick={() => setRaw(true)}>Markdown gốc</button>
            </span>
          )}
          {canEdit && !pForm && (
            <>
              <button className="kn-btn small" disabled={busy} onClick={editParent}>
                <Icon name="pencil" size={14} /> Sửa mục
              </button>
              <button className="kn-btn small danger" disabled={busy} onClick={removeParent}>Xóa mục</button>
            </>
          )}
        </span>
      </div>

      {pForm ? (
        <div className="kn-card kn-inline kn-form ki-edit">
          <div className="kn-grid">
            <label className="kn-field">
              <span>Số mục</span>
              <input value={pForm.number} onChange={(e) => setPForm({ ...pForm, number: e.target.value })} />
            </label>
            <label className="kn-field ki-wide">
              <span>Tiêu đề</span>
              <input value={pForm.title} onChange={(e) => setPForm({ ...pForm, title: e.target.value })} />
            </label>
            <label className="kn-field">
              <span>Loại</span>
              <select value={pForm.type} onChange={(e) => setPForm({ ...pForm, type: e.target.value as ParentForm["type"] })}>
                <option value="SECTION">Mục (SECTION)</option>
                <option value="TABLE">Bảng (TABLE)</option>
              </select>
            </label>
            <label className="kn-field">
              <span>Trang đầu</span>
              <input inputMode="numeric" value={pForm.page_start}
                onChange={(e) => setPForm({ ...pForm, page_start: e.target.value.replace(/\D/g, "") })} />
            </label>
            <label className="kn-field">
              <span>Trang cuối</span>
              <input inputMode="numeric" value={pForm.page_end}
                onChange={(e) => setPForm({ ...pForm, page_end: e.target.value.replace(/\D/g, "") })} />
            </label>
          </div>
          <label className="kn-field">
            <span>Breadcrumb</span>
            <input value={pForm.breadcrumb} onChange={(e) => setPForm({ ...pForm, breadcrumb: e.target.value })} />
          </label>
          <label className="kn-field">
            <span>Nội dung (Markdown) – agent đọc nội dung này khi trả lời</span>
            <textarea className="ki-textarea" rows={14} value={pForm.content_md}
              onChange={(e) => setPForm({ ...pForm, content_md: e.target.value })} />
          </label>
          <div className="kn-row">
            <span className="small muted">Chỉ sửa mục (Postgres). Child dùng để tìm kiếm sửa riêng ở dưới.</span>
            <span className="kn-actions">
              <button className="kn-btn small" disabled={busy} onClick={() => setPForm(null)}>Hủy</button>
              <button className="kn-btn small primary" disabled={busy || !pForm.content_md.trim()} onClick={saveParent}>
                {busy ? "Đang lưu…" : "Lưu"}
              </button>
            </span>
          </div>
        </div>
      ) : raw ? (
        <pre className="ki-pre">{p.content_md}</pre>
      ) : (
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

      <div className="kn-row ki-head ki-h">
        <h4>{d.children.length} child trên Qdrant</h4>
        {canEdit && cEdit?.id !== "new" && (
          <button className="kn-btn small" disabled={busy} onClick={() => setCEdit({ id: "new", form: childForm() })}>
            <Icon name="plus" size={14} /> Thêm child
          </button>
        )}
      </div>
      {d.children.length === 0 && (
        <p className="ki-check warn small">Mục này không có child → không tìm được bằng tìm kiếm vector/BM25.</p>
      )}
      {cEdit?.id === "new" && childEditor}
      {d.children.map((c) => (
        <article key={c.child_id} className="ki-child">
          <div className="ki-child-head small">
            <span className="kn-code">#{c.child_id.split("#").pop()}</span>
            <span className="kn-tag">{c.type}</span>
            {c.topic && <span className="muted">{c.topic}</span>}
            <span className="muted">{pageText(c.page_start, c.page_end)}</span>
            {canEdit && cEdit?.id !== c.child_id && (
              <span className="kn-actions ki-child-actions">
                <button className="kn-btn small" disabled={busy}
                  onClick={() => setCEdit({ id: c.child_id, form: childForm(c) })}>Sửa</button>
                <button className="kn-btn small danger" disabled={busy}
                  onClick={() => run(() => deleteStoredChild(docId, lang, c.child_id, userId),
                    `Xóa child #${c.child_id.split("#").pop()}? Không hoàn tác được.`)}>Xóa</button>
              </span>
            )}
          </div>
          {cEdit?.id === c.child_id ? childEditor : <pre className="ki-pre">{c.raw_text}</pre>}
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
