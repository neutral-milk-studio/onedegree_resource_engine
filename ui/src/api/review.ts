// Review queue data: GET /review/findings, POST /review/findings/{id}/apply|dismiss,
// POST /maintenance/run. Live mode calls the API; demo mode uses a small simulated queue.
import type { AuditEntry } from "./dashboard";
import { ApiError, type Session, type ValidationProblem } from "./client";

export type QueueCategory = "actively_bad" | "suspicious" | "discrepancy" | "missing_information" | "check_failed";

export interface Trust {
  score: number | null;
  level: "high" | "medium" | "low" | "unknown";
  badge: string;
  checked_at: string | null;
}

export interface QueueEvidence {
  kind: "quote" | "http_signal";
  source_type: string;
  url: string;
  fetched_at: string;
  snippet?: string | null;
  http_status?: number | null;
  error?: string | null;
}

export interface QueueFinding {
  id: string;
  service_id: string;
  category: QueueCategory;
  current_value: unknown;
  proposed_value: unknown;
  evidence: QueueEvidence[];
  confidence: number;
  model_id: string | null;
  created_at: string;
  review_state: "pending" | "applied" | "edited" | "dismissed";
  service_name: string;
  organization_name: string | null;
  source: string;
  field: string | null;
  field_label: string;
  harm_rank: number;
  harm_label: string;
  record_version: number;
  trust: Trust;
  risk_signals?: string[];
  seconds_to_fix?: number | null;
}

export interface QueueList {
  total_items: number;
  counts_by_category: Record<string, number>;
  contents: QueueFinding[];
}

export interface Decided {
  finding: QueueFinding;
  record_version: number;
  audit_entry: AuditEntry;
}

export interface MaintenanceReport {
  checked: number;
  by_category: Record<string, number>;
  findings_created: number;
  findings_refreshed: number;
  model_calls: number;
  cached: number;
  cost_usd: number;
  model_id: string;
  elapsed_ms: number;
}

export interface ReviewQueueApi {
  readonly isDemo: boolean;
  listQueue(category?: QueueCategory): Promise<QueueList>;
  apply(finding: QueueFinding, editedValue: unknown, secondsToFix: number): Promise<Decided>;
  dismiss(finding: QueueFinding, reason: string | undefined, secondsToFix: number): Promise<Decided>;
  runChecks(): Promise<MaintenanceReport>;
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
  if (Array.isArray(body.detail)) {
    throw new ApiError(response.status, body.message ?? "The change didn't pass validation.", body.detail);
  }
  throw new ApiError(response.status, body.detail ?? body.message ?? `Request failed (${response.status}).`);
}

export function createLiveReviewApi(token: string): ReviewQueueApi {
  const post = <T>(path: string, body: unknown) =>
    request<T>(token, path, { method: "POST", body: JSON.stringify(body) });
  return {
    isDemo: false,
    listQueue: (category) =>
      request<QueueList>(token, `/review/findings?sort=harm${category ? `&category=${category}` : ""}`),
    apply: (finding, editedValue, seconds) =>
      post<Decided>(`/review/findings/${finding.id}/apply`, {
        expected_version: finding.record_version,
        edited_value: editedValue ?? null,
        seconds_to_fix: seconds,
      }),
    dismiss: (finding, reason, seconds) =>
      post<Decided>(`/review/findings/${finding.id}/dismiss`, { reason: reason ?? null, seconds_to_fix: seconds }),
    runChecks: () => post<MaintenanceReport>("/maintenance/run", {}),
  };
}

/** Demo mode: three simulated findings, clearly labelled as demo data by the shell. */
export function createMockReviewApi(actor: string): ReviewQueueApi {
  const now = new Date().toISOString();
  const trust: Trust = { score: 65, level: "medium", badge: "1 open issue · checked today", checked_at: now };
  const base = {
    evidence: [] as QueueEvidence[],
    confidence: 0.9,
    model_id: "offline-rules",
    created_at: now,
    review_state: "pending" as const,
    source: "ndp",
    record_version: 1,
    trust,
  };
  let findings: QueueFinding[] = [
    {
      ...base,
      id: "demo-1",
      service_id: "demo-s1",
      category: "actively_bad",
      current_value: null,
      proposed_value: null,
      evidence: [{ kind: "http_signal", source_type: "website", url: "https://pantry.example", fetched_at: now, error: "DNS lookup failed (NXDOMAIN)" }],
      model_id: null,
      service_name: "Demo Pantry",
      organization_name: "Demo Org",
      field: "website",
      field_label: "Website",
      harm_rank: 0,
      harm_label: "Urgent: people may be sent to a closed or dead service",
    },
    {
      ...base,
      id: "demo-4",
      service_id: "demo-s4",
      category: "suspicious",
      current_value: "active",
      proposed_value: null,
      evidence: [{ kind: "quote", source_type: "website", url: "https://rent-relief.example", fetched_at: now, snippet: "Pay a $99 processing fee to reserve your grant." }],
      service_name: "Demo Rent Grants",
      organization_name: "Demo Relief Now",
      source: "community",
      field: "listing",
      field_label: "Listing",
      harm_rank: 1,
      harm_label: "Urgent: possible scam, flagged for review",
      risk_signals: ["Asks for a $99 fee before helping", "Website domain registered 10 days ago"],
    },
    {
      ...base,
      id: "demo-2",
      service_id: "demo-s2",
      category: "discrepancy",
      current_value: "(415) 555-0100",
      proposed_value: "(415) 555-0199",
      evidence: [{ kind: "quote", source_type: "website", url: "https://clinic.example", fetched_at: now, snippet: "Call our new line at (415) 555-0199." }],
      service_name: "Demo Clinic",
      organization_name: "Demo Health",
      field: "phone",
      field_label: "Phone",
      harm_rank: 2,
      harm_label: "High: people may call or arrive at the wrong time or number",
    },
    {
      ...base,
      id: "demo-3",
      service_id: "demo-s3",
      category: "missing_information",
      current_value: null,
      proposed_value: ["English", "Spanish"],
      evidence: [{ kind: "quote", source_type: "website", url: "https://legal.example", fetched_at: now, snippet: "English and Spanish" }],
      service_name: "Demo Legal Aid",
      organization_name: "Demo Legal",
      field: "languages",
      field_label: "Languages",
      harm_rank: 4,
      harm_label: "Low: information is missing",
    },
  ];
  const decide = (finding: QueueFinding, state: QueueFinding["review_state"], summary: string): Decided => {
    findings = findings.filter((item) => item.id !== finding.id);
    return {
      finding: { ...finding, review_state: state },
      record_version: finding.record_version + 1,
      audit_entry: {
        id: crypto.randomUUID(),
        at: new Date().toISOString(),
        actor,
        action: state === "dismissed" ? "dismiss_finding" : "apply_finding",
        change_source: "specialist",
        entity_type: "service",
        entity_id: finding.service_id,
        field: finding.field,
        old_value: finding.current_value,
        new_value: finding.proposed_value,
        summary,
      },
    };
  };
  return {
    isDemo: true,
    async listQueue(category) {
      const counts: Record<string, number> = {};
      findings.forEach((f) => (counts[f.category] = (counts[f.category] ?? 0) + 1));
      const contents = findings.filter((f) => !category || f.category === category);
      return { total_items: contents.length, counts_by_category: counts, contents };
    },
    async apply(finding, editedValue, seconds) {
      if ((finding.category === "actively_bad" || finding.category === "suspicious") && !editedValue) {
        throw new ApiError(422, "This finding has no proposed value. Choose a value to apply.", [
          { path: "status", message: "a value is required" },
        ]);
      }
      return decide(finding, editedValue ? "edited" : "applied", `${actor} updated ${finding.field_label.toLowerCase()} on “${finding.service_name}” (${Math.round(seconds)} s)`);
    },
    async dismiss(finding) {
      return decide(finding, "dismissed", `${actor} kept the current ${finding.field_label.toLowerCase()} on “${finding.service_name}”`);
    },
    async runChecks() {
      return { checked: 3, by_category: { discrepancy: 1 }, findings_created: 0, findings_refreshed: 3, model_calls: 0, cached: 0, cost_usd: 0, model_id: "offline-rules", elapsed_ms: 50 };
    },
  };
}

export function createReviewQueueApi(session: Session): ReviewQueueApi {
  return import.meta.env.VITE_API_MODE === "live" ? createLiveReviewApi(session.token) : createMockReviewApi(session.name);
}

const DAY_NAMES: Record<string, string> = { MO: "Mon", TU: "Tue", WE: "Wed", TH: "Thu", FR: "Fri", SA: "Sat", SU: "Sun" };

function clock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "PM" : "AM";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return `${hour}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** A field value in plain language, for the side-by-side view. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not in the record";
  if (Array.isArray(value)) {
    if (value.length && typeof value[0] === "object") {
      return (value as { byday: string; opens_at: string; closes_at: string }[])
        .map((slot) => `${slot.byday.split(",").map((d) => DAY_NAMES[d] ?? d).join(", ")}: ${clock(slot.opens_at)} – ${clock(slot.closes_at)}`)
        .join("\n");
    }
    return value.join(", ");
  }
  return String(value);
}
