// Cross-source conflicts: GET /conflicts, POST /conflicts/detect, POST /conflicts/{id}/resolve.
import { ApiError, type Session, type ValidationProblem } from "./client";

export interface ConflictSide {
  source: string;
  value: string | null;
  values: string[];
  organization_id: string;
  services: { id: string; name: string }[];
}

export interface Conflict {
  id: string;
  organization_name: string;
  field: string;
  field_label: string;
  status: "open" | "resolved";
  a: ConflictSide;
  b: ConflictSide;
  detected_at: string;
  resolution: string | null;
  resolved_value: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
}

export type Choice = "a" | "b" | "custom";

export interface ConflictsApi {
  readonly isDemo: boolean;
  list(status: "open" | "resolved"): Promise<Conflict[]>;
  detect(): Promise<{ opened: number; refreshed: number; closed: number }>;
  resolve(id: string, choice: Choice, customValue?: string): Promise<{ conflict: Conflict; services_updated: number }>;
}

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach the Resource Engine API. Is it running?");
  }
  if (response.ok) return (await response.json()) as T;
  const body: { detail?: string | ValidationProblem[]; message?: string } = await response.json().catch(() => ({}));
  if (Array.isArray(body.detail)) throw new ApiError(response.status, body.message ?? "Invalid value.", body.detail);
  throw new ApiError(response.status, body.detail ?? body.message ?? `Request failed (${response.status}).`);
}

export function createLiveConflictsApi(token: string): ConflictsApi {
  return {
    isDemo: false,
    list: (status) => request<Conflict[]>(token, `/conflicts?status=${status}`),
    detect: () => request(token, "/conflicts/detect", { method: "POST" }),
    resolve: (id, choice, customValue) =>
      request(token, `/conflicts/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ choice, custom_value: customValue ?? null }),
      }),
  };
}

/** Demo mode: one simulated conflict. */
export function createMockConflictsApi(actor: string): ConflictsApi {
  let conflicts: Conflict[] = [
    {
      id: "demo-c1",
      organization_name: "Demo Community Center",
      field: "email",
      field_label: "Email",
      status: "open",
      a: { source: "ndp", value: "admin@demo.example", values: ["admin@demo.example"], organization_id: "o1", services: [{ id: "s1", name: "Food Pantry" }, { id: "s2", name: "Rent Help" }] },
      b: { source: "odrs", value: "help@demo.example", values: ["help@demo.example"], organization_id: "o2", services: [{ id: "s3", name: "Get rent help" }] },
      detected_at: new Date().toISOString(),
      resolution: null,
      resolved_value: null,
      resolved_by: null,
      resolved_at: null,
    },
  ];
  return {
    isDemo: true,
    list: async (status) => conflicts.filter((c) => c.status === status),
    detect: async () => ({ opened: 0, refreshed: conflicts.filter((c) => c.status === "open").length, closed: 0 }),
    async resolve(id, choice, customValue) {
      const current = conflicts.find((c) => c.id === id);
      if (!current) throw new ApiError(404, "No such conflict");
      if (choice === "custom" && !customValue?.trim()) {
        throw new ApiError(422, "Enter the value to use.", [{ path: current.field, message: "a value is required" }]);
      }
      const value = choice === "a" ? current.a.value : choice === "b" ? current.b.value : customValue!.trim();
      const resolved: Conflict = { ...current, status: "resolved", resolution: choice, resolved_value: value, resolved_by: actor, resolved_at: new Date().toISOString() };
      conflicts = conflicts.map((c) => (c.id === id ? resolved : c));
      const updated = choice === "a" ? current.b.services.length : choice === "b" ? current.a.services.length : current.a.services.length + current.b.services.length;
      return { conflict: resolved, services_updated: updated };
    },
  };
}

export function createConflictsApi(session: Session): ConflictsApi {
  return import.meta.env.VITE_API_MODE === "live" ? createLiveConflictsApi(session.token) : createMockConflictsApi(session.name);
}
