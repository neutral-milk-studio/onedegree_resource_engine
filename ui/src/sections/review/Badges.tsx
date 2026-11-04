import { TIER_LABELS, type Tier } from "./queue";

const SOURCE_LABELS: Record<string, string> = {
  ndp: "211 NDP",
  odrs: "ODRS",
  spreadsheet: "Spreadsheet",
  website: "Website",
  facebook: "Facebook",
  directory: "Directory",
  specialist: "Specialist",
  phone: "Phone",
};

export const sourceLabel = (source: string): string => SOURCE_LABELS[source] ?? source;

export function SourceBadge({ source }: { source: string }) {
  return <span className={`source source-${source}`}>{sourceLabel(source)}</span>;
}

export function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span className={`tier tier-${tier}`}>
      <span aria-hidden="true" className="tier-dot" />
      {TIER_LABELS[tier]}
    </span>
  );
}
