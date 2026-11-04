import type { Clash } from "../../api/client";
import { sourceLabel } from "./Badges";
import { SplitDiff } from "./SplitDiff";
import { displayValue, fieldLabel, formatDate } from "./queue";

interface ClashCardProps {
  clash: Clash;
  busy: boolean;
  onResolve(choice: "source" | "current"): void;
}

export function ClashCard({ clash, busy, onResolve }: ClashCardProps) {
  const label = fieldLabel(clash.ref.field);
  return (
    <article className={`diff-card ${clash.status === "open" ? "" : "is-decided"}`} aria-label={`Source clash on ${label}`}>
      <header className="diff-card-header">
        <div>
          <p className="field-name">{label}</p>
          <h3>Changed in two places</h3>
        </div>
        {clash.status === "resolved" && <span className="decision">resolved</span>}
      </header>
      <p className="clash-base">
        <strong>Before both changes:</strong> {displayValue(clash.base_value)}
      </p>
      <SplitDiff
        left={{ label: "Current in this system", value: displayValue(clash.current_value) }}
        right={{
          source: clash.source,
          when: `Received ${formatDate(clash.detected_at)}`,
          value: displayValue(clash.source_value),
        }}
      />
      {clash.status === "open" && (
        <div className="actions">
          <button type="button" disabled={busy} onClick={() => onResolve("current")}>
            Keep current value
          </button>
          <button type="button" disabled={busy} onClick={() => onResolve("source")}>
            Use {sourceLabel(clash.source)} value
          </button>
        </div>
      )}
    </article>
  );
}
