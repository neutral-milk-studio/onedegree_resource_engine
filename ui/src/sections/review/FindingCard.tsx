import { useState } from "react";

import type { ReviewFinding } from "../../api/client";
import { sourceLabel } from "./Badges";
import { countDifferences, DifferenceCount, SplitDiff, type SplitSide } from "./SplitDiff";
import { describeFinding, displayValue, fieldLabel, formatDate, verifiedLabel } from "./queue";

const STATUS_OPTIONS = ["temporarily closed", "inactive", "defunct", "active"];

function editableText(finding: ReviewFinding): string {
  const value = finding.proposed_value as unknown;
  if (value && typeof value === "object") {
    return String((value as { name?: unknown }).name ?? "");
  }
  return value === null || value === undefined ? "" : String(value);
}

function toEditedValue(finding: ReviewFinding, text: string): unknown {
  const value = finding.proposed_value as unknown;
  return value && typeof value === "object" ? { ...value, name: text } : text;
}

/** What the record would say if the proposal were applied. */
function proposedText(finding: ReviewFinding): string {
  if (finding.ref.op === "add") {
    const items = Array.isArray(finding.current_value) ? finding.current_value.map(displayValue) : [];
    return [...items, displayValue(finding.proposed_value)].join(", ");
  }
  return displayValue(finding.proposed_value);
}

interface FindingCardProps {
  finding: ReviewFinding;
  currentDate?: string;
  currentSource?: string;
  busy: boolean;
  editing: boolean;
  onEditingChange(editing: boolean): void;
  onApply(editedValue?: unknown): void;
  onDismiss(): void;
}

export function FindingCard({
  finding,
  currentDate,
  currentSource,
  busy,
  editing,
  onEditingChange,
  onApply,
  onDismiss,
}: FindingCardProps) {
  const isStatus = finding.ref.field === "status";
  const [text, setText] = useState(() => editableText(finding) || (isStatus ? STATUS_OPTIONS[0] : ""));
  const hasProposal = finding.proposed_value !== null && finding.proposed_value !== undefined;
  const label = fieldLabel(finding.ref.field);
  const pending = finding.review_state === "pending";
  const evidence = finding.evidence[0];
  const sourceName = sourceLabel(evidence.source_type).toLowerCase();
  const failure = evidence.error ?? (evidence.http_status ? `HTTP ${evidence.http_status}` : null);

  const left: SplitSide = {
    source: currentSource,
    label: "Current record",
    when: verifiedLabel(currentDate),
    value: displayValue(finding.current_value),
  };
  const right: SplitSide = {
    source: evidence.source_type,
    when: `Checked ${formatDate(evidence.fetched_at)}`,
    link: evidence.url,
    value: hasProposal ? proposedText(finding) : "",
  };
  const differences = hasProposal ? countDifferences(left.value, right.value) : 0;

  return (
    <article className={`diff-card ${pending ? "" : "is-decided"}`} aria-label={describeFinding(finding)}>
      <header className="diff-card-header">
        <div>
          <p className="field-name">{label}</p>
          <h3>{describeFinding(finding).replace(`${label}: `, "").replace(/^./, (first) => first.toUpperCase())}</h3>
        </div>
        {pending ? (
          differences > 0 && <DifferenceCount count={differences} />
        ) : (
          <span className="decision">{finding.review_state}</span>
        )}
      </header>

      <SplitDiff
        left={left}
        right={right}
        rightBody={
          failure ? (
            <p className="split-failure">
              {failure}
              <span>The AI doesn't propose a value here. You decide what to do.</span>
            </p>
          ) : undefined
        }
      />

      {evidence.snippet && (
        <figure className="quote">
          <figcaption>Text on the {sourceName}</figcaption>
          <blockquote>{evidence.snippet}</blockquote>
        </figure>
      )}
      {finding.evidence.length > 1 && (
        <p className="fine-print">{finding.evidence.length - 1} more sources support this finding.</p>
      )}
      <p className="fine-print">
        Confidence {Math.round(finding.confidence * 100)}%{finding.model_id ? ` · Model ${finding.model_id}` : ""}
      </p>

      {pending && editing && (
        <form
          className="edit-form"
          onSubmit={(event) => {
            event.preventDefault();
            onApply(toEditedValue(finding, text));
          }}
        >
          <label htmlFor={`edit-${finding.id}`}>New value for {label.toLowerCase()}</label>
          {isStatus ? (
            <select id={`edit-${finding.id}`} value={text} onChange={(event) => setText(event.target.value)}>
              {STATUS_OPTIONS.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          ) : (
            <textarea
              id={`edit-${finding.id}`}
              value={text}
              rows={3}
              autoFocus
              onChange={(event) => setText(event.target.value)}
            />
          )}
          <div className="actions">
            <button type="submit" className="primary" disabled={busy || !text.trim()}>
              Save change
            </button>
            <button type="button" disabled={busy} onClick={() => onEditingChange(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {pending && !editing && (
        <div className="actions">
          {hasProposal && (
            <button type="button" className="primary" disabled={busy} onClick={() => onApply()}>
              Apply {sourceName} value
            </button>
          )}
          <button
            type="button"
            className={hasProposal ? undefined : "primary"}
            disabled={busy}
            onClick={() => onEditingChange(true)}
          >
            {hasProposal ? "Edit" : `Set ${label.toLowerCase()}`}
          </button>
          <button type="button" disabled={busy} onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      )}
    </article>
  );
}
