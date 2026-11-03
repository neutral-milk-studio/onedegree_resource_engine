// REST API client — STUB (task 31).
// The ONLY data path for the admin UI. The UI never touches a store directly.
// Spec: Requirements 39.2, 39.3.

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface ListParams {
  page?: number;
  per_page?: number;
  category?: string;
  source?: string;
  search?: string;
}

// Filled in by task 31: typed fetch wrappers for GET /services, GET /services/{id},
// the search endpoint, and the ingest run trigger/report. Bearer token attached here.
export async function listServices(_params: ListParams = {}): Promise<unknown> {
  throw new Error("task 31: implement REST client against the API");
}

export { API_BASE };
