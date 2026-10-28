import type { Citation } from "../types";

function pages(c: Citation): string | null {
  if (c.page == null) return null;
  return c.page_end && c.page_end !== c.page ? `tr. ${c.page}–${c.page_end}` : `tr. ${c.page}`;
}

export default function Citations({ items }: { items: Citation[] }) {
  return (
    <div className="citations">
      <div className="small muted">Nguồn</div>
      <ol>
        {items.map((c) => (
          <li key={c.n} value={c.n}>
            <span className="cite-doc">{c.doc_title || c.doc_id}</span>
            {c.revision && <span className="muted"> · Rev {c.revision}</span>}
            <span>
              {" · Mục "}
              {c.number || c.section_key}
              {c.title ? ` – ${c.title}` : ""}
            </span>
            {pages(c) && <span className="muted"> · {pages(c)}</span>}
            {c.lang.map((l) => (
              <span key={l} className="chip lang">
                {l.toUpperCase()}
              </span>
            ))}
          </li>
        ))}
      </ol>
    </div>
  );
}
