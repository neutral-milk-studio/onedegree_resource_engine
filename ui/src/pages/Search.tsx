// Search in plain language. Results never include listings flagged for review or closed.
import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import type { SearchApi, SearchResponse, SearchResult } from "../api/search";
import { TrustBadge } from "../components/TrustBadge";
import { sourceLabel } from "../format";

const EXAMPLES = [
  "I just lost my job and can't pay rent",
  "free food this weekend",
  "help in Spanish with legal problems",
  "a place to sleep tonight",
];
const PAGE = 10;

/** Renders «marked» words as <mark> without injecting HTML. */
function Highlighted({ text }: { text: string }) {
  const parts = text.split(/(«[^»]*»)/g).filter(Boolean);
  return (
    <>
      {parts.map((part, index) =>
        part.startsWith("«") ? (
          <mark key={index} className="rounded bg-medium-soft px-0.5 text-ink">
            {part.slice(1, -1)}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

export function Search({ api }: { api: SearchApi }) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (text: string, offset = 0) => {
    setError(null);
    setBusy(true);
    try {
      const next = await api.search(text, offset, PAGE);
      setResponse(next);
      setResults(offset ? [...results, ...next.results] : next.results);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Search failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(query.trim());
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 p-4 lg:p-6">
      <header>
        <h1 className="text-2xl font-semibold">Search</h1>
        <p className="text-sm text-muted">Describe what someone needs, in their own words.</p>
      </header>

      <form role="search" onSubmit={submit} className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-4">
        <label htmlFor="search-q" className="text-sm font-medium">
          What do you need help with?
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="search-q"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="For example: I just lost my job and can't pay rent"
            className="min-w-0 flex-1 !py-2.5 text-base"
            autoComplete="off"
          />
          <button type="submit" className="primary !px-5" disabled={busy || query.trim().length < 2}>
            {busy ? "Searching…" : "Search"}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted">Try:</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="!rounded-full !px-2.5 !py-0.5 text-xs"
              onClick={() => {
                setQuery(example);
                void run(example);
              }}
            >
              {example}
            </button>
          ))}
        </div>
      </form>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {response && (
        <section aria-labelledby="results-title" className="flex flex-col gap-3">
          <div aria-live="polite">
            <h2 id="results-title" className="text-lg font-semibold">
              {response.total === 0
                ? "No matching services"
                : `${response.total} ${response.total === 1 ? "service" : "services"} for “${response.query}”`}
            </h2>
            {response.needs.length > 0 && (
              <p className="text-sm text-muted">
                Looking for help with {response.needs.join(" and ").toLowerCase()}, so we also searched for{" "}
                {response.added_terms.slice(0, 6).join(", ")}.
              </p>
            )}
            {response.hidden > 0 && (
              <p className="text-sm text-muted">
                {response.hidden} matching {response.hidden === 1 ? "listing is" : "listings are"} not shown because{" "}
                {response.hidden === 1 ? "it is" : "they are"} flagged for review, closed, or inactive.
              </p>
            )}
            {response.total === 0 && (
              <p className="text-sm text-muted">Try fewer or different words, such as “rent”, “food” or “shelter”.</p>
            )}
          </div>

          <ol className="flex flex-col gap-3">
            {results.map((result) => (
              <li key={result.service_id}>
                <article className="flex flex-col gap-1.5 rounded-xl border border-line bg-surface p-4" aria-labelledby={`r-${result.service_id}`}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 id={`r-${result.service_id}`} className="text-base font-semibold">
                        {result.name}
                      </h3>
                      <p className="text-sm text-muted">
                        {result.organization_name}
                        {result.city ? ` · ${result.city}` : ""}
                      </p>
                    </div>
                    <TrustBadge trust={result.trust} />
                  </div>
                  <p className="text-sm">
                    <Highlighted text={result.snippet} />
                  </p>
                  <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                    {result.phone && <a href={`tel:${result.phone.replace(/[^\d+]/g, "")}`}>Call {result.phone}</a>}
                    {result.website && (
                      <a href={result.website} target="_blank" rel="noreferrer">
                        Website<span className="visually-hidden"> (opens in a new tab)</span>
                      </a>
                    )}
                    <span className="text-xs text-muted">From {sourceLabel(result.source)}</span>
                  </p>
                </article>
              </li>
            ))}
          </ol>
          {results.length < response.total && (
            <button type="button" className="self-center" disabled={busy} onClick={() => void run(response.query, results.length)}>
              {busy ? "Loading…" : `Show more (${response.total - results.length} left)`}
            </button>
          )}
        </section>
      )}
    </div>
  );
}
