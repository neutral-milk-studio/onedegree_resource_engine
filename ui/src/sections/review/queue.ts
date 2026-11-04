// Ranking, filtering, and wording for the review list.
import type { Category, Clash, HsdsRecord, QueueRecord, ReviewFinding } from "../../api/client";

export type ListView = "flagged" | "all";
export type Tier = "urgent" | "high" | "medium" | "low";

export const TIERS: Tier[] = ["urgent", "high", "medium", "low"];
export const TIER_LABELS: Record<Tier, string> = {
  urgent: "Urgent",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export interface QueueFilters {
  view: ListView;
  tier: Tier | "";
  category: Category | "";
  source: string;
  search: string;
}

export interface QueueRow extends QueueRecord {
  /** The most important pending finding, if any. */
  finding?: ReviewFinding;
  clash?: Clash;
  issue: string;
  /** Null when nothing about the record needs review. */
  tier: Tier | null;
  score: number;
  reasons: string[];
  sources: string[];
}

export const CATEGORY_LABELS: Record<Category, string> = {
  actively_bad: "Actively bad",
  suspicious: "Suspicious",
  discrepancy: "Discrepancy",
  missing_information: "Missing information",
  check_failed: "Check failed",
  verified: "Verified",
};

const CATEGORY_WEIGHT: Record<Category, number> = {
  actively_bad: 85,
  suspicious: 80,
  discrepancy: 50,
  missing_information: 20,
  check_failed: 15,
  verified: 0,
};
const CLASH_WEIGHT = 55;
const URGENT_SERVICE = /shelter|overnight|emergency|crisis|hotline|eviction|food|grocer|meal|pantry/i;
const SENSITIVE_FIELDS = new Set(["eligibility_description", "service_areas", "status", "schedules", "phones"]);
const DAY_MS = 86_400_000;

const FIELD_LABELS: Record<string, string> = {
  eligibility_description: "Eligibility",
  service_areas: "Service area",
  status: "Status",
  schedules: "Hours",
  phones: "Phone",
  languages: "Languages",
  fees_description: "Fees",
  email: "Email",
  url: "Website",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());
}

export function describeFinding(finding: ReviewFinding): string {
  const evidence = finding.evidence[0];
  const failure = evidence?.error ?? (evidence?.http_status ? `HTTP ${evidence.http_status}` : undefined);
  const label = fieldLabel(finding.ref.field);
  switch (finding.category) {
    case "actively_bad":
      return failure ? `Site problem: ${failure}` : "The organization may have closed";
    case "check_failed":
      return `Couldn't check: ${failure ?? "unknown error"}`;
    case "missing_information":
      return `${label} is missing`;
    default:
      if (finding.ref.op === "add") {
        const name = (finding.proposed_value as { name?: string } | null)?.name;
        return name ? `${label}: the website adds ${name}` : `${label}: the website adds a value`;
      }
      return `${label} differs from the website`;
  }
}

export function missingFields(record: HsdsRecord): string[] {
  const links = record.service_at_locations as { location?: { schedules?: unknown[] } }[] | undefined;
  const hasHours =
    Boolean((record.schedules as unknown[] | undefined)?.length) ||
    Boolean(links?.some((link) => link.location?.schedules?.length));
  const checks: [string, boolean][] = [
    ["Eligibility", Boolean(record.eligibility_description)],
    ["Hours", hasHours],
    ["Phone", Boolean((record.phones as unknown[] | undefined)?.length)],
  ];
  return checks.filter(([, present]) => !present).map(([label]) => label);
}

const tierFor = (score: number): Tier => (score >= 80 ? "urgent" : score >= 50 ? "high" : score >= 25 ? "medium" : "low");

const pluralize = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

function ageInDays(date?: string, now = Date.now()): number | undefined {
  if (!date) return undefined;
  const time = new Date(date).getTime();
  return Number.isNaN(time) ? undefined : Math.floor((now - time) / DAY_MS);
}

/** Scores how much harm a wrong record could cause, and explains the score in plain words. */
function assess(
  record: QueueRecord,
  finding: ReviewFinding | undefined,
  clash: Clash | undefined,
  category: Category | undefined,
  now: number,
) {
  if (!category && !clash) return { score: 0, reasons: [], tier: null };
  const reasons: string[] = [];
  let score = 0;
  if (clash) {
    score = CLASH_WEIGHT;
    reasons.push("A source update conflicts with a change made here");
  }
  if (category && CATEGORY_WEIGHT[category] > score) score = CATEGORY_WEIGHT[category];
  if (category === "actively_bad") reasons.push("People may not be able to reach this service");
  if (category === "discrepancy") reasons.push("The record may be giving people wrong information");
  if (category === "missing_information") reasons.push("People can't see key details");
  if (category === "check_failed") reasons.push("The check didn't finish, so the result is unknown");
  if (URGENT_SERVICE.test(record.name)) {
    score += 20;
    reasons.push("This is an urgent need");
  }
  if (finding && SENSITIVE_FIELDS.has(finding.ref.field) && category === "discrepancy") {
    score += 5;
    reasons.push(`${fieldLabel(finding.ref.field)} decides who can get help`);
  }
  if (finding) score += finding.confidence * 10;
  const age = ageInDays(record.assured_date, now);
  if (age !== undefined && age > 180) {
    score += age > 365 ? 10 : 5;
    reasons.push(`Not verified for ${pluralize(Math.round(age / 30), "month")}`);
  }
  return { score, reasons, tier: tierFor(score) };
}

export function buildRows(
  records: QueueRecord[],
  findings: ReviewFinding[],
  clashes: Clash[] = [],
  now = Date.now(),
): QueueRow[] {
  const pending = new Map<string, ReviewFinding>();
  for (const finding of findings) {
    if (finding.review_state !== "pending") continue;
    const current = pending.get(finding.service_id);
    if (!current || CATEGORY_WEIGHT[finding.category] > CATEGORY_WEIGHT[current.category]) {
      pending.set(finding.service_id, finding);
    }
  }
  const openClashes = new Map(
    clashes.filter((clash) => clash.status === "open").map((clash) => [clash.service_id, clash]),
  );
  return records
    .map((record): QueueRow => {
      const finding = pending.get(record.id);
      const clash = openClashes.get(record.id);
      const checked = record.last_check?.category;
      const decided = !finding && (checked === "discrepancy" || checked === "actively_bad" || checked === "check_failed");
      const category = finding?.category ?? (decided ? undefined : checked);
      const flagged = category && category !== "verified" ? category : undefined;
      const missing = record.missing_fields ?? [];
      const issue = finding
        ? describeFinding(finding)
        : clash
          ? `${fieldLabel(clash.ref.field)} changed in two places`
          : decided
            ? "Reviewed"
            : category === "missing_information"
              ? missing.length
                ? `Missing ${missing.join(", ")}`
                : "Fields are missing from this record"
              : category === "verified"
                ? "Matches the website"
                : "Not checked yet";
      const sources = [
        record.source,
        ...(finding?.evidence.map((item) => item.source_type) ?? []),
        ...(clash ? [clash.source] : []),
      ];
      return {
        ...record,
        finding,
        clash,
        issue,
        sources: [...new Set(sources)],
        ...assess(record, finding, clash, flagged, now),
      };
    })
    .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
}

export function rowCategory(row: QueueRow): Category | undefined {
  if (row.finding) return row.finding.category;
  if (row.issue === "Reviewed" || row.issue === "Not checked yet") return undefined;
  return row.last_check?.category;
}

export function filterRows(rows: QueueRow[], filters: QueueFilters): QueueRow[] {
  const term = filters.search.trim().toLowerCase();
  return rows.filter((row) => {
    if (filters.view === "flagged" && !row.tier) return false;
    if (filters.tier && row.tier !== filters.tier) return false;
    if (filters.category && rowCategory(row) !== filters.category) return false;
    if (filters.source && !row.sources.includes(filters.source)) return false;
    if (term && !`${row.name} ${row.organization_name ?? ""}`.toLowerCase().includes(term)) return false;
    return true;
  });
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not set";
  if (Array.isArray(value)) return value.map(displayValue).join(", ");
  if (typeof value === "object") {
    const name = (value as { name?: unknown }).name;
    return name ? String(name) : JSON.stringify(value);
  }
  return String(value);
}

export function verifiedLabel(value?: string | null): string {
  return value ? `Verified ${formatDate(value)}` : "Never verified";
}

export function formatDate(value?: string | null): string {
  if (!value) return "Never";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
