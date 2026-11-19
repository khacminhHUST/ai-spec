import Icon from "../Icon";
import { useCallback, useEffect, useState } from "react";
import {
  deleteSeries,
  fileDownloadUrl,
  filePreviewUrl,
  fmtDate,
  getSeries,
  setLatest,
  streamLabel,
  unpublishSeries,
  updateSeries,
  type KnowledgeOptions,
  type Project,
  type Revision,
  type SeriesHistory as History,
  type UploadPreset,
} from "../../knowledgeApi";
import Modal from "./Modal";

interface Props {
  docCode: string;
  userId: string;
  options: KnowledgeOptions | null;
  projects: Project[];
  onAddRevision: (preset: UploadPreset) => void;
  onChanged: () => void;
  onDeleted: () => void;
  onMoved?: () => void;
  onClose?: () => void;
  onError: (e: unknown) => void;
}

interface SeriesForm {
  project_id: string;
  stream: string;
  disciplines: string[];
}

/** Lịch sử revision của 1 bộ: lọc; xem PDF; thêm revision; sửa cả bộ (dự án, luồng, phân hệ – đổi dự án = chuyển
 *  tài liệu sang dự án khác); đặt hiệu lực, gỡ, xóa. */
export default function SeriesHistory({
  docCode,
  userId,
  options,
  projects,
  onAddRevision,
  onChanged,
  onDeleted,
  onMoved,
  onClose,
  onError,
}: Props) {
  const [data, setData] = useState<History | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [stage, setStage] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Revision | null>(null);
  const [seriesForm, setSeriesForm] = useState<SeriesForm | null>(null);

  const load = useCallback(() => {
    getSeries(docCode, { from, to, stage })
      .then(setData)
      .catch((e) => {
        setData(null);
        onError(e);
      });
  }, [docCode, from, to, stage, onError]);

  useEffect(load, [load]);
  useEffect(() => {
    setPreview(null);
    setSeriesForm(null);
  }, [docCode]);

  const act = async (f: () => Promise<unknown>, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return false;
    setBusy(true);
    try {
      await f();
      load();
      onChanged();
      return true;
    } catch (e) {
      onError(e);
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <div className="muted pad">Đang tải…</div>;
  const stages = Array.from(new Set(data.revisions.map((r) => r.stage).filter(Boolean))) as string[];
  const head = data.revisions.find((r) => r.is_latest) ?? data.revisions[0];

  const startEditSeries = () =>
    head &&
    setSeriesForm({
      project_id: head.project_id,
      stream: head.stream,
      disciplines: [...head.disciplines],
    });
  const saveSeries = async () => {
    if (!seriesForm || !head) return;
    const patch: Record<string, unknown> = {};
    const moving = seriesForm.project_id !== head.project_id;
    if (moving) patch.project_id = seriesForm.project_id;
    if (seriesForm.stream !== head.stream) patch.stream = seriesForm.stream;
    if (seriesForm.disciplines.join() !== head.disciplines.join()) patch.disciplines = seriesForm.disciplines;
    if (Object.keys(patch).length === 0) return setSeriesForm(null);
    if (moving) {
      const to = projects.find((p) => p.project_id === seriesForm.project_id)?.name ?? seriesForm.project_id;
      if (!window.confirm(`Chuyển ${data.n_revisions} revision của tài liệu này sang dự án "${to}"?`)) return;
    }
    if (await act(() => updateSeries(docCode, patch))) {
      setSeriesForm(null);
      if (moving) onMoved?.();
    }
  };
  const toggleDisc = (d: string) =>
    seriesForm &&
    setSeriesForm({
      ...seriesForm,
      disciplines: seriesForm.disciplines.includes(d)
        ? seriesForm.disciplines.filter((x) => x !== d)
        : [...seriesForm.disciplines, d],
    });

  const removeSeries = async () => {
    setBusy(true);
    try {
      if (!window.confirm(`Xóa cả bộ ${docCode} (${data.n_revisions} revision)? Không hoàn tác được; file gốc vẫn giữ.`))
        return;
      await deleteSeries(docCode);
      onDeleted();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kn-card kn-series">
      {onClose && (
        <button className="icon-btn kn-series-close" onClick={onClose} aria-label="Đóng">
          <Icon name="x" />
        </button>
      )}
      <div className="kn-row">
        <div className="kp-series-head">
          <strong className="kp-series-title">{head?.title || docCode}</strong>
          <div className="small muted">
            {head && `${streamLabel(head.stream)} · ${head.disciplines.join(", ") || "chưa có phân hệ"} · `}
            {data.n_revisions} revision{" "}
            {data.unpublished && <span className="kn-tag warn">Đã gỡ khỏi tìm kiếm – đặt bản hiệu lực để khôi phục</span>}
          </div>
        </div>
        <div className="kn-actions">
          <button
            className="kn-btn small primary"
            disabled={busy || !head}
            onClick={() => head && onAddRevision({ project_id: head.project_id, stream: head.stream, doc_code: docCode })}
          >
            + Thêm revision
          </button>
          <button className="kn-btn small" disabled={busy || !head} onClick={startEditSeries}>
            Sửa / chuyển dự án
          </button>
          {!data.unpublished && (
            <button
              className="kn-btn small"
              disabled={busy}
              onClick={() => act(() => unpublishSeries(docCode), "Gỡ mọi revision của bộ này khỏi tìm kiếm?")}
            >
              Gỡ khỏi tìm kiếm
            </button>
          )}
          <button className="kn-btn small danger" disabled={busy} onClick={removeSeries}>
            Xóa cả bộ
          </button>
        </div>
      </div>

      {seriesForm && (
        <div className="kn-card kn-inline kn-form">
          <strong>Sửa thông tin tài liệu (áp cho cả {data.n_revisions} revision)</strong>
          <div className="kn-grid">
            <label className="kn-field">
              <span>Dự án</span>
              <select value={seriesForm.project_id} onChange={(e) => setSeriesForm({ ...seriesForm, project_id: e.target.value })}>
                {projects.map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="kn-field">
              <span>Luồng</span>
              <select value={seriesForm.stream} onChange={(e) => setSeriesForm({ ...seriesForm, stream: e.target.value })}>
                {(options?.streams ?? []).map((s) => (
                  <option key={s.value} value={s.value} title={s.label}>
                    {streamLabel(s.value)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="kn-field">
            <span>Phân hệ (phân hệ đầu tiên là phân hệ chính)</span>
            <div className="kn-checks">
              {(options?.disciplines ?? []).map((d) => (
                <label key={d}>
                  <input type="checkbox" checked={seriesForm.disciplines.includes(d)} onChange={() => toggleDisc(d)} /> {d}
                </label>
              ))}
            </div>
          </div>
          <div className="kn-row">
            <span className="small muted">Đổi dự án = chuyển cả bộ sang dự án đó.</span>
            <span className="kn-actions">
              <button className="kn-btn small" disabled={busy} onClick={() => setSeriesForm(null)}>
                Hủy
              </button>
              <button
                className="kn-btn small primary"
                disabled={busy || seriesForm.disciplines.length === 0}
                onClick={saveSeries}
              >
                Lưu
              </button>
            </span>
          </div>
        </div>
      )}

      <div className="kn-filters">
        <label>
          Ban hành từ <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          đến <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label>
          Giai đoạn{" "}
          <select value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="">(tất cả)</option>
            {stages.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <ul className="kn-revs">
        {data.revisions.length === 0 && <li className="muted pad">Không có revision khớp bộ lọc</li>}
        {data.revisions.map((r) => (
          <li key={r.doc_id} className={r.is_latest ? "kn-rev kn-latest" : "kn-rev"}>
            <div className="kn-rev-main">
              <span className="kn-rev-line">
                <strong>Rev {r.revision ?? "?"}</strong>
                {r.is_latest && <span className="kn-tag ok">Hiệu lực</span>}
                <span className="small muted">
                  {fmtDate(r.issue_date)}
                  {r.stage && ` · ${r.stage}`}
                </span>
              </span>
              <span className="small muted kn-rev-title">
                {r.title ?? "–"}
                {r.issuer && ` · ${r.issuer}`}
              </span>
            </div>
            <span className="kn-actions">
              <button className="kn-btn small" onClick={() => setPreview(r)}>
                Xem
              </button>
              <a
                className="kn-btn small"
                href={fileDownloadUrl(r.doc_id)}
                download={r.file_name || `${r.title || r.doc_id}.pdf`}
              >
                Tải xuống
              </a>
              {!r.is_latest && (
                <button
                  className="kn-btn small"
                  disabled={busy}
                  onClick={() => act(() => setLatest(docCode, r.doc_id, userId))}
                >
                  Đặt hiệu lực
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>

      {preview && (
        <Modal
          title={`${head?.title || docCode} – Rev ${preview.revision ?? "?"}`}
          onClose={() => setPreview(null)}
          className="kp-modal-pdf"
        >
          <PdfFrame docId={preview.doc_id} />
        </Modal>
      )}
    </div>
  );
}

/** Khung xem PDF: tải file thành blob rồi hiển thị (trình duyệt không tự tải xuống). */
function PdfFrame({ docId }: { docId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    setUrl(null);
    setErr(null);
    filePreviewUrl(docId)
      .then((u) => {
        made = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      })
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [docId]);

  if (err) return <div className="kn-pdf kn-pdf-msg">{err}</div>;
  if (!url) return <div className="kn-pdf kn-pdf-msg muted">Đang tải PDF…</div>;
  return <iframe className="kn-pdf" src={url} title="Xem PDF" />;
}
