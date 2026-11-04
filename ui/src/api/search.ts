// GET /search?q= : plain-language search over active, trusted listings.
import { ApiError, type Session } from "./client";
import type { Trust } from "./review";

export interface SearchResult {
  service_id: string;
  name: string;
  organization_name: string | null;
  snippet: string;
  city: string | null;
  phone: string | null;
  website: string | null;
  source: string;
  score: number;
  trust: Trust;
}

export interface SearchResponse {
  query: string;
  needs: string[];
  own_terms: string[];
  added_terms: string[];
  total: number;
  hidden: number;
  backend: string;
  results: SearchResult[];
}

export interface SearchApi {
  readonly isDemo: boolean;
  search(q: string, offset?: number, limit?: number): Promise<SearchResponse>;
}

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export function createLiveSearchApi(token: string): SearchApi {
  return {
    isDemo: false,
    async search(q, offset = 0, limit = 10) {
      const params = new URLSearchParams({ q, offset: String(offset), limit: String(limit) });
      let response: Response;
      try {
        response = await fetch(`${API_BASE}/search?${params}`, { headers: { Authorization: `Bearer ${token}` } });
      } catch {
        throw new ApiError(0, "Can't reach the Resource Engine API. Is it running?");
      }
      if (!response.ok) {
        const body: { detail?: unknown } = await response.json().catch(() => ({}));
        const message = response.status === 422 ? "Type at least two letters." : typeof body.detail === "string" ? body.detail : `Search failed (${response.status}).`;
        throw new ApiError(response.status, message);
      }
      return (await response.json()) as SearchResponse;
    },
  };
}

/** Demo mode: two simulated results. */
export function createMockSearchApi(): SearchApi {
  const now = new Date().toISOString();
  const results: SearchResult[] = [
    {
      service_id: "demo-1", name: "Help paying rent", organization_name: "Demo Housing Help", city: "Oakland",
      snippet: "Emergency «rent» and «financial» «assistance» for tenants behind on «rent».", phone: "(510) 555-0100",
      website: "https://housing.example", source: "odrs", score: 9,
      trust: { score: 98, level: "high", badge: "Verified today", checked_at: now },
    },
    {
      service_id: "demo-2", name: "Job center", organization_name: "Demo Works", city: "Oakland",
      snippet: "Free «employment» services and «unemployment» benefits help.", phone: null, website: null,
      source: "ndp", score: 4, trust: { score: null, level: "unknown", badge: "Not checked yet", checked_at: null },
    },
  ];
  return {
    isDemo: true,
    async search(q, offset = 0) {
      if (q.trim().length < 2) throw new ApiError(422, "Type at least two letters.");
      return {
        query: q, needs: ["Rent and housing costs"], own_terms: q.toLowerCase().split(/\s+/), added_terms: ["housing", "financial assistance"],
        total: 2, hidden: 1, backend: "demo", results: offset ? [] : results,
      };
    },
  };
}

export function createSearchApi(session: Session): SearchApi {
  return import.meta.env.VITE_API_MODE === "live" ? createLiveSearchApi(session.token) : createMockSearchApi();
}
