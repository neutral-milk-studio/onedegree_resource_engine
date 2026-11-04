import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import type { DashboardApi, IngestRun } from "../api/dashboard";
import { FindingsChart } from "../components/FindingsChart";
import { Icon } from "../components/Icon";
import { StatTile } from "../components/StatTile";
import { formatCount, formatDuration, formatUsd, sourceLabel, timeAgo } from "../format";

const DEFAULT_SOURCES = ["odrs", "ndp"];

export function Overview({ api }: { api: DashboardApi }) {
  const queryClient = useQueryClient();
  const metrics = useQuery({ queryKey: ["metrics"], queryFn: () => api.getMetrics() });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.getAudit(12) });

  const ingest = useMutation({
    mutationFn: (source: string) => api.runIngest(source),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  if (metrics.isError) {
    const error = metrics.error;
    return (
      <div className="p-4 lg:p-6">
        <p className="alert" role="alert">
          Couldn't load the dashboard. {error instanceof ApiError ? error.message : "Try again."}
        </p>
      </div>
    );
  }

  const m = metrics.data;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Overview</h1>
          <p className="text-sm text-muted">How complete, current and trustworthy the listings are right now.</p>
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 text-sm"
          onClick={() => void metrics.refetch().then(() => audit.refetch())}
          disabled={metrics.isFetching}
        >
          <Icon name="refresh" />
          {metrics.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {!m ? (
        <p className="text-sm text-muted" role="status">
          Loading…
        </p>
      ) : (
        <>
          <section aria-label="Key numbers" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <StatTile
              label="Total records"
              value={formatCount(m.total_records)}
              hint={
                m.records_by_source.length ? (
                  <ul className="flex flex-col">
                    {m.records_by_source.map((item) => (
                      <li key={item.source} className="flex justify-between gap-2">
                        <span>{sourceLabel(item.source)}</span>
                        <span className="tabular-nums">{formatCount(item.records)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  "Nothing ingested yet. Run a source below."
                )
              }
            />
            <StatTile
              label="Quarantined"
              value={formatCount(m.quarantined_records)}
              tone={m.quarantined_records > 0 ? "attention" : "default"}
              hint={
                m.quarantined_objects > 0
                  ? `Records held back. Plus ${m.quarantined_objects} smaller parts (an address or phone) held back from stored records.`
                  : "Records held back because they aren't valid HSDS."
              }
            />
            <StatTile
              label="Open findings"
              value={formatCount(m.findings.open_total)}
              tone={m.findings.open_total > 0 ? "attention" : "default"}
              hint={
                m.findings.checks_run === 0
                  ? "No website checks have run yet."
                  : `From ${formatCount(m.findings.checks_run)} website checks. ${formatCount(m.findings.decided)} resolved` +
                    (m.findings.median_seconds_to_fix !== null
                      ? `, median ${Math.round(m.findings.median_seconds_to_fix)} s to fix.`
                      : ".")
              }
            />
            <StatTile
              label="Conflicts between sources"
              value={m.conflicts_open === null ? null : formatCount(m.conflicts_open)}
              hint={m.conflicts_open === null ? "Not checked yet." : "Same organization, different values."}
            />
            <StatTile
              label="Average trust score"
              value={m.avg_trust_score === null ? null : `${Math.round(m.avg_trust_score)} / 100`}
              hint={
                m.avg_trust_score === null
                  ? "Shown after the first website check."
                  : `Across ${formatCount(m.findings.records_checked)} checked records. Higher is better.`
              }
            />
            <StatTile
              label="Cost per AI check"
              value={m.cost_per_check_usd === null ? null : formatUsd(m.cost_per_check_usd)}
              hint={
                m.cost_per_check_usd === null
                  ? "No AI checks yet."
                  : m.findings.model_ids.every((id) => id === "offline-rules")
                    ? "Offline rules, no model cost. Set BEDROCK_MODEL_ID to use Amazon Bedrock."
                    : `Average Amazon Bedrock cost (${m.findings.model_ids.join(", ")}). Unchanged pages are cached.`
              }
            />
            <StatTile
              label="Missing information"
              value={formatCount(m.records_missing_information)}
              hint="Stored with a placeholder, such as no organization description. Fixable in review."
            />
            <StatTile
              label="Active services"
              value={formatCount(m.records_by_status.active ?? 0)}
              hint={`${formatCount(m.total_records - (m.records_by_status.active ?? 0))} inactive or closed`}
            />
          </section>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
            <div className="lg:col-span-3">
              <FindingsChart counts={m.findings.open_by_category} checksRun={m.findings.checks_run} />
            </div>
            <section
              aria-labelledby="sources-title"
              className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4 lg:col-span-2"
            >
              <h2 id="sources-title" className="text-base font-semibold">
                Sources
              </h2>
              <ul className="flex flex-col divide-y divide-[var(--border)]">
                {[...new Set([...DEFAULT_SOURCES, ...m.last_ingest.map((run) => run.source)])].map((source) => {
                  const last = m.last_ingest.find((run) => run.source === source);
                  const running = ingest.isPending && ingest.variables === source;
                  return (
                    <li key={source} className="flex flex-wrap items-center justify-between gap-2 py-2">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{sourceLabel(source)}</p>
                        <p className="text-xs text-muted">
                          {last?.finished_at
                            ? `${last.received} received · ${last.stored} stored · ${last.quarantined} quarantined · ${timeAgo(last.finished_at)}`
                            : "Not ingested yet"}
                        </p>
                      </div>
                      <button
                        type="button"
                        className="!px-2.5 !py-1 text-xs"
                        disabled={ingest.isPending}
                        onClick={() => ingest.mutate(source)}
                      >
                        {running ? "Running…" : last ? "Run again" : "Run now"}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <IngestResult run={ingest.data} error={ingest.error} />
            </section>
          </div>

          <section aria-labelledby="activity-title" className="rounded-xl border border-line bg-surface p-4">
            <h2 id="activity-title" className="mb-2 text-base font-semibold">
              Recent activity
            </h2>
            {audit.data && audit.data.length > 0 ? (
              <ol className="flex flex-col divide-y divide-[var(--border)]">
                {audit.data.map((entry) => (
                  <li key={entry.id} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 py-2 text-sm">
                    <span className="min-w-0">{entry.summary ?? `${entry.action} on ${entry.entity_type}`}</span>
                    <span className="text-xs text-muted">
                      {entry.actor} · <time dateTime={entry.at}>{timeAgo(entry.at)}</time>
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted">No activity yet.</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function IngestResult({ run, error }: { run?: IngestRun; error: Error | null }) {
  if (error) {
    return (
      <p className="alert text-sm" role="alert">
        Ingest failed. {error.message}
      </p>
    );
  }
  if (!run) return null;
  return (
    <p className="rounded-lg bg-good-soft px-3 py-2 text-sm text-good" role="status">
      {sourceLabel(run.source)}: {run.received} received, {run.stored} stored, {run.quarantined} quarantined in{" "}
      {formatDuration(run.elapsed_ms)}.
    </p>
  );
}
