import { useMemo, useState } from "react";
import Icon from "../Icon";
import { fmtDate, projectStatus, type Project, type TreeProject } from "../../knowledgeApi";
import ProjectForm from "./ProjectForm";

interface Props {
  tree: TreeProject[];
  loading: boolean;
  onOpen: (projectId: string) => void;
  onCreated: (p: Project) => void;
  onError: (e: unknown) => void;
}

const PAGE_SIZES = [9, 18, 36];
const norm = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/gi, "d")
    .toLowerCase();

/** Kho tri thức – danh sách dự án dạng thẻ (theo trang Quản lý dự án Hawee): tìm theo tên, phân trang,
 *  tạo dự án mới (chỉ cần tên). Bấm thẻ → tài liệu của dự án. */
export default function ProjectsBoard({ tree, loading, onOpen, onCreated, onError }: Props) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(PAGE_SIZES[0]);
  const [creating, setCreating] = useState(false);

  const filtered = useMemo(() => {
    const q = norm(query.trim());
    return q ? tree.filter((p) => norm(`${p.name} ${p.project_id}`).includes(q)) : tree;
  }, [tree, query]);
  const pages = Math.max(1, Math.ceil(filtered.length / size));
  const cur = Math.min(page, pages);
  const shown = filtered.slice((cur - 1) * size, cur * size);

  return (
    <div className="kp">
      <div className="kp-head">
        <div>
          <h2 className="kp-title">
            Kho tri thức <span className="kp-count">{tree.length} dự án</span>
          </h2>
          <p className="muted kp-sub">Tài liệu dự án sắp theo từng dự án. Chọn một dự án để xem, tải lên hoặc sửa tài liệu.</p>
        </div>
        <button className="kn-btn primary" onClick={() => setCreating(true)}>
          <Icon name="plus" size={16} /> Dự án mới
        </button>
      </div>

      {creating && (
        <ProjectForm
          onCancel={() => setCreating(false)}
          onSaved={(p) => {
            setCreating(false);
            onCreated(p);
          }}
          onError={onError}
        />
      )}

      <label className="kp-search">
        <Icon name="search" size={18} />
        <input
          type="search"
          value={query}
          placeholder="Tìm theo tên dự án…"
          aria-label="Tìm theo tên dự án"
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
        />
      </label>

      {loading && tree.length === 0 ? (
        <div className="muted pad">Đang tải…</div>
      ) : filtered.length === 0 ? (
        <div className="kp-empty">
          <Icon name="folder" size={28} />
          <p>{query ? "Không có dự án khớp tên đã nhập." : "Chưa có dự án nào. Bấm “Dự án mới” để bắt đầu."}</p>
        </div>
      ) : (
        <div className="kp-grid">
          {shown.map((p) => {
            const st = projectStatus(p.start_date, p.end_date);
            return (
              <button key={p.project_id} className="kp-card" onClick={() => onOpen(p.project_id)}>
                <div className="kp-card-top">
                  <span className="kp-name">{p.name}</span>
                  <span className={`kp-badge ${st.tone}`}>{st.label}</span>
                </div>
                <div className="kp-code">{p.project_id}</div>
                <dl className="kp-info">
                  {p.start_date && (
                    <div>
                      <dt>
                        <Icon name="calendar" size={15} /> Bắt đầu
                      </dt>
                      <dd>{fmtDate(p.start_date)}</dd>
                    </div>
                  )}
                  {p.end_date && (
                    <div>
                      <dt>
                        <Icon name="calendar" size={15} /> Dự kiến kết thúc
                      </dt>
                      <dd>{fmtDate(p.end_date)}</dd>
                    </div>
                  )}
                  <div>
                    <dt>
                      <Icon name="file" size={15} /> Tài liệu
                    </dt>
                    <dd>
                      {p.n_series === 0
                        ? "Chưa có"
                        : `${p.n_series} bộ${p.n_documents > p.n_series ? ` · ${p.n_documents} bản` : ""}`}
                    </dd>
                  </div>
                  {p.active_jobs > 0 && (
                    <div>
                      <dt>
                        <Icon name="clock" size={15} /> Đang xử lý
                      </dt>
                      <dd>{p.active_jobs} file</dd>
                    </div>
                  )}
                </dl>
                <span className="kp-foot">
                  <Icon name="folder" size={15} /> Nhấn để xem chi tiết
                </span>
              </button>
            );
          })}
        </div>
      )}

      {filtered.length > 0 && (
        <nav className="kp-pager" aria-label="Phân trang">
          <span className="muted">
            Hiển thị <strong>{(cur - 1) * size + 1}</strong> – <strong>{Math.min(cur * size, filtered.length)}</strong> /{" "}
            <strong>{filtered.length}</strong> dự án
          </span>
          <span className="kn-actions">
            <label className="muted small">
              Mỗi trang{" "}
              <select
                value={size}
                onChange={(e) => {
                  setSize(Number(e.target.value));
                  setPage(1);
                }}
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
            <button className="icon-btn kp-pg" disabled={cur <= 1} onClick={() => setPage(cur - 1)} aria-label="Trang trước">
              <Icon name="chevron-left" />
            </button>
            <span className="kp-pg-num">
              {cur} / {pages}
            </span>
            <button className="icon-btn kp-pg" disabled={cur >= pages} onClick={() => setPage(cur + 1)} aria-label="Trang sau">
              <Icon name="chevron-right" />
            </button>
          </span>
        </nav>
      )}
    </div>
  );
}
