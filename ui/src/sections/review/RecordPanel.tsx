import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { HsdsRecord, ReviewApi, ReviewFinding, ValidationProblem } from "../../api/client";
import { ApiError } from "../../api/client";
import { SourceBadge, TierBadge } from "./Badges";
import { ClashCard } from "./ClashCard";
import { FindingCard } from "./FindingCard";
import { countDifferences, DifferenceCount, SplitDiff } from "./SplitDiff";
import { displayValue, fieldLabel, formatDate, missingFields, verifiedLabel, type QueueRow } from "./queue";

const FieldsEditor = lazy(() => import("./FieldsEditor").then((module) => ({ default: module.FieldsEditor })));

interface HistoryEntry {
  id: string;
  field_name: string;
  previous_value: string;
  replacement_value: string;
  updated_by: string;
  last_action_date: string;
}

function parseHistoryValue(value: string): string {
  try {
    return displayValue(JSON.parse(value));
  } catch {
    return value;
  }
}

function History({ record }: { record: HsdsRecord }) {
  const entries = [...((record.metadata as HistoryEntry[] | undefined) ?? [])].reverse();
  return (
    <section aria-labelledby="history-heading">
      <h3 id="history-heading" className="section-title">
        Change history
      </h3>
      {entries.length === 0 ? (
        <p className="muted">No changes yet.</p>
      ) : (
        <ol className="history">
          {entries.map((entry) => (
            <li key={entry.id}>
              <p>
                <strong>{fieldLabel(entry.field_name)}</strong> changed by {entry.updated_by} on{" "}
                {formatDate(entry.last_action_date)}
              </p>
              <SplitDiff
                left={{ label: "From", value: parseHistoryValue(entry.previous_value) }}
                right={{ label: "To", value: parseHistoryValue(entry.replacement_value) }}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function SaveError({ message, problems }: { message: string; problems: ValidationProblem[] }) {
  return (
    <div className="alert" role="alert">
      <strong>{message} Nothing was saved.</strong>
      {problems.length > 0 && (
        <ul>
          {problems.map((problem) => (
            <li key={`${problem.path}${problem.message}`}>
              {problem.path ? `${fieldLabel(problem.path)}: ` : ""}
              {problem.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface RecordPanelProps {
  api: ReviewApi;
  recordId: string;
  row?: QueueRow;
  onNext?(): void;
}

export function RecordPanel({ api, recordId, row, onNext }: RecordPanelProps) {
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ["record", recordId], queryFn: () => api.getFindings(recordId) });
  const comparison = useQuery({ queryKey: ["comparison", recordId], queryFn: () => api.getComparison(recordId) });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<"apply" | "dismiss" | null>(null);
  // Remounting the editor after a failed save drops the rejected value from its cells.
  const [editorResets, setEditorResets] = useState(0);
  const openedAt = useRef(Date.now());
  const steps = useRef(0);

  useEffect(() => {
    openedAt.current = Date.now();
    steps.current = 0;
    setEditingId(null);
    setSaved(null);
    setConfirming(null);
  }, [recordId]);

  const mutation = useMutation({
    mutationFn: (run: () => Promise<unknown>) => {
      steps.current += 1;
      return run();
    },
    onSuccess: () => {
      const seconds = Math.max(1, Math.round((Date.now() - openedAt.current) / 1000));
      const count = steps.current;
      setSaved(`Saved in ${seconds} s and ${count} ${count === 1 ? "step" : "steps"}.`);
      setEditingId(null);
      setConfirming(null);
    },
    onError: () => setEditorResets((count) => count + 1),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["record", recordId] });
      void queryClient.invalidateQueries({ queryKey: ["flagged"] });
      void queryClient.invalidateQueries({ queryKey: ["findings"] });
      void queryClient.invalidateQueries({ queryKey: ["clashes"] });
    },
  });

  const data = detail.data;
  const pending = data?.findings.filter((finding) => finding.review_state === "pending") ?? [];
  const next: ReviewFinding | undefined = pending[0];
  const version = data?.version ?? 0;

  const apply = useCallback(
    (finding: ReviewFinding, editedValue?: unknown) =>
      mutation.mutate(() => api.applyChange(finding.id, version, editedValue)),
    [api, mutation, version],
  );
  const dismiss = useCallback(
    (finding: ReviewFinding) => mutation.mutate(() => api.dismissFinding(finding.id)),
    [api, mutation],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "escape") setConfirming(null);
      if (!next) return;
      if (key === "a" && next.proposed_value !== null && next.proposed_value !== undefined) setConfirming("apply");
      if (key === "d") setConfirming("dismiss");
      if (key === "e") {
        steps.current += 1;
        setEditingId(next.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next]);

  if (detail.isPending) return <p className="panel-message">Loading the record…</p>;
  if (detail.isError || !data) {
    return (
      <div className="alert" role="alert">
        Couldn't load this record. <button onClick={() => void detail.refetch()}>Try again</button>
      </div>
    );
  }

  const record = data.record;
  const organization = record.organization as { name?: string; website?: string } | undefined;
  const source = (record.x_resource_engine as { source?: { system?: string; record_id?: string } } | undefined)?.source;
  const verified = record.assured_date as string | undefined;
  const missing = missingFields(record);
  const rows = comparison.data ?? [];
  const error = mutation.error;
  const openClashes = data.clashes.filter((clash) => clash.status === "open");
  const allDone = saved && pending.length === 0 && openClashes.length === 0;

  return (
    <div className="panel-content">
      <header className="record-header">
        {row?.tier && <TierBadge tier={row.tier} />}
        <p className="eyebrow">{organization?.name}</p>
        <h2>{String(record.name)}</h2>
        <dl className="record-facts">
          <div>
            <dt>Status</dt>
            <dd className="capitalize">{String(record.status)}</dd>
          </div>
          <div>
            <dt>From</dt>
            <dd>{source?.system ? <SourceBadge source={source.system} /> : "Unknown"}</dd>
          </div>
          <div>
            <dt>Last verified</dt>
            <dd>{formatDate(verified)}</dd>
          </div>
          <div>
            <dt>Last checked</dt>
            <dd>{formatDate(data.last_check?.checked_at)}</dd>
          </div>
        </dl>
        {organization?.website && (
          <a href={organization.website} target="_blank" rel="noreferrer">
            Organization website<span className="visually-hidden"> (opens in a new tab)</span> ↗
          </a>
        )}
      </header>

      {row && row.reasons.length > 0 && (
        <section className="why" aria-label="Why this is prioritized">
          <h3>Why this needs attention</h3>
          <ul>
            {row.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </section>
      )}

      <div aria-live="polite">
        {saved && !error && (
          <div className="success">
            <span>{allDone ? `All done with this record. ${saved}` : saved}</span>
            {allDone && onNext && (
              <button type="button" className="primary" onClick={onNext}>
                Next record
              </button>
            )}
          </div>
        )}
      </div>
      {error && (
        <SaveError
          message={error instanceof ApiError ? error.message : "The save failed."}
          problems={error instanceof ApiError ? error.problems : []}
        />
      )}

      {confirming && next && (
        <div className="confirm" role="alertdialog" aria-label="Confirm action">
          <p>
            {confirming === "apply" ? "Apply" : "Dismiss"} this finding: {fieldLabel(next.ref.field).toLowerCase()}?
          </p>
          <div className="actions">
            <button
              type="button"
              className="primary"
              autoFocus
              disabled={mutation.isPending}
              onClick={() => (confirming === "apply" ? apply(next) : dismiss(next))}
            >
              {confirming === "apply" ? "Apply" : "Dismiss"}
            </button>
            <button type="button" onClick={() => setConfirming(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {data.findings.length > 0 && (
        <section aria-labelledby="findings-heading" className="stack">
          <h3 id="findings-heading" className="section-title">
            Findings
          </h3>
          {data.findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              currentDate={verified}
              currentSource={source?.system}
              busy={mutation.isPending}
              editing={editingId === finding.id}
              onEditingChange={(editing) => {
                if (editing) steps.current += 1;
                setEditingId(editing ? finding.id : null);
              }}
              onApply={(value) => apply(finding, value)}
              onDismiss={() => dismiss(finding)}
            />
          ))}
        </section>
      )}

      {data.clashes.length > 0 && (
        <section aria-labelledby="clashes-heading" className="stack">
          <h3 id="clashes-heading" className="section-title">
            Source clashes
          </h3>
          {data.clashes.map((clash) => (
            <ClashCard
              key={clash.id}
              clash={clash}
              busy={mutation.isPending}
              onResolve={(choice) => mutation.mutate(() => api.resolveClash(clash.id, version, choice))}
            />
          ))}
        </section>
      )}

      {missing.length > 0 && (
        <section aria-labelledby="missing-heading" className="missing">
          <h3 id="missing-heading" className="section-title">
            Missing from this record
          </h3>
          <ul className="chips">
            {missing.map((field) => (
              <li key={field}>{field}</li>
            ))}
          </ul>
        </section>
      )}

      {rows.length > 0 && (
        <section aria-labelledby="compare-heading" className="stack">
          <h3 id="compare-heading" className="section-title">
            Across sources
          </h3>
          {rows.map((comparisonRow) => {
            const [base, ...others] = comparisonRow.values;
            return (
              <article key={comparisonRow.field} className="diff-card" aria-label={`${comparisonRow.field} across sources`}>
                <header className="diff-card-header">
                  <p className="field-name">{comparisonRow.field}</p>
                  {comparisonRow.agree ? (
                    <span className="agree">Same fact</span>
                  ) : (
                    <DifferenceCount count={countDifferences(base.value ?? "", others[0]?.value ?? "")} />
                  )}
                </header>
                {others.map((other) => (
                  <SplitDiff
                    key={other.source}
                    left={{
                      source: base.source,
                      when: verifiedLabel(base.observed_at),
                      value: base.value ?? "Not set",
                    }}
                    right={{
                      source: other.source,
                      when: verifiedLabel(other.observed_at),
                      value: other.value ?? "Not set",
                    }}
                  />
                ))}
                {comparisonRow.note && <p className="fine-print">{comparisonRow.note}</p>}
              </article>
            );
          })}
        </section>
      )}

      <details className="all-fields">
        <summary>Edit all fields</summary>
        <Suspense fallback={<p className="muted">Loading the editor…</p>}>
          <FieldsEditor
            key={`${version}-${editorResets}`}
            record={record}
            onChange={(field, value) =>
              mutation.mutate(() =>
                api.patchRecord(recordId, version, [
                  { ref: { entity_type: "service", entity_id: String(record.id), field, op: "set" }, value },
                ]),
              )
            }
          />
        </Suspense>
      </details>

      <History record={record} />
    </div>
  );
}
