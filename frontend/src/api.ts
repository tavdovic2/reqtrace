// Typed client for the reqTrace API. All requests go to /api, which the Vite dev server
// and nginx both forward to the FastAPI backend.

import { askBody } from "./devMode";

export const API_BASE = "/api";

export interface Drawer {
  id: number;
  name: string;
  is_temporary: boolean;
  created_at: string;
  document_count: number;
}

export type DocumentStatus = "processing" | "ready" | "failed";
export type ExtractionStatus = "idle" | "running" | "done" | "failed";

export interface DocumentInfo {
  id: number;
  drawer_id: number;
  filename: string;
  page_count: number;
  status: DocumentStatus;
  /** Pages parsed so far while processing; equals page_count once indexing starts. */
  ingest_pages_done: number;
  error: string | null;
  created_at: string;
  expires_at: string | null;
  extraction_status: ExtractionStatus;
}

export interface Source {
  filename: string;
  doc_id: number;
  page: number;
  snippet: string;
  /** The evidence passage exactly as it appears in the page text, if the API found it. */
  highlight: string | null;
}

export interface AnswerLine {
  text: string;
  /** Indexes into `sources`. */
  sources: number[];
}

export interface RetrievedChunk {
  rank: number;
  filename: string;
  doc_id: number;
  page: number;
  section: string;
  score: number;
  snippet: string;
}

export interface DiscardedClaim {
  statement: string;
  reason: string;
  code: "no_citation" | "number_missing";
  numbers: string[];
}

/** Returned only when the request sets `debug`. */
export interface AskDebug {
  queries: string[];
  retrieved: RetrievedChunk[];
  model_answered: boolean | null;
  discarded: DiscardedClaim[];
}

export interface AskResponse {
  answer: string;
  answered: boolean;
  sources: Source[];
  lines: AnswerLine[];
  debug?: AskDebug | null;
}

export type RequirementKind = "obligation" | "limit";

export interface Requirement {
  code: string;
  kind: RequirementKind;
  /** Verbatim span of the page text; the primary content of a row. */
  quote: string;
  /** The model's rewording of the quote; empty when removed or when paraphrasing is off. */
  paraphrase: string;
  type: string;
  page: number;
  verified: boolean;
}

export interface RequirementsTable {
  document_id: number;
  status: ExtractionStatus;
  error: string | null;
  done_chunks: number;
  total_chunks: number;
  rejected: number;
  rejected_quote_not_found: number;
  rejected_not_requirement: number;
  skipped_sections: number;
  paraphrases_removed: number;
  elapsed_seconds: number | null;
  requirements: Requirement[];
}

export interface Health {
  api: "ok";
  qdrant: boolean;
  ollama: boolean;
  llm_model: string;
  llm_model_available: boolean;
}

/** An error with the API's own message (FastAPI's `detail`) when there is one. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function detailMessage(body: unknown): string | null {
  if (!body || typeof body !== "object" || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : "")).join("; ");
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError("network", 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(detailMessage(body) ?? response.statusText, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  health: () => request<Health>("/health"),
  listDrawers: () => request<Drawer[]>("/drawers"),
  createDrawer: (name: string, isTemporary: boolean) =>
    request<Drawer>("/drawers", json("POST", { name, is_temporary: isTemporary })),
  renameDrawer: (id: number, name: string) => request<Drawer>(`/drawers/${id}`, json("PATCH", { name })),
  deleteDrawer: (id: number) => request<void>(`/drawers/${id}`, { method: "DELETE" }),
  listDocuments: (drawerId: number) => request<DocumentInfo[]>(`/drawers/${drawerId}/documents`),
  uploadDocument: (drawerId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentInfo>(`/drawers/${drawerId}/documents`, { method: "POST", body: form });
  },
  deleteDocument: (id: number) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  // In developer mode the debug details come with every answer: they cost no extra model
  // call, and a "not in the documents" answer can explain itself without asking again.
  ask: (question: string, drawerId: number | null, devMode: boolean) =>
    request<AskResponse>("/ask", json("POST", askBody(question, drawerId, devMode))),
  startExtraction: (docId: number) =>
    request<RequirementsTable>(`/documents/${docId}/requirements`, { method: "POST" }),
  getRequirements: (docId: number) => request<RequirementsTable>(`/documents/${docId}/requirements`),
};

export const fileUrl = (docId: number) => `${API_BASE}/documents/${docId}/file`;
export const exportUrl = (docId: number, format: "csv" | "xlsx") =>
  `${API_BASE}/documents/${docId}/requirements?format=${format}`;
