import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import type { Category, ReviewApi } from "../api/client";
import { sourceLabel } from "./review/Badges";
import { QueueCard } from "./review/QueueCard";
import { RecordPanel } from "./review/RecordPanel";
import { buildRows, CATEGORY_LABELS, filterRows, TIER_LABELS, TIERS, type ListView, type Tier } from "./review/queue";

const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];

export function RecordsReview({ api }: { api: ReviewApi }) {
  const records = useQuery({ queryKey: ["flagged"], queryFn: () => api.listFlagged() });
  const findings = useQuery({ queryKey: ["findings"], queryFn: () => api.listAllFindings() });
  const clashes = useQuery({ queryKey: ["clashes"], queryFn: () => api.listClashes() });
  const [view, setView] = useState<ListView>("flagged");
  const [tier, setTier] = useState<Tier | "">("");
  const [category, setCategory] = useState<Category | "">("");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string>();

  const allRows = useMemo(
    () => buildRows(records.data ?? [], findings.data ?? [], clashes.data ?? []),
    [records.data, findings.data, clashes.data],
  );
  const rows = useMemo(
    () => filterRows(allRows, { view, tier, category, source, search }),
    [allRows, view, tier, category, source, search],
  );
  const sources = useMemo(() => [...new Set(allRows.flatMap((row) => row.sources))].sort(), [allRows]);
  const tierCounts = useMemo(
    () => Object.fromEntries(TIERS.map((item) => [item, allRows.filter((row) => row.tier === item).length])),
    [allRows],
  );
  const toReview = allRows.filter((row) => row.tier).length;
  const selectedRow = allRows.find((row) => row.id === selectedId);

  useEffect(() => {
    if (!selectedId && rows.length) setSelectedId(rows[0].id);
  }, [rows, selectedId]);

  const move = (step: number) => {
    const index = rows.findIndex((row) => row.id === selectedId);
    const next = rows[Math.min(Math.max(index + step, 0), rows.length - 1)];
    if (next) setSelectedId(next.id);
  };

  /** Goes to the highest-priority record still needing review, other than this one. */
  const nextToReview = () => {
    const next = rows.find((row) => row.tier && row.id !== selectedId);
    if (next) setSelectedId(next.id);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key !== "j" && key !== "k") return;
      const index = rows.findIndex((row) => row.id === selectedId);
      const next = rows[key === "j" ? Math.min(index + 1, rows.length - 1) : Math.max(index - 1, 0)];
      if (next) setSelectedId(next.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, selectedId]);

  useEffect(() => {
    document.querySelector('.queue-card[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  if (records.isError || findings.isError || clashes.isError) {
    return (
      <div className="alert page-alert" role="alert">
        Couldn't load the review list. Check that the API is running.{" "}
        <button
          onClick={() => {
            void records.refetch();
            void findings.refetch();
            void clashes.refetch();
          }}
        >
          Try again
        </button>
      </div>
    );
  }

  const loading = records.isPending || findings.isPending || clashes.isPending;

  return (
    <div className="review">
      <section className="queue" aria-label="Review list">
        <div className="queue-head">
          <h2>{toReview} records to review</h2>
          <div className="segmented" role="group" aria-label="Show">
            <button type="button" aria-pressed={view === "flagged"} onClick={() => setView("flagged")}>
              To review
            </button>
            <button type="button" aria-pressed={view === "all"} onClick={() => setView("all")}>
              All {allRows.length}
            </button>
          </div>
        </div>
        <div className="tier-filter" role="group" aria-label="Filter by priority">
          <button type="button" aria-pressed={tier === ""} onClick={() => setTier("")}>
            All
          </button>
          {TIERS.map((item) => (
            <button
              key={item}
              type="button"
              className={`tier-button tier-button-${item}`}
              aria-pressed={tier === item}
              onClick={() => setTier(tier === item ? "" : item)}
            >
              {TIER_LABELS[item]} {tierCounts[item]}
            </button>
          ))}
        </div>
        <div className="filters">
          <label className="search">
            <span className="visually-hidden">Search</span>
            <input
              type="search"
              value={search}
              placeholder="Search service or organization"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label>
            <span className="visually-hidden">Category</span>
            <select value={category} onChange={(event) => setCategory(event.target.value as Category | "")}>
              <option value="">All categories</option>
              {CATEGORIES.map((item) => (
                <option key={item} value={item}>
                  {CATEGORY_LABELS[item]}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="visually-hidden">Source</span>
            <select value={source} onChange={(event) => setSource(event.target.value)}>
              <option value="">All sources</option>
              {sources.map((item) => (
                <option key={item} value={item}>
                  {sourceLabel(item)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {loading ? (
          <p className="panel-message">Loading the review list…</p>
        ) : rows.length === 0 ? (
          <p className="panel-message">{toReview === 0 ? "Everything is reviewed." : "No records match these filters."}</p>
        ) : (
          <ul className="queue-list" aria-label={`${rows.length} records`}>
            {rows.map((row) => (
              <QueueCard key={row.id} row={row} selected={row.id === selectedId} onSelect={() => setSelectedId(row.id)} />
            ))}
          </ul>
        )}
        <p className="shortcuts">
          <kbd>J</kbd>
          <kbd>K</kbd> move · <kbd>A</kbd> apply · <kbd>E</kbd> edit · <kbd>D</kbd> dismiss
        </p>
        <span className="visually-hidden" aria-live="polite">
          {rows.length} records shown
        </span>
      </section>
      <section className="panel" aria-label="Record details">
        {selectedId ? (
          <RecordPanel api={api} recordId={selectedId} row={selectedRow} onNext={nextToReview} />
        ) : (
          <p className="panel-message">Select a record to review it.</p>
        )}
      </section>
      {/* `move` keeps the arrow buttons on narrow screens in step with the J and K keys. */}
      <nav className="mobile-nav" aria-label="Move between records">
        <button type="button" onClick={() => move(-1)}>
          Previous
        </button>
        <button type="button" onClick={() => move(1)}>
          Next
        </button>
      </nav>
    </div>
  );
}
