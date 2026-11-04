// Add a partner: POST /sources/preview (multipart), POST /sources/confirm, GET /sources/sample.
import { ApiError, type Session } from "./client";
import type { IngestRun } from "./dashboard";

export interface ColumnSuggestion {
  column: string;
  target: string;
  confidence: number;
  reason: string;
  samples: string[];
}

export interface Preview {
  upload_id: string;
  filename: string;
  rows: number;
  model_id: string;
  note: string | null;
  cost_usd: number;
  columns: ColumnSuggestion[];
  targets: Record<string, string>;
}

export interface QuarantineItem {
  id: string;
  native_id: string;
  level: string;
  kind: string;
  object_id: string | null;
  reasons: string[];
}

export interface ConfirmResult {
  source: string;
  run: IngestRun;
  quarantine: QuarantineItem[];
}

export interface PartnersApi {
  readonly isDemo: boolean;
  sampleFile(): Promise<File>;
  preview(file: File): Promise<Preview>;
  confirm(uploadId: string, partnerName: string, mapping: Record<string, string>, defaultState: string): Promise<ConfirmResult>;
}

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

async function call<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, ...init?.headers } });
  } catch {
    throw new ApiError(0, "Can't reach the Resource Engine API. Is it running?");
  }
  if (!response.ok) {
    const body: { detail?: unknown } = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`);
  }
  return (path.endsWith("/sample") ? await response.text() : await response.json()) as T;
}

export function createLivePartnersApi(token: string): PartnersApi {
  return {
    isDemo: false,
    async sampleFile() {
      const text = await call<string>(token, "/sources/sample");
      return new File([text], "county_food_pantries.csv", { type: "text/csv" });
    },
    preview(file) {
      const form = new FormData();
      form.append("file", file);
      return call<Preview>(token, "/sources/preview", { method: "POST", body: form });
    },
    confirm(uploadId, partnerName, mapping, defaultState) {
      return call<ConfirmResult>(token, "/sources/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upload_id: uploadId, partner_name: partnerName, mapping, default_state: defaultState }),
      });
    },
  };
}

const TARGETS: Record<string, string> = {
  organization_name: "Organization name",
  service_name: "Service name",
  description: "Service description",
  phone: "Phone",
  email: "Email",
  website: "Website",
  address_1: "Street address",
  city: "City",
  state: "State",
  postal_code: "ZIP code",
  hours: "Hours",
  eligibility: "Who can get help",
  languages: "Languages",
  service_area: "Service area",
  packed: "Several fields in one column",
  ignore: "Don't import",
};

/** Demo mode: a fixed suggestion and result, labelled as demo data by the shell. */
export function createMockPartnersApi(actor: string): PartnersApi {
  return {
    isDemo: true,
    sampleFile: async () => new File(["Pantry Name,Phone #\nDemo Pantry,(707) 555-0101\n"], "demo.csv", { type: "text/csv" }),
    async preview(file) {
      if (!file.name.toLowerCase().endsWith(".csv")) throw new ApiError(415, "Upload a CSV file.");
      return {
        upload_id: "demo-upload",
        filename: file.name,
        rows: 7,
        model_id: "offline-rules",
        note: null,
        cost_usd: 0,
        targets: TARGETS,
        columns: [
          { column: "Pantry Name", target: "service_name", confidence: 0.9, reason: "the header names this field", samples: ["Demo Pantry"] },
          { column: "Phone #", target: "phone", confidence: 0.83, reason: "values look like phone numbers", samples: ["(707) 555-0101"] },
          { column: "Notes", target: "ignore", confidence: 0.3, reason: "no match; check this column", samples: ["bring a bag"] },
        ],
      };
    },
    async confirm(_uploadId, partnerName) {
      if (!partnerName.trim()) throw new ApiError(422, "Give the partner a name.");
      const now = new Date().toISOString();
      return {
        source: `partner_${partnerName.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
        run: {
          id: "demo-run", source: "partner_demo", status: "succeeded", triggered_by: actor, started_at: now, finished_at: now,
          elapsed_ms: 180, received: 7, stored: 7, quarantined: 0, services_stored: 7, objects_total: 40,
          objects_kept: 38, objects_quarantined: 2, error: null,
        },
        quarantine: [
          { id: "q1", native_id: "demo/mobile-pantry", level: "object", kind: "address", object_id: null, reasons: ["(root): 'address_1' is a required property"] },
          { id: "q2", native_id: "demo/food-share", level: "object", kind: "phone", object_id: null, reasons: ["number: 'call the office' contains no digits (Resource Engine quality rule)"] },
        ],
      };
    },
  };
}

export function createPartnersApi(session: Session): PartnersApi {
  return import.meta.env.VITE_API_MODE === "live" ? createLivePartnersApi(session.token) : createMockPartnersApi(session.name);
}

const FIELD_WORDS: Record<string, string> = {
  address_1: "street address",
  city: "city",
  postal_code: "ZIP code",
  state_province: "state",
  number: "phone number",
  name: "name",
};

/** A validator message in plain language, e.g. "(root): 'address_1' is a required property". */
export function plainReason(kind: string, reason: string): string {
  const required = reason.match(/'([a-z_0-9]+)' is a required property/);
  if (required) return `The ${FIELD_WORDS[required[1]] ?? required[1].replace(/_/g, " ")} is missing.`;
  const noDigits = reason.match(/'(.*)' contains no digits/);
  if (noDigits) return `“${noDigits[1]}” isn't a phone number.`;
  if (reason.includes("no organization or service name")) return "The row has no organization or service name.";
  return `${kind}: ${reason}`;
}
