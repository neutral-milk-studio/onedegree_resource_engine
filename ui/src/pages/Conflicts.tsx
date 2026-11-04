// Conflicts: the same organization from two sources with different values. The specialist
// chooses which value wins; it is applied to every service of the losing source.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import type { Choice, Conflict, ConflictSide, ConflictsApi } from "../api/conflicts";
import { sourceLabel, timeAgo } from "../format";

export function Conflicts({ api }: { api: ConflictsApi }) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"open" | "resolved">("open");
  const [saved, setSaved] = useState<{ conflict: Conflict; services_updated: number } | null>(null);
  const conflicts = useQuery({ queryKey: ["conflicts", tab], queryFn: () => api.list(tab) });
  const detect = useMutation({
    mutationFn: () => api.detect(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["conflicts"] }),
  });

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Conflicts</h1>
          <p className="text-sm text-muted">
            The same organization listed by two sources with different values. Choose which one is right.
          </p>
        </div>
        <button type="button" className="text-sm" onClick={() => detect.mutate()} disabled={detect.isPending}>
          {detect.isPending ? "Looking…" : "Look for conflicts again"}
        </button>
      </header>
      {detect.data && (
        <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm" role="status">
          {detect.data.opened} new, {detect.data.refreshed} still open, {detect.data.closed} now match.
        </p>
      )}

      {saved && (
        <p className="rounded-lg bg-good-soft px-3 py-2 text-sm text-good" role="status">
          Saved. {saved.conflict.organization_name}: updated {saved.services_updated}{" "}
          {saved.services_updated === 1 ? "record" : "records"} to use {saved.conflict.resolved_value}. The change is
          in the audit log.
        </p>
      )}

      <div role="group" aria-label="Show" className="flex gap-2">
        {(["open", "resolved"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={tab === value}
            onClick={() => setTab(value)}
            className={`!rounded-full !px-3 !py-1 text-sm ${tab === value ? "!border-ink !bg-ink text-surface" : ""}`}
          >
            {value === "open" ? "Open" : "Resolved"}
          </button>
        ))}
      </div>

      {conflicts.isError && (
        <p className="alert" role="alert">
          Couldn't load conflicts. {conflicts.error.message}
        </p>
      )}
      {conflicts.data?.length === 0 && (
        <p className="rounded-xl border border-line bg-surface p-4 text-sm text-muted">
          {tab === "open" ? "No open conflicts. The sources agree." : "Nothing resolved yet."}
        </p>
      )}
      <ul className="flex flex-col gap-4">
        {conflicts.data?.map((conflict) => (
          <li key={conflict.id}>
            {conflict.status === "open" ? (
              <OpenConflict conflict={conflict} api={api} onSaved={setSaved} />
            ) : (
              <ResolvedConflict conflict={conflict} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SideCard({ side, name, checked, onChoose }: { side: ConflictSide; name: string; checked: boolean; onChoose(): void }) {
  return (
    <label
      className={`flex cursor-pointer flex-col gap-2 rounded-xl border p-3 ${
        checked ? "border-primary bg-selected" : "border-line bg-surface"
      }`}
    >
      <span className="flex items-center gap-2">
        <input type="radio" name={name} checked={checked} onChange={onChoose} />
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">{sourceLabel(side.source)} says</span>
      </span>
      <span className="break-all text-lg font-semibold">{side.value ?? "—"}</span>
      {side.values.length > 1 && <span className="text-xs text-muted">Also listed: {side.values.filter((v) => v !== side.value).join(", ")}</span>}
      <span className="text-xs text-muted">
        Used by {side.services.length} {side.services.length === 1 ? "service" : "services"}:{" "}
        {side.services.map((s) => s.name).join(", ")}
      </span>
    </label>
  );
}

function OpenConflict({
  conflict,
  api,
  onSaved,
}: {
  conflict: Conflict;
  api: ConflictsApi;
  onSaved(result: { conflict: Conflict; services_updated: number }): void;
}) {
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState<Choice | null>(null);
  const [custom, setCustom] = useState("");
  const resolve = useMutation({
    mutationFn: () => api.resolve(conflict.id, choice!, choice === "custom" ? custom : undefined),
    onSuccess: (result) => {
      onSaved(result);
      void queryClient.invalidateQueries({ queryKey: ["conflicts"] });
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const name = `choice-${conflict.id}`;
  const error = resolve.error as ApiError | Error | null;

  return (
    <article className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4" aria-labelledby={`t-${conflict.id}`}>
      <div>
        <h2 id={`t-${conflict.id}`} className="text-lg font-semibold">
          {conflict.organization_name}: {conflict.field_label.toLowerCase()}
        </h2>
        <p className="text-xs text-muted">Found {timeAgo(conflict.detected_at)}. Which value should every listing use?</p>
      </div>
      <fieldset className="m-0 grid grid-cols-1 gap-3 border-0 p-0 md:grid-cols-2">
        <legend className="visually-hidden">Choose the {conflict.field_label.toLowerCase()} to keep</legend>
        <SideCard side={conflict.a} name={name} checked={choice === "a"} onChoose={() => setChoice("a")} />
        <SideCard side={conflict.b} name={name} checked={choice === "b"} onChoose={() => setChoice("b")} />
        <label className={`flex flex-col gap-2 rounded-xl border p-3 md:col-span-2 ${choice === "custom" ? "border-primary bg-selected" : "border-line"}`}>
          <span className="flex items-center gap-2 text-sm">
            <input type="radio" name={name} checked={choice === "custom"} onChange={() => setChoice("custom")} />
            Neither. Use another value (both sources will use it)
          </span>
          {choice === "custom" && (
            <input
              aria-label={`New ${conflict.field_label.toLowerCase()}`}
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              placeholder={conflict.field === "email" ? "name@example.org" : ""}
            />
          )}
        </label>
      </fieldset>
      {error && (
        <div className="alert" role="alert">
          {error.message}
          {error instanceof ApiError && error.problems.length > 0 && (
            <ul>
              {error.problems.map((p) => (
                <li key={p.path + p.message}>
                  {p.path}: {p.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <button type="button" className="primary self-start" disabled={!choice || resolve.isPending} onClick={() => resolve.mutate()}>
        {resolve.isPending ? "Saving…" : "Use this value"}
      </button>
    </article>
  );
}

function ResolvedConflict({ conflict }: { conflict: Conflict }) {
  const how =
    conflict.resolution === "now_match"
      ? "The sources now agree."
      : `${conflict.resolved_by} chose ${conflict.resolved_value}${conflict.resolution === "custom" ? " (a new value)" : ""}.`;
  return (
    <article className="flex flex-col gap-1 rounded-xl border border-line bg-surface p-4">
      <h2 className="text-base font-semibold">
        {conflict.organization_name}: {conflict.field_label.toLowerCase()}
      </h2>
      <p className="text-sm">{how}</p>
      <p className="text-xs text-muted">
        {sourceLabel(conflict.a.source)}: {conflict.a.value} · {sourceLabel(conflict.b.source)}: {conflict.b.value}
        {conflict.resolved_at ? ` · ${timeAgo(conflict.resolved_at)}` : ""}
      </p>
    </article>
  );
}
