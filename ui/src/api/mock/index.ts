// In-memory implementation of the review API, used until the backend is ready.
// It follows contracts/api/openapi.yaml. Findings and clashes in it are simulated.
import type {
  Clash,
  FieldChange,
  FieldComparison,
  HsdsRecord,
  QueueRecord,
  RecordSummary,
  ReviewApi,
  ReviewFinding,
  Session,
  ValidationProblem,
} from "../client";
import { ApiError } from "../client";
import demoData from "./demo-data.json";

export const MOCK_REVIEWER = "Demo reviewer";

/** Fictional accounts for the demo. They only exist in mock mode. */
export const DEMO_ACCOUNTS = [
  { email: "sam.rivera@example.org", name: "Sam Rivera", password: "review-demo" },
  { email: "jordan.lee@example.org", name: "Jordan Lee", password: "review-demo" },
];

export async function mockSignIn(email: string, password: string): Promise<Session> {
  const account = DEMO_ACCOUNTS.find((item) => item.email === email.trim().toLowerCase());
  if (!account || account.password !== password) {
    throw new ApiError(401, "That email or password isn't right.");
  }
  return { email: account.email, name: account.name, token: "mock-token" };
}

const STATUSES = ["active", "inactive", "defunct", "temporarily closed"];
const TIME_WITH_OFFSET = /^([01]\d|2[0-3]):[0-5]\d(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$/;

interface Stored {
  service: HsdsRecord;
  version: number;
}

type Check = NonNullable<RecordSummary["last_check"]>;

const clone = <T>(value: T): T => structuredClone(value);

function collectProblems(value: unknown, path: string, problems: ValidationProblem[]): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectProblems(item, `${path}/${index}`, problems));
  } else if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      if (key === "x_resource_engine") continue;
      const childPath = path ? `${path}/${key}` : key;
      const isTime = (key === "opens_at" || key === "closes_at") && typeof child === "string";
      if (isTime && !TIME_WITH_OFFSET.test(child)) {
        problems.push({ path: childPath, message: "Use HH:MM with a UTC offset, such as 09:00-08:00." });
      }
      collectProblems(child, childPath, problems);
    }
  } else if (value === "") {
    problems.push({ path, message: "This can't be empty. Clear the field instead." });
  }
}

/** A small subset of HSDS validation, so mock mode behaves like the server. */
export function validateRecord(record: HsdsRecord): ValidationProblem[] {
  const problems: ValidationProblem[] = [];
  if (!record.name) {
    problems.push({ path: "name", message: "A service needs a name." });
  }
  if (!STATUSES.includes(String(record.status))) {
    problems.push({ path: "status", message: `Status must be one of: ${STATUSES.join(", ")}.` });
  }
  for (const key of ["minimum_age", "maximum_age"]) {
    if (record[key] !== undefined && typeof record[key] !== "number") {
      problems.push({ path: key, message: "Enter a number." });
    }
  }
  collectProblems(record, "", problems);
  return problems;
}

function missingFields(record: HsdsRecord): string[] {
  const links = record.service_at_locations as { location?: { schedules?: unknown[] } }[] | undefined;
  const hasHours =
    Boolean((record.schedules as unknown[] | undefined)?.length) ||
    Boolean(links?.some((link) => link.location?.schedules?.length));
  const checks: [string, boolean][] = [
    ["eligibility", Boolean(record.eligibility_description)],
    ["hours", hasHours],
    ["phone", Boolean((record.phones as unknown[] | undefined)?.length)],
  ];
  return checks.filter(([, present]) => !present).map(([label]) => label);
}

function findObject(value: unknown, id: string): Record<string, unknown> | undefined {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findObject(item, id);
      if (found) return found;
    }
  } else if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    if (object.id === id) return object;
    return findObject(Object.values(object), id);
  }
  return undefined;
}

function applyToRecord(record: HsdsRecord, change: FieldChange, actor: string): HsdsRecord {
  const updated = clone(record);
  const target = findObject(updated, change.ref.entity_id);
  if (!target) {
    throw new ApiError(404, "That object isn't part of this record.");
  }
  const { field, op = "set" } = change.ref;
  const previous = target[field];
  if (op === "add") {
    const list = (target[field] as Record<string, unknown>[] | undefined) ?? [];
    target[field] = [...list, { id: crypto.randomUUID(), ...(change.value as object) }];
  } else if (op === "remove") {
    const list = (target[field] as Record<string, unknown>[] | undefined) ?? [];
    target[field] = list.filter((item) => item.id !== change.value);
  } else if (change.value === null || change.value === undefined) {
    delete target[field];
  } else {
    target[field] = change.value;
  }
  const history = (updated.metadata as unknown[] | undefined) ?? [];
  updated.metadata = [
    ...history,
    {
      id: crypto.randomUUID(),
      resource_id: change.ref.entity_id,
      resource_type: change.ref.entity_type,
      last_action_date: new Date().toISOString(),
      last_action_type: "update",
      field_name: field,
      previous_value: JSON.stringify(previous ?? null),
      replacement_value: JSON.stringify(target[field] ?? null),
      updated_by: actor,
    },
  ];
  return updated;
}

export function createMockApi(reviewer: string = MOCK_REVIEWER): ReviewApi {
  const data = clone(demoData) as unknown as {
    records: Stored[];
    findings: ReviewFinding[];
    clashes: Clash[];
    checks: Record<string, Check>;
    comparisons: Record<string, FieldComparison[]>;
  };
  const records = new Map(data.records.map((item) => [String(item.service.id), item]));
  const findings = new Map(data.findings.map((item) => [item.id, item]));
  const clashes = new Map(data.clashes.map((item) => [item.id, item]));

  const stored = (recordId: string): Stored => {
    const item = records.get(recordId);
    if (!item) throw new ApiError(404, "Record not found.");
    return item;
  };

  const save = (recordId: string, expectedVersion: number, changes: FieldChange[]) => {
    const item = stored(recordId);
    if (item.version !== expectedVersion) {
      throw new ApiError(409, "Someone changed this record. Reload it and try again.");
    }
    const updated = changes.reduce(
      (service, change) => applyToRecord(service, change, reviewer),
      item.service,
    );
    const problems = validateRecord(updated);
    if (problems.length) {
      throw new ApiError(422, "The change didn't pass validation.", problems);
    }
    records.set(recordId, { service: updated, version: item.version + 1 });
  };

  const openCount = (recordId: string) =>
    [...findings.values()].filter(
      (item) => item.service_id === recordId && item.review_state === "pending",
    ).length;

  const summary = ({ service, version }: Stored): QueueRecord => {
    const id = String(service.id);
    const source = service.x_resource_engine as { source: { system: string } };
    const organization = service.organization as { name?: string } | undefined;
    return {
      id,
      name: String(service.name),
      organization_name: organization?.name ?? null,
      source: source.source.system,
      status: String(service.status),
      version,
      open_findings: openCount(id),
      last_check: data.checks[id] ?? null,
      assured_date: service.assured_date as string | undefined,
      missing_fields: missingFields(service),
    };
  };

  return {
    isDemo: true,
    async listFlagged(filters = {}) {
      return [...records.values()]
        .map(summary)
        .filter((row) => !filters.source || row.source === filters.source)
        .filter((row) => !filters.category || row.last_check?.category === filters.category);
    },
    async listAllFindings() {
      return clone([...findings.values()]);
    },
    async listClashes() {
      return clone([...clashes.values()]);
    },
    async getFindings(recordId) {
      const item = stored(recordId);
      return {
        record: clone(item.service),
        version: item.version,
        findings: clone([...findings.values()].filter((row) => row.service_id === recordId)),
        clashes: clone([...clashes.values()].filter((row) => row.service_id === recordId)),
        last_check: data.checks[recordId] ?? null,
      };
    },
    async applyChange(findingId, expectedVersion, editedValue) {
      const finding = findings.get(findingId);
      if (!finding) throw new ApiError(404, "Finding not found.");
      const edited = editedValue !== undefined && editedValue !== null;
      const value = edited ? editedValue : finding.proposed_value;
      if (value === null || value === undefined) {
        throw new ApiError(422, "This finding has no proposed value, so enter one.");
      }
      save(finding.service_id, expectedVersion, [{ ref: finding.ref, value }]);
      finding.review_state = edited ? "edited" : "applied";
      return clone(finding);
    },
    async dismissFinding(findingId) {
      const finding = findings.get(findingId);
      if (!finding) throw new ApiError(404, "Finding not found.");
      finding.review_state = "dismissed";
      return clone(finding);
    },
    async patchRecord(recordId, expectedVersion, changes) {
      save(recordId, expectedVersion, changes);
    },
    async resolveClash(clashId, expectedVersion, choice, customValue) {
      const clash = clashes.get(clashId);
      if (!clash) throw new ApiError(404, "Clash not found.");
      const value = { source: clash.source_value, current: clash.current_value, custom: customValue }[choice];
      save(clash.service_id, expectedVersion, [{ ref: clash.ref, value }]);
      clash.status = "resolved";
      return clone(clash);
    },
    async getComparison(recordId) {
      return clone(data.comparisons[recordId] ?? []);
    },
  };
}
