// Real REST implementation of the review API.
import type {
  Clash,
  FieldChange,
  RecordDetail,
  RecordSummary,
  ReviewApi,
  ReviewFinding,
  Session,
  ValidationProblem,
} from "./client";
import { ApiError } from "./client";

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";
interface ListResponse<T> {
  total_items: number;
  contents: T[];
}

/**
 * The API has no sign-in endpoint yet, so the password is used as the bearer token for now.
 * Replace this when real accounts exist.
 */
export async function liveSignIn(email: string, password: string): Promise<Session> {
  const response = await fetch(`${API_BASE}/services?per_page=1`, { headers: { Authorization: `Bearer ${password}` } });
  if (!response.ok) throw new ApiError(401, "That email or password isn't right.");
  return { email, name: email.split("@")[0], token: password };
}

async function request<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (response.ok) {
    return (await response.json()) as T;
  }
  const body: { detail?: string | ValidationProblem[] } = await response.json().catch(() => ({}));
  const detail = body.detail;
  if (Array.isArray(detail)) {
    throw new ApiError(response.status, "The change didn't pass validation.", detail);
  }
  throw new ApiError(response.status, detail ?? `Request failed with status ${response.status}.`);
}

export function createLiveApi(token: string): ReviewApi {
  const get = <T>(path: string, init?: RequestInit) => request<T>(token, path, init);
  const post = <T>(path: string, body: unknown) => get<T>(path, { method: "POST", body: JSON.stringify(body) });
  return {
    isDemo: false,
    async listFlagged(filters = {}) {
      const query = new URLSearchParams(
        Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
      );
      return (await get<ListResponse<RecordSummary>>(`/review/records?${query}`)).contents;
    },
    async listAllFindings() {
      return (await get<ListResponse<ReviewFinding>>("/review/findings")).contents;
    },
    async listClashes() {
      return (await get<ListResponse<Clash>>("/review/clashes")).contents;
    },
    getFindings: (recordId) => get<RecordDetail>(`/review/records/${recordId}`),
    applyChange: (findingId, expectedVersion, editedValue) =>
      post<ReviewFinding>(`/review/findings/${findingId}/apply`, {
        expected_version: expectedVersion,
        edited_value: editedValue,
      }),
    dismissFinding: (findingId, reason) => post<ReviewFinding>(`/review/findings/${findingId}/dismiss`, { reason }),
    async patchRecord(recordId, expectedVersion, changes: FieldChange[]) {
      await get(`/review/records/${recordId}`, {
        method: "PATCH",
        body: JSON.stringify({ expected_version: expectedVersion, changes }),
      });
    },
    resolveClash: (clashId, expectedVersion, choice, customValue) =>
      post<Clash>(`/review/clashes/${clashId}/resolve`, {
        expected_version: expectedVersion,
        choice,
        custom_value: customValue,
      }),
    getComparison: async () => [],
  };
}
