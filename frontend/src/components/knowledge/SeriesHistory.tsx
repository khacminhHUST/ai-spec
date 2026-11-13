import { useCallback, useEffect, useState } from "react";
import {
  deleteRevision,
  deleteSeries,
  fileUrl,
  fmtDate,
  fmtTime,
  getSeries,
  setLatest,
  unpublishSeries,
  updateDocument,
  updateSeries,
  type KnowledgeOptions,
  type Project,
  type Revision,
  type SeriesHistory as History,
  type UploadPreset,
} from "../../knowledgeApi";

interface Props {
  docCode: string;
  userId: string;
  options: KnowledgeOptions | null;
  projects: Project[];
  onAddRevision: (preset: UploadPreset) => void;
  onChanged: () => void;
  onDeleted: () => void;
  onError: (e: unknown) => void;
}

interface DocForm {
  revision: string;
  issue_date: string;
  stage: string;
  title: string;
  issuer: string;
}

interface SeriesForm {
  project_id: string;
  building: string;
  stream: string;
  disciplines: string[];
}

const toDocForm = (r: Revision): DocForm => ({
  revision: r.revision ?? "",
  issue_date: r.issue_date ?? "",
  stage: r.stage ?? "",
  title: r.title ?? "",
  issuer: r.issuer ?? "",
});

/** Lịch sử revision của 1 bộ: lọc; thêm revision; sửa metadata (từng revision / cả bộ); đặt hiệu lực, gỡ, xóa. */
export default function SeriesHistory({
  docCode,
  userId,
  options,
  projects,
  onAddRevision,
  onChanged,
  onDeleted,
  onError,
}: Props) {
  const [data, setData] = useState<History | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [stage, setStage] = useState("");
  const [busy, setBusy] = useState(false);
  const [editDoc, setEditDoc] = useState<string | null>(null);
  const [docForm, setDocForm] = useState<DocForm | null>(null);
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
    setEditDoc(null);
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

  const startEditDoc = (r: Revision) => {
    setEditDoc(r.doc_id);
    setDocForm(toDocForm(r));
  };
  const saveDoc = async (r: Revision) => {
    if (!docForm) return;
    const old = toDocForm(r);
    // chỉ gửi trường đã đổi; để trống = xóa giá trị
    const patch = Object.fromEntries(
      (Object.keys(docForm) as (keyof DocForm)[])
        .filter((k) => docForm[k].trim() !== old[k])
        .map((k) => [k, docForm[k].trim() || null]),
    );
    if (Object.keys(patch).length === 0) return setEditDoc(null);
    if (await act(() => updateDocument(r.doc_id, patch))) setEditDoc(null);
  };

  const startEditSeries = () =>
    head &&
    setSeriesForm({
      project_id: head.project_id,
      building: head.building,
      stream: head.stream,
      disciplines: [...head.disciplines],
    });
  const seriesProject = projects.find((p) => p.project_id === seriesForm?.project_id);
  const saveSeries = async () => {
    if (!seriesForm || !head) return;
    const patch: Record<string, unknown> = {};
    if (seriesForm.project_id !== head.project_id) patch.project_id = seriesForm.project_id;
    if (seriesForm.building !== head.building) patch.building = seriesForm.building;
    if (seriesForm.stream !== head.stream) patch.stream = seriesForm.stream;
    if (seriesForm.disciplines.join() !== head.disciplines.join()) patch.disciplines = seriesForm.disciplines;
    if (Object.keys(patch).length === 0) return setSeriesForm(null);
    if (await act(() => updateSeries(docCode, patch))) setSeriesForm(null);
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
    <div className="kn-card">
      <div className="kn-row">
        <div>
          <div className="kn-code">{docCode}</div>
          <div className="small muted">
            {head && `${head.project_id} / ${head.building} · ${head.stream} · ${head.disciplines.join(", ")} · `}
            {data.n_revisions} revision{" "}
            {data.unpublished && <span className="kn-tag warn">Đã gỡ khỏi tìm kiếm – đặt bản hiệu lực để khôi phục</span>}
          </div>
        </div>
        <div className="kn-actions">
          <button
            className="kn-btn small primary"
            disabled={busy || !head}
            onClick={() => head && onAddRevision({ project_id: head.project_id, building: head.building, doc_code: docCode })}
          >
            + Thêm revision
          </button>
          <button className="kn-btn small" disabled={busy || !head} onClick={startEditSeries}>
            Sửa thông tin bộ
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
          <strong>Sửa thông tin bộ (áp cho cả {data.n_revisions} revision)</strong>
          <div className="kn-grid">
            <label className="kn-field">
              <span>Dự án</span>
              <select
                value={seriesForm.project_id}
                onChange={(e) => {
                  const p = projects.find((x) => x.project_id === e.target.value);
                  setSeriesForm({ ...seriesForm, project_id: e.target.value, building: p?.buildings[0]?.building ?? "" });
                }}
              >
                {projects.map((p) => (
                  <option key={p.project_id} value={p.project_id}>
                    {p.project_id} – {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="kn-field">
              <span>Công trình</span>
              <select value={seriesForm.building} onChange={(e) => setSeriesForm({ ...seriesForm, building: e.target.value })}>
                {(seriesProject?.buildings ?? []).map((b) => (
                  <option key={b.building}>{b.building}</option>
                ))}
              </select>
            </label>
            <label className="kn-field">
              <span>Luồng</span>
              <select value={seriesForm.stream} onChange={(e) => setSeriesForm({ ...seriesForm, stream: e.target.value })}>
                {(options?.streams ?? []).map((s) => (
                  <option key={s.value} value={s.value} title={s.label}>
                    {s.value}
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
            <span className="small muted">Mã bộ ({docCode}) giữ nguyên để revision sau vẫn nối đúng bộ.</span>
            <span className="kn-actions">
              <button className="kn-btn small" disabled={busy} onClick={() => setSeriesForm(null)}>
                Hủy
              </button>
              <button
                className="kn-btn small primary"
                disabled={busy || !seriesForm.building || seriesForm.disciplines.length === 0}
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
      <div className="table-wrap">
      <table className="kn-table stack">
        <thead>
          <tr>
            <th>Revision</th>
            <th>Ngày ban hành</th>
            <th>Giai đoạn</th>
            <th>Tiêu đề / Đơn vị ban hành</th>
            <th>Phát hành vào hệ thống</th>
            <th>Người phát hành</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data.revisions.length === 0 && (
            <tr>
              <td colSpan={7} className="muted stack-full">
                Không có revision khớp bộ lọc
              </td>
            </tr>
          )}
          {data.revisions.map((r) =>
            editDoc === r.doc_id && docForm ? (
              <tr key={r.doc_id} className="kn-editing">
                <td data-label="Revision">
                  <input value={docForm.revision} onChange={(e) => setDocForm({ ...docForm, revision: e.target.value })} />
                </td>
                <td data-label="Ngày ban hành">
                  <input
                    type="date"
                    value={docForm.issue_date}
                    onChange={(e) => setDocForm({ ...docForm, issue_date: e.target.value })}
                  />
                </td>
                <td data-label="Giai đoạn">
                  <select value={docForm.stage} onChange={(e) => setDocForm({ ...docForm, stage: e.target.value })}>
                    <option value="">–</option>
                    {Array.from(new Set([...(options?.stages ?? []), ...(docForm.stage ? [docForm.stage] : [])])).map(
                      (s) => (
                        <option key={s}>{s}</option>
                      ),
                    )}
                  </select>
                </td>
                <td data-label="Tiêu đề / Đơn vị">
                  <input
                    value={docForm.title}
                    placeholder="Tiêu đề"
                    onChange={(e) => setDocForm({ ...docForm, title: e.target.value })}
                  />
                  <input
                    value={docForm.issuer}
                    placeholder="Đơn vị ban hành"
                    onChange={(e) => setDocForm({ ...docForm, issuer: e.target.value })}
                  />
                </td>
                <td colSpan={2} className="small muted stack-full">
                  Đổi revision / ngày ban hành không tự đổi bản hiệu lực – dùng "Đặt hiệu lực".
                </td>
                <td className="kn-actions">
                  <button className="kn-btn small primary" disabled={busy} onClick={() => saveDoc(r)}>
                    Lưu
                  </button>
                  <button className="kn-btn small" disabled={busy} onClick={() => setEditDoc(null)}>
                    Hủy
                  </button>
                </td>
              </tr>
            ) : (
              <tr key={r.doc_id} className={r.is_latest ? "kn-latest" : ""}>
                <td data-label="Revision" className="stack-title">
                  {r.revision ?? "?"} {r.is_latest && <span className="kn-tag ok">Hiệu lực</span>}
                </td>
                <td data-label="Ngày ban hành">{fmtDate(r.issue_date)}</td>
                <td data-label="Giai đoạn">{r.stage ?? "–"}</td>
                <td data-label="Tiêu đề / Đơn vị" title={r.doc_id}>
                  {r.title ?? "–"}
                  {r.issuer && <div className="small muted">{r.issuer}</div>}
                </td>
                <td data-label="Phát hành">{fmtTime(r.published_at ?? r.ingested_at)}</td>
                <td data-label="Người phát hành">{r.published_by ?? (r.job_id ? "–" : "(CLI)")}</td>
                <td className="kn-actions">
                  {!r.is_latest && (
                    <button
                      className="kn-btn small"
                      disabled={busy}
                      onClick={() => act(() => setLatest(docCode, r.doc_id, userId))}
                    >
                      Đặt hiệu lực
                    </button>
                  )}
                  <button className="kn-btn small" disabled={busy} onClick={() => startEditDoc(r)}>
                    Sửa
                  </button>
                  <a className="kn-btn small" href={fileUrl(r.doc_id)} target="_blank" rel="noreferrer">
                    File gốc
                  </a>
                  <button
                    className="kn-btn small danger"
                    disabled={busy}
                    onClick={() =>
                      data.n_revisions === 1
                        ? removeSeries() // revision cuối → bộ biến mất
                        : act(() => deleteRevision(r.doc_id), `Xóa revision ${r.revision ?? r.doc_id}? (file gốc vẫn giữ)`)
                    }
                  >
                    Xóa
                  </button>
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
      </div>
    </div>
  );
}
