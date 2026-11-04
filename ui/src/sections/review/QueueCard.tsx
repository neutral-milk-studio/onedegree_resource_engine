import { SourceBadge, TierBadge } from "./Badges";
import { verifiedLabel, type QueueRow } from "./queue";

interface QueueCardProps {
  row: QueueRow;
  selected: boolean;
  onSelect(): void;
}

export function QueueCard({ row, selected, onSelect }: QueueCardProps) {
  return (
    <li>
      <button
        type="button"
        className={`queue-card ${row.tier ? `queue-card-${row.tier}` : "queue-card-none"}`}
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
      >
        <span className="queue-card-top">
          {row.tier ? <TierBadge tier={row.tier} /> : <span className="tier tier-none">{row.issue}</span>}
          <span className="queue-card-sources">
            {row.sources.map((source) => (
              <SourceBadge key={source} source={source} />
            ))}
          </span>
        </span>
        <span className="queue-card-title">{row.name}</span>
        <span className="queue-card-org">{row.organization_name}</span>
        {row.tier && <span className="queue-card-issue">{row.issue}</span>}
        <span className="queue-card-meta">{verifiedLabel(row.assured_date)}</span>
      </button>
    </li>
  );
}
