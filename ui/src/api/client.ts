// REST API client. This is the only data path for the review tool.

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface ListParams {
  page?: number;
  per_page?: number;
  category?: string;
  source?: string;
  search?: string;
}

// Typed requests for the HSDS and review endpoints, with the bearer token, go here.
export async function listServices(_params: ListParams = {}): Promise<unknown> {
  throw new Error("Not implemented yet: REST client");
}

export { API_BASE };
