import { useMemo, useState } from "react";
import type { Citation } from "../types";
import Icon from "./Icon";

/** Số mục hiện sẵn mỗi tài liệu; còn lại thu gọn sau nút "Thêm N mục". */
const PREVIEW = 3;

/** Dòng quốc hiệu / tiêu ngữ trên bìa văn bản – không phải tên tài liệu (ingest nhận nhầm làm tiêu đề). */
const GENERIC_TITLE = /^\s*(c[ộo]ng h[òo]a x[ãa] h[ộo]i|đ[ộo]c l[ậa]p\s*[-–]\s*t[ựu] do|socialist republic of viet)/i;

function pages(c: Citation): string | null {
  if (c.page == null) return null;
  return c.page_end && c.page_end !== c.page ? `tr. ${c.page}–${c.page_end}` : `tr. ${c.page}`;
}

/** Mã mục nội bộ (app/utils/keys.py) → nhãn đọc được: table_7_4_17 → "Bảng 7.4.17", sec_0 → "Phần mở đầu". */
export function sectionLabel(c: Pick<Citation, "number" | "section_key">): string {
  if (c.number) return `Mục ${c.number}`;
  const m = /^([a-z]+)_(.+)$/.exec(c.section_key);
  if (!m) return c.section_key;
  const num = m[2].replace(/_/g, ".").toUpperCase();
  switch (m[1]) {
    case "table":
      return `Bảng ${num}`;
    case "sec":
      return num === "0" ? "Phần mở đầu" : `Mục ${num}`;
    case "chap":
      return `Chương ${num}`;
    case "art":
      return `Điều ${num}`;
    case "appx":
      return `Phụ lục ${num}`;
    default:
      return c.section_key;
  }
}

interface DocGroup {
  doc_id: string;
  title: string | null; // null = chưa có tiêu đề dùng được
  revision?: string | null;
  lang: string[];
  items: Citation[];
}

/** Gom trích dẫn theo tài liệu (giữ thứ tự số trích dẫn nhỏ nhất của mỗi tài liệu). */
export function groupCitations(items: Citation[]): DocGroup[] {
  const by = new Map<string, DocGroup>();
  for (const c of [...items].sort((a, b) => a.n - b.n)) {
    let g = by.get(c.doc_id);
    if (!g) {
      const t = c.doc_title?.trim() || "";
      g = { doc_id: c.doc_id, title: t && !GENERIC_TITLE.test(t) ? t : null, revision: c.revision, lang: [], items: [] };
      by.set(c.doc_id, g);
    }
    g.items.push(c);
    for (const l of c.lang) if (!g.lang.includes(l)) g.lang.push(l);
  }
  return [...by.values()];
}

function DocSources({ g }: { g: DocGroup }) {
  const [open, setOpen] = useState(false);
  const shown = open ? g.items : g.items.slice(0, PREVIEW);
  const hidden = g.items.length - shown.length;
  return (
    <li className="cite-group">
      <div className="cite-head">
        <Icon name="file" size={16} className="cite-icon" />
        <span className={"cite-doc" + (g.title ? "" : " untitled")} title={g.title ?? undefined}>
          {g.title ?? "Tài liệu chưa có tiêu đề"}
        </span>
        <span className="cite-meta">
          {g.revision && <span className="cite-tag">Rev {g.revision}</span>}
          {g.lang.map((l) => (
            <span key={l} className="cite-tag">
              {l.toUpperCase()}
            </span>
          ))}
        </span>
      </div>
      <ol className="cite-items">
        {shown.map((c) => (
          <li key={c.n}>
            <span className="cite-n" aria-label={`Trích dẫn ${c.n}`}>
              {c.n}
            </span>
            <span className="cite-sec">
              <span className="cite-sec-label">{sectionLabel(c)}</span>
              {c.title && <span className="cite-sec-title"> – {c.title}</span>}
            </span>
            {pages(c) && <span className="cite-page">{pages(c)}</span>}
          </li>
        ))}
      </ol>
      {g.items.length > PREVIEW && (
        <button type="button" className="cite-more" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Thu gọn" : `Thêm ${hidden} mục`}
          <Icon name={open ? "chevron-left" : "chevron-right"} size={14} className={open ? "up" : "down"} />
        </button>
      )}
    </li>
  );
}

export default function Citations({ items }: { items: Citation[] }) {
  const groups = useMemo(() => groupCitations(items), [items]);
  return (
    <section className="citations" aria-label="Nguồn trích dẫn">
      <div className="citations-label">
        <Icon name="book" size={14} />
        Nguồn
        <span className="citations-count">
          {items.length} trích dẫn{groups.length > 1 ? ` · ${groups.length} tài liệu` : ""}
        </span>
      </div>
      <ul className="cite-groups">
        {groups.map((g) => (
          <DocSources key={g.doc_id} g={g} />
        ))}
      </ul>
    </section>
  );
}
