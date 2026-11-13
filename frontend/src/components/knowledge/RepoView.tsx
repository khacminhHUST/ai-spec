import { useCallback, useEffect, useState } from "react";
import { getTree, type KnowledgeOptions, type Project, type TreeProject, type UploadPreset } from "../../knowledgeApi";
import SeriesHistory from "./SeriesHistory";

interface Props {
  userId: string;
  options: KnowledgeOptions | null;
  projects: Project[];
  onAdd: (preset: UploadPreset) => void;
  onError: (e: unknown) => void;
}

/** Cây kho (trái): dự án → công trình → luồng → phân hệ → bộ tài liệu; lịch sử revision của bộ đang chọn (phải).
 * Thêm tài liệu (mở form upload điền sẵn dự án / công trình), sửa / xóa ở SeriesHistory. */
export default function RepoView({ userId, options, projects, onAdd, onError }: Props) {
  const [tree, setTree] = useState<TreeProject[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const reload = useCallback(() => {
    getTree().then(setTree).catch(onError);
  }, [onError]);

  useEffect(reload, [reload]);

  return (
    <div className="kn-split">
      <section className="kn-tree">
        <div className="kn-row">
          <strong>Cây kho</strong>
          <span className="kn-actions">
            <button className="kn-btn small primary" onClick={() => onAdd({})}>
              + Thêm tài liệu
            </button>
            <button className="kn-btn small" onClick={reload}>
              Làm mới
            </button>
          </span>
        </div>
        {tree.length === 0 && <div className="muted small">Chưa có dự án</div>}
        {tree.map((p) => (
          <details key={p.project_id} open>
            <summary>
              <strong>{p.project_id}</strong> <span className="muted">{p.name}</span>
            </summary>
            {p.buildings.length === 0 && <div className="kn-indent muted small">Chưa có công trình</div>}
            {p.buildings.map((b) => (
              <details key={b.building} className="kn-indent" open>
                <summary>
                  Công trình {b.building}{" "}
                  <button
                    className="kn-link small"
                    title={`Thêm tài liệu vào ${p.project_id} / ${b.building}`}
                    onClick={(e) => {
                      e.preventDefault();
                      onAdd({ project_id: p.project_id, building: b.building });
                    }}
                  >
                    + thêm
                  </button>
                </summary>
                {b.streams.length === 0 && <div className="kn-indent muted small">Chưa có tài liệu</div>}
                {b.streams.map((st) => (
                  <details key={st.stream} className="kn-indent" open>
                    <summary>{st.stream}</summary>
                    {st.disciplines.map((d) => (
                      <details key={d.discipline} className="kn-indent" open>
                        <summary>{d.discipline}</summary>
                        {d.series.map((s) => (
                          <button
                            key={s.doc_code}
                            className={"kn-series" + (selected === s.doc_code ? " active" : "")}
                            onClick={() => setSelected(s.doc_code)}
                            title={s.doc_code}
                          >
                            <span className="kn-series-title">{s.title || s.doc_code}</span>
                            <span className="small muted">
                              {s.n_revisions} revision ·{" "}
                              {s.unpublished ? (
                                <span className="kn-tag warn">Đã gỡ</span>
                              ) : (
                                <>hiệu lực: Rev {s.latest?.revision ?? "?"}</>
                              )}
                            </span>
                          </button>
                        ))}
                      </details>
                    ))}
                  </details>
                ))}
              </details>
            ))}
          </details>
        ))}
      </section>
      <section className="kn-detail">
        {selected ? (
          <SeriesHistory
            docCode={selected}
            userId={userId}
            options={options}
            projects={projects}
            onAddRevision={onAdd}
            onChanged={reload}
            onDeleted={() => {
              setSelected(null);
              reload();
            }}
            onError={onError}
          />
        ) : (
          <div className="muted pad">Chọn một bộ tài liệu để xem, sửa, xóa hoặc thêm revision.</div>
        )}
      </section>
    </div>
  );
}
