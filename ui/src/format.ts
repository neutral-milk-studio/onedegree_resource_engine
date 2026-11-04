// Plain-language formatting shared by the dashboard pages.

export const SOURCE_LABELS: Record<string, string> = {
  odrs: "One Degree (ODRS)",
  ndp: "211 (NDP)",
  spreadsheet: "Partner spreadsheet",
  community: "Community submission",
};

export const sourceLabel = (source: string) => SOURCE_LABELS[source] ?? source;

export const CATEGORY_LABELS: Record<string, string> = {
  actively_bad: "Actively bad",
  suspicious: "Suspicious",
  discrepancy: "Doesn't match website",
  missing_information: "Missing information",
  check_failed: "Couldn't check",
  verified: "Verified",
};

export const CATEGORY_HELP: Record<string, string> = {
  actively_bad: "Website gone or organization appears closed",
  suspicious: "Two or more scam risk signals. Flagged for review, not proven",
  discrepancy: "A fact on the website differs from the record",
  missing_information: "The record is missing a field the website has",
  check_failed: "The website couldn't be read; nothing was guessed",
  verified: "Records whose latest website check matched",
};

const number = new Intl.NumberFormat("en-US");
export const formatCount = (value: number) => number.format(value);

export function formatUsd(value: number): string {
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
