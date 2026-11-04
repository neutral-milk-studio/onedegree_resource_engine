// Dashboard data: GET /metrics, GET /audit, GET /ingest/runs, POST /ingest/{source}.
// Live mode calls the API. Demo (mock) mode returns clearly simulated numbers.
import { ApiError, type Session } from "./client";

export const FINDING_CATEGORIES = [
  "actively_bad",
  "suspicious",
  "discrepancy",
  "missing_information",
  "check_failed",
  "verified",
] as const;
export type FindingCategory = (typeof FINDING_CATEGORIES)[number];

export interface SourceCount {
  source: string;
  records: number;
}

export interface LastIngest {
  source: string;
  finished_at: string | null;
  received: number;
  stored: number;
  quarantined: number;
  objects_quarantined: number;
}

export interface Metrics {
  generated_at: string;
  total_records: number;
  records_by_source: SourceCount[];
  records_by_status: Record<string, number>;
  quarantined_records: number;
  records_missing_information: number;
  quarantined_objects: number;
  last_ingest: LastIngest[];
  findings: {
    checks_run: number;
    open_total: number;
    open_by_category: Record<FindingCategory, number>;
  };
  conflicts_open: number | null;
  avg_trust_score: number | null;
  cost_per_check_usd: number | null;
}

export interface AuditEntry {
  id: string;
  at: string;
  actor: string;
  action: string;
  change_source: string;
  entity_type: string;
  entity_id: string;
  field: string | null;
  old_value: unknown;
  new_value: unknown;
  summary: string | null;
}

export interface IngestRun {
  id: string;
  source: string;
  status: "running" | "succeeded" | "failed";
  triggered_by: string;
  started_at: string;
  finished_at: string | null;
  elapsed_ms: number | null;
  received: number;
  stored: number;
  quarantined: number;
  services_stored: number;
  objects_total: number;
  objects_kept: number;
  objects_quarantined: number;
  error: string | null;
}

export interface DashboardApi {
  readonly isDemo: boolean;
  getMetrics(): Promise<Metrics>;
  getAudit(limit?: number): Promise<AuditEntry[]>;
  getRuns(): Promise<IngestRun[]>;
  runIngest(source: string): Promise<IngestRun>;
}

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach the Resource Engine API. Is it running?");
  }
  if (response.ok) return (await response.json()) as T;
  const body: { detail?: unknown } = await response.json().catch(() => ({}));
  const detail = typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`;
  throw new ApiError(response.status, detail);
}

export function createLiveDashboardApi(token: string): DashboardApi {
  return {
    isDemo: false,
    getMetrics: () => request<Metrics>(token, "/metrics"),
    getAudit: (limit = 15) => request<AuditEntry[]>(token, `/audit?limit=${limit}`),
    getRuns: () => request<IngestRun[]>(token, "/ingest/runs"),
    runIngest: (source) => request<IngestRun>(token, `/ingest/${encodeURIComponent(source)}`, { method: "POST" }),
  };
}

/** Simulated numbers for demo mode. The page labels them as demo data. */
export function createMockDashboardApi(actor: string): DashboardApi {
  const now = () => new Date().toISOString();
  const runs: IngestRun[] = [];
  const audit: AuditEntry[] = [];
  const counts: Record<string, { received: number; stored: number; quarantined: number; objects: number }> = {
    odrs: { received: 50, stored: 50, quarantined: 0, objects: 0 },
    ndp: { received: 20, stored: 20, quarantined: 0, objects: 7 },
  };
  const services: Record<string, number> = { odrs: 50, ndp: 36 };

  const runIngest = async (source: string): Promise<IngestRun> => {
    const count = counts[source];
    if (!count) throw new ApiError(404, `unknown source '${source}'`);
    const run: IngestRun = {
      id: crypto.randomUUID(),
      source,
      status: "succeeded",
      triggered_by: actor,
      started_at: now(),
      finished_at: now(),
      elapsed_ms: 400,
      received: count.received,
      stored: count.stored,
      quarantined: count.quarantined,
      services_stored: services[source],
      objects_total: 0,
      objects_kept: 0,
      objects_quarantined: count.objects,
      error: null,
    };
    runs.unshift(run);
    audit.unshift({
      id: crypto.randomUUID(),
      at: now(),
      actor,
      action: "ingest_run",
      change_source: "ingest",
      entity_type: "source",
      entity_id: source,
      field: null,
      old_value: null,
      new_value: null,
      summary: `Ingested ${source}: ${count.received} received, ${count.stored} stored, ${count.quarantined} quarantined`,
    });
    return run;
  };

  return {
    isDemo: true,
    async getMetrics() {
      const latest = Object.keys(counts)
        .map((source) => runs.find((run) => run.source === source))
        .filter((run): run is IngestRun => Boolean(run));
      const bySource = latest.map((run) => ({ source: run.source, records: services[run.source] }));
      return {
        generated_at: now(),
        total_records: bySource.reduce((sum, item) => sum + item.records, 0),
        records_by_source: bySource,
        records_by_status: {},
        quarantined_records: latest.reduce((sum, run) => sum + run.quarantined, 0),
        records_missing_information: latest.some((run) => run.source === "odrs") ? 50 : 0,
        quarantined_objects: latest.reduce((sum, run) => sum + run.objects_quarantined, 0),
        last_ingest: latest.map((run) => ({ ...run })),
        findings: {
          checks_run: 0,
          open_total: 0,
          open_by_category: Object.fromEntries(FINDING_CATEGORIES.map((c) => [c, 0])) as Record<
            FindingCategory,
            number
          >,
        },
        conflicts_open: null,
        avg_trust_score: null,
        cost_per_check_usd: null,
      };
    },
    getAudit: async (limit = 15) => audit.slice(0, limit),
    getRuns: async () => runs,
    runIngest,
  };
}

export function createDashboardApi(session: Session): DashboardApi {
  return import.meta.env.VITE_API_MODE === "live"
    ? createLiveDashboardApi(session.token)
    : createMockDashboardApi(session.name);
}
