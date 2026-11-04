// Add a partner: upload a spreadsheet, review the suggested mapping, confirm, see the result.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { ConfirmResult, PartnersApi, Preview } from "../api/partners";
import { plainReason } from "../api/partners";
import { formatDuration } from "../format";

type Stage = "choose" | "previewing" | "mapping" | "importing" | "done";

function confidenceLevel(value: number): { label: string; style: string } {
  if (value >= 0.85) return { label: "High", style: "bg-good-soft text-good" };
  if (value >= 0.6) return { label: "Medium", style: "bg-medium-soft text-medium-ink" };
  return { label: "Low: please check", style: "bg-urgent-soft text-urgent-ink" };
}

function useStopwatch(running: boolean) {
  const start = useRef<number | null>(null);
  const [ms, setMs] = useState(0);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => start.current !== null && setMs(Date.now() - start.current), 200);
    return () => window.clearInterval(timer);
  }, [running]);
  return {
    ms,
    begin() {
      start.current = Date.now();
      setMs(0);
    },
    stop() {
      if (start.current !== null) setMs(Date.now() - start.current);
    },
  };
}

export function AddPartner({ api }: { api: PartnersApi }) {
  const queryClient = useQueryClient();
  const [stage, setStage] = useState<Stage>("choose");
  const [partnerName, setPartnerName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [defaultState, setDefaultState] = useState("CA");
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const clock = useStopwatch(stage === "previewing" || stage === "mapping" || stage === "importing");

  const loadSample = async () => {
    setError(null);
    try {
      setFile(await api.sampleFile());
      if (!partnerName) setPartnerName("Solano County Food Pantries");
    } catch (failure) {
      setError((failure as Error).message);
    }
  };

  const suggest = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setError(null);
    clock.begin();
    setStage("previewing");
    try {
      const next = await api.preview(file);
      setPreview(next);
      setMapping(Object.fromEntries(next.columns.map((c) => [c.column, c.target])));
      setStage("mapping");
    } catch (failure) {
      setError((failure as Error).message);
      setStage("choose");
    }
  };

  const importRows = async () => {
    if (!preview) return;
    setError(null);
    setStage("importing");
    try {
      const done = await api.confirm(preview.upload_id, partnerName, mapping, defaultState);
      clock.stop();
      setResult(done);
      setStage("done");
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    } catch (failure) {
      setError((failure as Error).message);
      setStage("mapping");
    }
  };

  const reset = () => {
    setStage("choose");
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
    setPartnerName("");
  };

  const changed = preview ? preview.columns.filter((c) => mapping[c.column] !== c.target).length : 0;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Add a partner</h1>
          <p className="text-sm text-muted">
            Upload a partner's spreadsheet. We suggest where each column goes; you check it before anything is saved.
          </p>
        </div>
        {stage !== "choose" && (
          <p className="text-sm tabular-nums text-muted" aria-live="off">
            Elapsed: {formatDuration(clock.ms)}
          </p>
        )}
      </header>

      <ol className="flex flex-wrap gap-2 text-sm" aria-label="Steps">
        {["Upload", "Check the columns", "Import"].map((label, index) => {
          const current = stage === "choose" || stage === "previewing" ? 0 : stage === "mapping" || stage === "importing" ? 1 : 2;
          return (
            <li
              key={label}
              aria-current={index === current ? "step" : undefined}
              className={`rounded-full px-3 py-1 ${index === current ? "bg-ink text-surface" : index < current ? "bg-good-soft text-good" : "bg-surface-2 text-muted"}`}
            >
              {index + 1}. {label}
            </li>
          );
        })}
      </ol>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {(stage === "choose" || stage === "previewing") && (
        <form onSubmit={(event) => void suggest(event)} className="flex flex-col gap-4 rounded-xl border border-line bg-surface p-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Partner name</span>
            <input value={partnerName} onChange={(event) => setPartnerName(event.target.value)} required placeholder="For example: Solano County Food Pantries" />
          </label>
          <div className="flex flex-col gap-1 text-sm">
            <label htmlFor="partner-file" className="font-medium">
              Spreadsheet (CSV)
            </label>
            <input id="partner-file" type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
            <p className="text-xs text-muted">
              From Excel or Google Sheets: File → Download → CSV. Up to 2 MB.{" "}
              <button type="button" className="!border-0 !bg-transparent !p-0 text-primary underline" onClick={() => void loadSample()}>
                Use the sample county food pantry file
              </button>
            </p>
            {file && <p className="text-xs">Selected: {file.name}</p>}
          </div>
          <button type="submit" className="primary self-start" disabled={!file || !partnerName.trim() || stage === "previewing"}>
            {stage === "previewing" ? "Reading the file…" : "Suggest a mapping"}
          </button>
        </form>
      )}

      {(stage === "mapping" || stage === "importing") && preview && (
        <section aria-labelledby="mapping-title" className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
          <div>
            <h2 id="mapping-title" className="text-lg font-semibold">
              Where each column goes
            </h2>
            <p className="text-sm text-muted">
              {preview.filename}: {preview.rows} rows. Suggested by {preview.model_id === "offline-rules" ? "offline rules" : `Amazon Bedrock (${preview.model_id})`}.
              {changed > 0 && ` You changed ${changed}.`}
            </p>
            {preview.note && <p className="mt-1 text-sm text-medium-ink">{preview.note}</p>}
          </div>
          {/* One table; on narrow screens each row stacks as a card so nothing scrolls sideways. */}
          <table className="block w-full text-sm md:table">
            <thead className="hidden md:table-header-group">
              <tr className="text-left text-muted">
                <th className="py-2 pr-3 font-medium">Column</th>
                <th className="py-2 pr-3 font-medium">Example values</th>
                <th className="py-2 pr-3 font-medium">Goes to</th>
                <th className="py-2 font-medium">Confidence</th>
              </tr>
            </thead>
            <tbody className="flex flex-col gap-3 md:table-row-group">
              {preview.columns.map((column) => {
                const level = confidenceLevel(column.confidence);
                return (
                  <tr
                    key={column.column}
                    className="flex flex-col gap-2 rounded-lg border border-line p-3 align-top md:table-row md:rounded-none md:border-0 md:border-t md:p-0"
                  >
                    <th scope="row" className="text-left font-medium md:py-2 md:pr-3">
                      {column.column}
                    </th>
                    <td className="min-w-0 text-xs text-muted md:max-w-[18rem] md:py-2 md:pr-3">
                      <span className="sr-only md:hidden">Example values: </span>
                      {column.samples.length
                        ? column.samples.map((s) => (
                            <div key={s} className="truncate" title={s}>
                              {s}
                            </div>
                          ))
                        : "(empty)"}
                    </td>
                    <td className="md:py-2 md:pr-3">
                      <select
                        aria-label={`Field for column ${column.column}`}
                        value={mapping[column.column]}
                        onChange={(event) => setMapping({ ...mapping, [column.column]: event.target.value })}
                        className="w-full md:w-auto"
                      >
                        {Object.entries(preview.targets).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="md:py-2">
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${level.style}`}>
                        {level.label} · {Math.round(column.confidence * 100)}%
                      </span>
                      <p className="mt-1 text-xs text-muted">{column.reason}</p>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <label className="flex max-w-xs flex-col gap-1 text-sm">
            <span className="font-medium">State for addresses without one</span>
            <input value={defaultState} maxLength={2} onChange={(event) => setDefaultState(event.target.value.toUpperCase())} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="primary" disabled={stage === "importing"} onClick={() => void importRows()}>
              {stage === "importing" ? "Importing…" : `Confirm and import ${preview.rows} rows`}
            </button>
            <button type="button" onClick={reset} disabled={stage === "importing"}>
              Start over
            </button>
          </div>
        </section>
      )}

      {stage === "done" && result && (
        <section aria-labelledby="result-title" className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4" role="status">
          <h2 id="result-title" className="text-lg font-semibold text-good">
            {partnerName} added in {formatDuration(clock.ms)}
          </h2>
          <p className="text-sm text-muted">
            From upload to stored, including your review. The import itself took {formatDuration(result.run.elapsed_ms)}. Source name:{" "}
            <code>{result.source}</code>
          </p>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Rows received", result.run.received],
              ["Stored", result.run.stored],
              ["Rows quarantined", result.run.quarantined],
              ["Parts held back", result.run.objects_quarantined],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg bg-surface-2 p-3">
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="text-xs text-muted">
            Received = stored + quarantined ({result.run.received} = {result.run.stored} + {result.run.quarantined}). Nothing was dropped.
          </p>
          {result.quarantine.length > 0 ? (
            <div>
              <h3 className="text-sm font-semibold">Held back, with the reason</h3>
              <ul className="mt-1 flex flex-col divide-y divide-[var(--border)] text-sm">
                {result.quarantine.map((item) => (
                  <li key={item.id} className="py-2">
                    <span className="font-medium">{item.native_id.split("/").pop()?.replace(/-/g, " ")}</span>
                    {item.level === "object" ? ` (the ${item.kind}; the rest of the row was stored)` : " (the whole row)"}:{" "}
                    {item.reasons.map((r) => plainReason(item.kind, r)).join(" ")}
                    <span className="block text-xs text-muted">{item.reasons.join("; ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm">Every row was stored in full.</p>
          )}
          <div className="flex flex-wrap gap-2">
            <a className="text-sm" href="#/overview">
              See it on the Overview
            </a>
            <button type="button" className="text-sm" onClick={reset}>
              Add another partner
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
