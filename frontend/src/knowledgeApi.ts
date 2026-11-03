// API kho tri thức + dự án (app/api/routes/knowledge.py, projects.py). Chưa có đăng nhập: user_id chỉ để truy vết.

export interface Option {
  value: string;
  label: string;
}

export interface KnowledgeOptions {
  streams: Option[];
  disciplines: string[];
  doc_types: Option[];
  stages: string[];
  max_upload_mb: number;
}

export interface Building {
  building: string;
  aliases: string[];
}

export interface Project {
  project_id: string;
  name: string;
  aliases: string[];
  owner_aliases: string[];
  source_priority: string[];
  governing_language: string;
  buildings: Building[];
}

export interface RevisionBrief {
  doc_id: string;
  revision: string | null;
  issue_date: string | null;
  stage: string | null;
}

export interface SeriesNode {
  doc_code: string;
  title: string | null;
  n_revisions: number;
  latest: RevisionBrief | null;
  unpublished: boolean;
}

export interface TreeProject {
  project_id: string;
  name: string;
  buildings: {
    building: string;
    streams: { stream: string; disciplines: { discipline: string; series: SeriesNode[] }[] }[];
  }[];
}

export interface Revision {
  doc_id: string;
  doc_code: string;
  title: string | null;
  issuer: string | null;
  revision: string | null;
  issue_date: string | null;
  stage: string | null;
  languages: string[];
  is_latest: boolean;
  ingested_at: string;
  job_id: string | null;
  file_name: string | null;
  uploaded_by: string | null;
  published_by: string | null;
  published_at: string | null;
}

export interface SeriesHistory {
  doc_code: string;
  n_revisions: number;
  unpublished: boolean;
  revisions: Revision[];
}

export type JobStatus = "QUEUED" | "PROCESSING" | "DRAFT" | "PUBLISHING" | "PUBLISHED" | "FAILED" | "CANCELLED";

export interface Job {
  job_id: string;
  file_name: string;
  size: number;
  project_id: string;
  building: string;
  stream: string;
  disciplines: string[];
  doc_type: string;
  revision: string | null;
  issue_date: string | null;
  stage?: string | null;
  title?: string | null;
  issuer?: string | null;
  series_doc_code: string;
  status: JobStatus;
  step: string | null;
  error: string | null;
  doc_id: string | null;
  uploaded_by: string | null;
  created_at: string;
  published_by: string | null;
  published_at: string | null;
  updated_at: string;
  report?: Record<string, any> | null;
  warnings?: string[];
  replaces?: RevisionBrief & { title: string | null } | null;
}

export interface SectionRow {
  lang: string;
  section_key: string;
  parent_section_key: string | null;
  type: "SECTION" | "TABLE";
  number: string | null;
  title: string | null;
  page_start: number | null;
  page_end: number | null;
  token_count: number;
  n_children: number;
}

export interface SectionPreview {
  parent: { section_key: string; title: string | null; breadcrumb: string; content_md: string; page_start: number | null; page_end: number | null };
  children: { child_id: string; type: string; topic: string | null; raw_text: string; page_start: number | null; page_end: number | null }[];
}

export interface UploadMeta {
  project_id: string;
  building: string;
  stream: string;
  disciplines: string[];
  doc_type: string;
  revision?: string;
  issue_date?: string;
  stage?: string;
  title?: string;
  issuer?: string;
  series: { mode: "new" | "revision"; doc_code?: string };
}

export const ACTIVE_STATUSES: JobStatus[] = ["QUEUED", "PROCESSING", "PUBLISHING"];

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = await r.text();
    try {
      msg = JSON.parse(msg).detail ?? msg;
    } catch {
      /* giữ nguyên text */
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

const send = <T>(method: string, url: string, body?: unknown) =>
  req<T>(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const q = (params: Record<string, string | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) s.set(k, v);
  const t = s.toString();
  return t ? `?${t}` : "";
};

const enc = encodeURIComponent;

// ---- danh mục
export const getOptions = () => req<KnowledgeOptions>("/knowledge/options");
export const suggestFromName = (fileName: string) =>
  req<{ revision: string | null; issue_date: string | null; stage: string | null }>(
    `/knowledge/suggest${q({ file_name: fileName })}`,
  );

// ---- dự án
export const listProjects = () => req<Project[]>("/projects");
export const createProject = (p: Omit<Project, "buildings"> & { buildings?: Building[] }) =>
  send<Project>("POST", "/projects", p);
export const updateProject = (id: string, p: Partial<Omit<Project, "project_id" | "buildings">>) =>
  send<Project>("PATCH", `/projects/${enc(id)}`, p);
export const addBuilding = (id: string, b: Building) => send<Project>("POST", `/projects/${enc(id)}/buildings`, b);
export const updateBuilding = (id: string, b: string, aliases: string[]) =>
  send<Project>("PATCH", `/projects/${enc(id)}/buildings/${enc(b)}`, { aliases });
export const deleteBuilding = (id: string, b: string) =>
  send<Project>("DELETE", `/projects/${enc(id)}/buildings/${enc(b)}`);

// ---- kho / phiên bản
export const getTree = (projectId?: string) => req<TreeProject[]>(`/knowledge/tree${q({ project_id: projectId })}`);
export const getSeries = (code: string, f: { from?: string; to?: string; stage?: string } = {}) =>
  req<SeriesHistory>(`/knowledge/series/${enc(code)}${q(f)}`);
export const setLatest = (code: string, docId: string, userId: string) =>
  send("POST", `/knowledge/series/${enc(code)}/latest`, { doc_id: docId, user_id: userId });
export const unpublishSeries = (code: string) => send("POST", `/knowledge/series/${enc(code)}/unpublish`);
export const deleteRevision = (docId: string) => send("DELETE", `/knowledge/documents/${enc(docId)}`);
export const fileUrl = (docId: string) => `/knowledge/documents/${enc(docId)}/file`;

// ---- upload / job
export function uploadDocument(file: File, meta: UploadMeta, userId: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("metadata", JSON.stringify(meta));
  form.append("user_id", userId);
  return req<Job>("/knowledge/uploads", { method: "POST", body: form });
}
export const listJobs = (status?: string) => req<Job[]>(`/knowledge/jobs${q({ status })}`);
export const getJob = (id: string) => req<Job>(`/knowledge/jobs/${enc(id)}`);
export const getSections = (id: string) => req<SectionRow[]>(`/knowledge/jobs/${enc(id)}/sections`);
export const getSection = (id: string, lang: string, key: string) =>
  req<SectionPreview>(`/knowledge/jobs/${enc(id)}/sections/${enc(lang)}/${enc(key)}`);
export const jobAction = (id: string, action: "publish" | "cancel" | "retry", userId: string) =>
  send<Job>("POST", `/knowledge/jobs/${enc(id)}/${action}`, { user_id: userId });

export const STATUS_LABEL: Record<JobStatus, string> = {
  QUEUED: "Chờ xử lý",
  PROCESSING: "Đang xử lý",
  DRAFT: "Chờ duyệt",
  PUBLISHING: "Đang phát hành",
  PUBLISHED: "Đã phát hành",
  FAILED: "Lỗi",
  CANCELLED: "Đã hủy",
};

export const fmtDate = (s: string | null | undefined) => (s ? new Date(s).toLocaleDateString("vi-VN") : "–");
export const fmtTime = (s: string | null | undefined) => (s ? new Date(s).toLocaleString("vi-VN") : "–");
