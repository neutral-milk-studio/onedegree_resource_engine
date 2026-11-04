// Open findings by category: ranked horizontal bars in harm order, one hue (single series,
// so no legend), value at each bar tip, a hover/focus readout, and a table view.
import { useState } from "react";

import { FINDING_CATEGORIES, type FindingCategory } from "../api/dashboard";
import { CATEGORY_HELP, CATEGORY_LABELS, formatCount } from "../format";

export function FindingsChart({ counts, checksRun }: { counts: Record<FindingCategory, number>; checksRun: number }) {
  const [showTable, setShowTable] = useState(false);
  const [hovered, setHovered] = useState<FindingCategory | null>(null);
  const rows = FINDING_CATEGORIES.map((category) => ({ category, value: counts[category] ?? 0 }));
  const max = Math.max(...rows.map((row) => row.value), 0);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  const openTotal = rows.reduce((sum, row) => sum + (row.category === "verified" ? 0 : row.value), 0);
  const hoveredRow = rows.find((row) => row.category === hovered);

  return (
    <section aria-labelledby="findings-title" className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 id="findings-title" className="text-base font-semibold">
            Findings by category
          </h2>
          <p className="text-xs text-muted">Open findings, most harmful first. Verified counts records that matched.</p>
        </div>
        {total > 0 && (
          <button type="button" className="!px-2 !py-1 text-xs" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Show chart" : "Show table"}
          </button>
        )}
      </div>

      {total === 0 ? (
        <p className="rounded-lg bg-surface-2 p-4 text-sm text-muted">
          {checksRun === 0
            ? "No website checks have run yet. Findings appear here after the first AI check."
            : "No open findings. Every checked record is resolved or verified."}
        </p>
      ) : showTable ? (
        <table className="w-full text-sm">
          <caption className="visually-hidden">Findings by category</caption>
          <thead>
            <tr className="text-left text-muted">
              <th className="py-1 font-medium">Category</th>
              <th className="py-1 text-right font-medium">Open</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.category} className="border-t border-line">
                <td className="py-1.5">{CATEGORY_LABELS[row.category]}</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(row.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <>
          <p className="mb-2 min-h-[2.5rem] text-xs text-muted" aria-live="polite">
            {hoveredRow ? (
              <>
                <span className="font-semibold text-ink">
                  {CATEGORY_LABELS[hoveredRow.category]}: {formatCount(hoveredRow.value)}
                  {hoveredRow.category === "verified" ? " records" : " open"}
                </span>{" "}
                ·{" "}
                {hoveredRow.category === "verified"
                  ? "records whose latest check matched the website"
                  : `${openTotal ? Math.round((hoveredRow.value / openTotal) * 100) : 0}% of open findings`}
                <br />
                {CATEGORY_HELP[hoveredRow.category]}
              </>
            ) : (
              "Point at or tab to a bar for details."
            )}
          </p>
          <ul className="flex flex-col gap-1" aria-label="Findings by category">
            {rows.map((row) => {
              const share = max ? (row.value / max) * 100 : 0;
              return (
                <li
                  key={row.category}
                  className={`grid grid-cols-[minmax(7rem,11rem)_1fr] items-center gap-3 rounded px-1 ${
                    hovered === row.category ? "bg-surface-2" : ""
                  }`}
                  onMouseEnter={() => setHovered(row.category)}
                  onMouseLeave={() => setHovered(null)}
                >
                  <span className="truncate text-sm text-ink">{CATEGORY_LABELS[row.category]}</span>
                  <div
                    tabIndex={0}
                    role="img"
                    aria-label={`${CATEGORY_LABELS[row.category]}: ${row.value}${row.category === "verified" ? " records" : " open"}`}
                    className="h-8 pr-12 outline-offset-2"
                    onFocus={() => setHovered(row.category)}
                    onBlur={() => setHovered(null)}
                  >
                    {/* The track excludes the label gutter, so bar length stays proportional. */}
                    <div className="relative flex h-full items-center">
                      {row.value > 0 && (
                        <span
                          data-testid={`bar-${row.category}`}
                          className="block h-4 rounded-r bg-series"
                          style={{ width: `${share}%`, minWidth: "4px" }}
                        />
                      )}
                      <span
                        className="absolute whitespace-nowrap pl-2 text-sm tabular-nums text-ink"
                        style={{ left: `${share}%` }}
                      >
                        {formatCount(row.value)}
                      </span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
