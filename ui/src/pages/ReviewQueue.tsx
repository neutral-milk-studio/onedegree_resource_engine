// Review queue: findings sorted by harm, filter by category, a detail panel with current vs
// proposed side by side, evidence link + quote, Apply / Keep current, a timer, and the audit
// entry after saving. AI proposes; the specialist decides.
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import {
  formatValue,
  type Decided,
  type QueueCategory,
  type QueueEvidence,
  type QueueFinding,
  type ReviewQueueApi,
} from "../api/review";
import { TrustBadge } from "../components/TrustBadge";
import { CATEGORY_HELP, CATEGORY_LABELS, formatUsd, sourceLabel } from "../format";
import { SplitDiff } from "../sections/review/SplitDiff";

const CATEGORIES: QueueCategory[] = ["actively_bad", "suspicious", "discrepancy", "missing_information", "check_failed"];
const STATUS_CHOICES = [
  { value: "inactive", label: "Mark as inactive" },
  { value: "temporarily closed", label: "Mark as temporarily closed" },
  { value: "defunct", label: "Mark as closed for good" },
];
const CATEGORY_STYLE: Record<string, string> = {
  actively_bad: "bg-urgent-soft text-urgent-ink",
  suspicious: "bg-urgent-soft text-urgent-ink",
  discrepancy: "bg-high-soft text-high-ink",
  missing_information: "bg-medium-soft text-medium-ink",
  check_failed: "bg-low-soft text-low-ink",
};

function CategoryBadge({ category }: { category: string }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${CATEGORY_STYLE[category] ?? ""}`}>
      {CATEGORY_LABELS[category] ?? category}
    </span>
  );
}

/** Whole seconds since the detail for ``key`` opened, ticking every second. */
function useElapsed(key: string): number {
  const start = useRef(Date.now());
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    start.current = Date.now();
    setSeconds(0);
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - start.current) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [key]);
  return seconds;
}

export function ReviewQueue({ api }: { api: ReviewQueueApi }) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<QueueCategory | undefined>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lastDecision, setLastDecision] = useState<Decided | null>(null);

  const queue = useQuery({ queryKey: ["queue", category], queryFn: () => api.listQueue(category) });
  const findings = useMemo(() => queue.data?.contents ?? [], [queue.data]);
  const selected = findings.find((f) => f.id === selectedId) ?? null;

  // On wide screens open the most harmful finding next to the list. On phones the list comes
  // first, because an open detail replaces the list there.
  useEffect(() => {
    const wide = typeof window.matchMedia !== "function" || window.matchMedia("(min-width: 1024px)").matches;
    if (wide && !selectedId && findings.length && !lastDecision) setSelectedId(findings[0].id);
  }, [findings, selectedId, lastDecision]);

  const checks = useMutation({
    mutationFn: () => api.runChecks(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["queue"] });
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
    },
  });

  const onDecided = (decided: Decided) => {
    setLastDecision(decided);
    setSelectedId(null);
    void queryClient.invalidateQueries({ queryKey: ["queue"] });
    void queryClient.invalidateQueries({ queryKey: ["metrics"] });
    void queryClient.invalidateQueries({ queryKey: ["audit"] });
  };

  const counts = queue.data?.counts_by_category ?? {};
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Review queue</h1>
          <p className="text-sm text-muted">
            What the AI found on organization websites, most harmful first. Nothing changes until you apply it.
          </p>
        </div>
        <button type="button" className="text-sm" onClick={() => checks.mutate()} disabled={checks.isPending}>
          {checks.isPending ? "Checking websites…" : "Check websites now"}
        </button>
      </header>
      {checks.data && (
        <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm" role="status">
          Checked {checks.data.checked} websites in {(checks.data.elapsed_ms / 1000).toFixed(1)} s:{" "}
          {Object.entries(checks.data.by_category)
            .map(([c, n]) => `${n} ${(CATEGORY_LABELS[c] ?? c).toLowerCase()}`)
            .join(", ")}
          . {checks.data.findings_created} new findings. Model: {checks.data.model_id}
          {checks.data.cost_usd > 0 ? `, cost ${formatUsd(checks.data.cost_usd)}` : ", no model cost"}
          {checks.data.cached ? ` (${checks.data.cached} from cache)` : ""}.
        </p>
      )}
      {checks.error && (
        <p className="alert" role="alert">
          Checks failed. {checks.error.message}
        </p>
      )}

      <div role="group" aria-label="Filter by category" className="flex flex-wrap gap-2">
        <FilterChip active={!category} onClick={() => setCategory(undefined)} label="All" count={total} />
        {CATEGORIES.map((c) => (
          <FilterChip
            key={c}
            active={category === c}
            onClick={() => {
              setCategory(c);
              setSelectedId(null);
              setLastDecision(null);
            }}
            label={CATEGORY_LABELS[c]}
            count={counts[c] ?? 0}
          />
        ))}
      </div>

      {queue.isError ? (
        <p className="alert" role="alert">
          Couldn't load the queue. {queue.error.message}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(18rem,26rem)_1fr]">
          <section aria-label="Findings" className={`flex flex-col gap-2 ${selected ? "hidden lg:flex" : ""}`}>
            {queue.isLoading && <p className="text-sm text-muted">Loading…</p>}
            {!queue.isLoading && findings.length === 0 && (
              <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">
                {total === 0
                  ? "Nothing to review. Run a website check to look for problems."
                  : "No findings in this category."}
              </p>
            )}
            <ol className="flex flex-col gap-2">
              {findings.map((finding) => (
                <li key={finding.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedId(finding.id);
                      setLastDecision(null);
                    }}
                    aria-current={finding.id === selectedId ? "true" : undefined}
                    className={`flex w-full flex-col gap-1 !rounded-xl !p-3 text-left ${
                      finding.id === selectedId ? "!border-primary !bg-selected" : ""
                    }`}
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <CategoryBadge category={finding.category} />
                      <span className="text-xs text-muted">{finding.field_label}</span>
                    </span>
                    <span className="font-medium">{finding.service_name}</span>
                    <span className="text-xs text-muted">
                      {finding.organization_name} · {sourceLabel(finding.source)}
                    </span>
                    <TrustBadge trust={finding.trust} />
                  </button>
                </li>
              ))}
            </ol>
          </section>

          <section aria-label="Finding detail" className="min-w-0">
            {lastDecision ? (
              <SavedPanel decided={lastDecision} hasNext={findings.length > 0} onNext={() => {
                setLastDecision(null);
                setSelectedId(findings[0]?.id ?? null);
              }} />
            ) : selected ? (
              <Detail key={selected.id} finding={selected} api={api} onDecided={onDecided} onBack={() => setSelectedId(null)} />
            ) : (
              findings.length > 0 && <p className="text-sm text-muted">Choose a finding to review.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function FilterChip({ active, onClick, label, count }: { active: boolean; onClick(): void; label: string; count: number }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`!rounded-full !px-3 !py-1 text-sm ${active ? "!border-ink !bg-ink text-surface" : ""}`}
    >
      {label} <span className="tabular-nums opacity-75">{count}</span>
    </button>
  );
}

/** The suspicious-listing detail: a risk score bar, each signal's weight, reason and quote,
 * and what our record says next to what the website says. No "scam" or "fraud" wording --
 * the score only explains itself; a specialist decides. */
function SuspiciousPanel({ finding, evidence }: { finding: QueueFinding; evidence: QueueEvidence }) {
  const score = finding.risk_score ?? 0;
  const signals = finding.risk_signals ?? [];
  const tier = score >= 70 ? "urgent" : "high";
  return (
    <div className="flex flex-col gap-4">
      <span className="inline-flex w-fit items-center gap-1 rounded-full bg-urgent-soft px-2.5 py-1 text-sm font-semibold text-urgent-ink">
        ⚠ Flagged for review
      </span>
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-sm">
          <span className="font-semibold">Risk score</span>
          <span className="tabular-nums text-muted">{score} of 100</span>
        </div>
        <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-2" role="img" aria-label={`Risk score ${score} of 100`}>
          <div className={`h-full rounded-full ${tier === "urgent" ? "bg-urgent" : "bg-high"}`} style={{ width: `${Math.min(100, score)}%` }} />
        </div>
        <p className="text-xs text-muted">A score of {40} or more goes to a specialist. The score only explains why — you decide.</p>
      </div>

      {signals.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Why it was flagged</h3>
          <ul className="flex flex-col gap-2">
            {signals.map((signal) => (
              <li
                key={signal.code}
                className="flex items-start gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2"
              >
                {signal.weight > 0 && (
                  <span className="mt-0.5 shrink-0 rounded-full bg-ink px-2 py-0.5 text-xs font-bold text-surface">
                    +{signal.weight}
                  </span>
                )}
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium">{signal.message}</span>
                  {signal.quote && <span className="text-sm italic text-muted">“{signal.quote}”</span>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg bg-good-soft px-3 py-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-good">Our record says</p>
          <p className="text-sm text-good">
            {finding.field_label} — {formatValue(finding.current_value)}
          </p>
        </div>
        <div className="rounded-lg bg-high-soft px-3 py-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-high-ink">The website says</p>
          <p className="text-sm text-high-ink">{evidence.snippet ?? "See the page for details."}</p>
        </div>
      </div>

      <p className="text-xs text-muted">
        Source:{" "}
        <a href={evidence.url} target="_blank" rel="noreferrer">
          {evidence.url}
          <span className="visually-hidden"> (opens in a new tab)</span>
        </a>
      </p>
    </div>
  );
}

function Detail({
  finding,
  api,
  onDecided,
  onBack,
}: {
  finding: QueueFinding;
  api: ReviewQueueApi;
  onDecided(decided: Decided): void;
  onBack(): void;
}) {
  const [status, setStatus] = useState(STATUS_CHOICES[0].value);
  const [reason, setReason] = useState("");
  const seconds = useElapsed(finding.id);
  const evidence = finding.evidence[0];
  const isDead = finding.category === "actively_bad";
  const isSuspicious = finding.category === "suspicious";
  const needsStatus = isDead || isSuspicious;
  const canApply = finding.category !== "check_failed";

  const apply = useMutation({
    mutationFn: () => api.apply(finding, isSuspicious ? "inactive" : needsStatus ? status : undefined, seconds),
    onSuccess: onDecided,
  });
  const keep = useMutation({
    mutationFn: () => api.dismiss(finding, reason.trim() || undefined, seconds),
    onSuccess: onDecided,
  });
  const hide = useMutation({
    mutationFn: () => api.hide(finding, seconds),
    onSuccess: onDecided,
  });
  const markSafe = useMutation({
    mutationFn: () => api.markSafe(finding, reason.trim(), seconds),
    onSuccess: onDecided,
  });
  const error = (apply.error ?? keep.error ?? hide.error ?? markSafe.error) as ApiError | Error | null;
  const busy = apply.isPending || keep.isPending || hide.isPending || markSafe.isPending;

  return (
    <article className="flex flex-col gap-4 rounded-xl border border-line bg-surface p-4" aria-labelledby="detail-title">
      <button type="button" className="self-start text-sm lg:hidden" onClick={onBack}>
        ← Back to the list
      </button>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">{finding.harm_label}</p>
          <h2 id="detail-title" className="text-xl font-semibold">
            {finding.service_name}
          </h2>
          <p className="text-sm text-muted">
            {finding.organization_name} · from {sourceLabel(finding.source)}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <TrustBadge trust={finding.trust} />
          <p className="text-xs tabular-nums text-muted" aria-live="off">
            Working on this: {seconds} s
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <CategoryBadge category={finding.category} />
        <span className="text-sm">{CATEGORY_HELP[finding.category]}</span>
      </div>

      {isSuspicious ? (
        <SuspiciousPanel finding={finding} evidence={evidence} />
      ) : evidence.kind === "quote" ? (
        <>
          <h3 className="text-sm font-semibold">{finding.field_label}: record vs. website</h3>
          <SplitDiff
            left={{ label: "In the record now", value: formatValue(finding.current_value) }}
            right={{ label: "On the website", value: formatValue(finding.proposed_value), link: evidence.url }}
          />
          <figure className="rounded-lg border-l-4 border-primary bg-surface-2 px-3 py-2">
            <blockquote className="text-sm">“{evidence.snippet}”</blockquote>
            <figcaption className="mt-1 text-xs text-muted">
              Quoted from{" "}
              <a href={evidence.url} target="_blank" rel="noreferrer">
                {evidence.url}
                <span className="visually-hidden"> (opens in a new tab)</span>
              </a>{" "}
              · confidence {Math.round(finding.confidence * 100)}% · {finding.model_id}
            </figcaption>
          </figure>
        </>
      ) : (
        <div className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
          <p className="font-medium">
            {isDead ? "The website looks gone." : "We couldn't read the website, so nothing was guessed."}
          </p>
          <p className="text-muted">
            {evidence.http_status ? `HTTP ${evidence.http_status}. ` : ""}
            {evidence.error}
          </p>
          <p className="mt-1 text-xs text-muted">
            Checked{" "}
            <a href={evidence.url} target="_blank" rel="noreferrer">
              {evidence.url}
              <span className="visually-hidden"> (opens in a new tab)</span>
            </a>
          </p>
        </div>
      )}

      {needsStatus && !isSuspicious && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">If you confirm it's closed, update the status to</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)} className="max-w-xs">
            {STATUS_CHOICES.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">
          {isSuspicious ? "Note for “Mark safe” (required to mark safe)" : "Note for “Keep current” (optional)"}
        </span>
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder={
            isSuspicious
              ? "For example: called them, this is a real, verified program"
              : "For example: called them, the record is right"
          }
        />
      </label>

      {error && (
        <div className="alert" role="alert">
          {error.message}
          {error instanceof ApiError && error.problems.length > 0 && (
            <ul>
              {error.problems.map((problem) => (
                <li key={`${problem.path}-${problem.message}`}>
                  {problem.path}: {problem.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {isSuspicious ? (
          <>
            <button type="button" className="primary" disabled={busy} onClick={() => hide.mutate()}>
              {hide.isPending ? "Saving…" : "Hide from search"}
            </button>
            <button
              type="button"
              disabled={busy || !reason.trim()}
              title={reason.trim() ? undefined : "Add a short note above first"}
              onClick={() => markSafe.mutate()}
            >
              {markSafe.isPending ? "Saving…" : "Mark safe"}
            </button>
            <button type="button" disabled={busy} onClick={() => apply.mutate()}>
              {apply.isPending ? "Saving…" : "Confirm and remove"}
            </button>
          </>
        ) : (
          <>
            {canApply && (
              <button type="button" className="primary" disabled={busy} onClick={() => apply.mutate()}>
                {apply.isPending ? "Saving…" : isDead ? "Apply status change" : "Apply website value"}
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => keep.mutate()}>
              {keep.isPending ? "Saving…" : "Keep current"}
            </button>
          </>
        )}
      </div>
      {isSuspicious && (
        <p className="rounded-lg bg-good-soft px-3 py-2 text-sm text-good">
          While it's flagged, this listing is already hidden from search, text messages and the public API.
        </p>
      )}
    </article>
  );
}

function SavedPanel({ decided, hasNext, onNext }: { decided: Decided; hasNext: boolean; onNext(): void }) {
  const kept = decided.finding.review_state === "dismissed";
  const seconds = decided.finding.seconds_to_fix;
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4" role="status" aria-live="polite">
      <p className="text-lg font-semibold text-good">
        {kept ? "Kept the current value" : "Saved"}
        {typeof seconds === "number" ? ` in ${Math.round(seconds)} s` : ""}
      </p>
      <p className="text-sm">{decided.audit_entry.summary}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-muted">
        <dt>Who</dt>
        <dd>{decided.audit_entry.actor}</dd>
        <dt>When</dt>
        <dd>
          <time dateTime={decided.audit_entry.at}>{new Date(decided.audit_entry.at).toLocaleString()}</time>
        </dd>
        <dt>Field</dt>
        <dd>{decided.audit_entry.field ?? "record"}</dd>
        <dt>Source of change</dt>
        <dd>{decided.audit_entry.change_source}</dd>
        {!kept && (
          <>
            <dt>Record version</dt>
            <dd>{decided.record_version}</dd>
          </>
        )}
      </dl>
      {hasNext && (
        <button type="button" className="primary self-start" onClick={onNext}>
          Next finding
        </button>
      )}
    </section>
  );
}
