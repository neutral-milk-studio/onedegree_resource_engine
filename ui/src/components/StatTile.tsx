import type { ReactNode } from "react";

interface StatTileProps {
  label: string;
  /** null renders an em dash with the hint explaining why. */
  value: ReactNode | null;
  hint?: ReactNode;
  tone?: "default" | "attention";
}

/** One headline number: label, value, optional hint. */
export function StatTile({ label, value, hint, tone = "default" }: StatTileProps) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-xl border border-line bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <p
        className={`text-3xl font-semibold tabular-nums leading-tight ${
          tone === "attention" ? "text-urgent-ink" : "text-ink"
        }`}
      >
        {value ?? <span className="text-muted">—</span>}
      </p>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}
