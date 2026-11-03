import { useCallback, useEffect, useState } from "react";
import {
  deleteRevision,
  fileUrl,
  fmtDate,
  fmtTime,
  getSeries,
  setLatest,
  unpublishSeries,
  type SeriesHistory as History,
} from "../../knowledgeApi";

interface Props {
  docCode: string;
  userId: string;
  onChanged: () => void;
  onError: (e: unknown) => void;
}

/** Lịch sử revision của 1 bộ: lọc theo ngày ban hành / giai đoạn; đặt hiệu lực, gỡ, xóa, tải file gốc. */
export default function SeriesHistory({ docCode, userId, onChanged, onError }: Props) {
  const [data, setData] = useState<History | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [stage, setStage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getSeries(docCode, { from, to, stage })
      .then(setData)
      .catch((e) => {
        setData(null);
        onError(e);
      });
  }, [docCode, from, to, stage, onError]);

  useEffect(load, [load]);

  const act = async (f: () => Promise<unknown>, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      await f();
      load();
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <div className="muted pad">Đang tải…</div>;
  const stages = Array.from(new Set(data.revisions.map((r) => r.stage).filter(Boolean))) as string[];

  return (
    <div className="kn-card">
      <div className="kn-row">
        <div>
          <div className="kn-code">{docCode}</div>
          <div className="small muted">
            {data.n_revisions} revision{" "}
            {data.unpublished && <span className="kn-tag warn">Đã gỡ khỏi tìm kiếm – đặt bản hiệu lực để khôi phục</span>}
          </div>
        </div>
        {!data.unpublished && (
          <button
            className="kn-btn danger"
            disabled={busy}
            onClick={() => act(() => unpublishSeries(docCode), "Gỡ mọi revision của bộ này khỏi tìm kiếm?")}
          >
            Gỡ khỏi tìm kiếm
          </button>
        )}
      </div>
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
      <table className="kn-table">
        <thead>
          <tr>
            <th>Revision</th>
            <th>Ngày ban hành</th>
            <th>Giai đoạn</th>
            <th>Tiêu đề</th>
            <th>Phát hành vào hệ thống</th>
            <th>Người phát hành</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {data.revisions.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">
                Không có revision khớp bộ lọc
              </td>
            </tr>
          )}
          {data.revisions.map((r) => (
            <tr key={r.doc_id} className={r.is_latest ? "kn-latest" : ""}>
              <td>
                {r.revision ?? "?"} {r.is_latest && <span className="kn-tag ok">Hiệu lực</span>}
              </td>
              <td>{fmtDate(r.issue_date)}</td>
              <td>{r.stage ?? "–"}</td>
              <td title={r.doc_id}>{r.title ?? "–"}</td>
              <td>{fmtTime(r.published_at ?? r.ingested_at)}</td>
              <td>{r.published_by ?? (r.job_id ? "–" : "(CLI)")}</td>
              <td className="kn-actions">
                {!r.is_latest && (
                  <button className="kn-btn small" disabled={busy} onClick={() => act(() => setLatest(docCode, r.doc_id, userId))}>
                    Đặt hiệu lực
                  </button>
                )}
                <a className="kn-btn small" href={fileUrl(r.doc_id)} target="_blank" rel="noreferrer">
                  File gốc
                </a>
                <button
                  className="kn-btn small danger"
                  disabled={busy}
                  onClick={() =>
                    act(() => deleteRevision(r.doc_id), `Xóa revision ${r.revision ?? r.doc_id}? (file gốc vẫn giữ)`)
                  }
                >
                  Xóa
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
