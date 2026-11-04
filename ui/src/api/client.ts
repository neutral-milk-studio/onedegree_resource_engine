// REST API client. This is the only data path for the review tool.
import type { components } from "./schema";
import { createLiveApi, liveSignIn } from "./live";
import { createMockApi, mockSignIn } from "./mock";

type Schemas = components["schemas"];

export type Category = Schemas["Category"];
export type RecordSummary = Schemas["RecordSummary"];
export type RecordDetail = Schemas["RecordDetail"];
export type ReviewFinding = Schemas["ReviewFinding"];
export type ReviewEvidence = Schemas["ReviewEvidence"];
export type Clash = Schemas["Clash"];
export type FieldChange = Schemas["FieldChange"];
export type IngestCounts = Schemas["IngestCounts"];
export type HsdsRecord = RecordDetail["record"];

export interface ValidationProblem {
  path: string;
  message: string;
}

/** A failed request, with the field-level problems when the server sends them. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly problems: ValidationProblem[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface SourceValue {
  source: string;
  value?: string;
  observed_at?: string;
}

/**
 * A review list row. The optional fields help rank and describe records, and they aren't in the
 * API contract yet. Without them, ranking ignores age and the card shows a general message.
 */
export interface QueueRecord extends RecordSummary {
  assured_date?: string;
  missing_fields?: string[];
}

export interface Session {
  name: string;
  email: string;
  token: string;
}

export interface FieldComparison {
  field: string;
  agree: boolean;
  note?: string;
  values: SourceValue[];
}

export interface FlaggedFilters {
  category?: Category;
  source?: string;
}

export interface ReviewApi {
  /** Every ingested record with its findings. The review list filters and ranks these. */
  listFlagged(filters?: FlaggedFilters): Promise<QueueRecord[]>;
  listAllFindings(): Promise<ReviewFinding[]>;
  listClashes(): Promise<Clash[]>;
  /** One record with its findings, clashes, and version. */
  getFindings(recordId: string): Promise<RecordDetail>;
  /** Applies a proposed value, or the edited value when one is sent. */
  applyChange(findingId: string, expectedVersion: number, editedValue?: unknown): Promise<ReviewFinding>;
  dismissFinding(findingId: string, reason?: string): Promise<ReviewFinding>;
  patchRecord(recordId: string, expectedVersion: number, changes: FieldChange[]): Promise<void>;
  resolveClash(
    clashId: string,
    expectedVersion: number,
    choice: "source" | "current" | "custom",
    customValue?: unknown,
  ): Promise<Clash>;
  /** Cross-source comparison for one record. The review API doesn't serve this yet. */
  getComparison(recordId: string): Promise<FieldComparison[]>;
  /** True when simulated data is in use, so the UI can say so. */
  readonly isDemo: boolean;
}

const isLive = () => import.meta.env.VITE_API_MODE === "live";

export function createApi(session: Session): ReviewApi {
  return isLive() ? createLiveApi(session.token) : createMockApi(session.name);
}

/** Signs a reviewer in. Throws an ApiError with status 401 for bad credentials. */
export async function signIn(email: string, password: string): Promise<Session> {
  return isLive() ? liveSignIn(email, password) : mockSignIn(email, password);
}
