import type { Trust } from "../api/review";

const STYLES: Record<Trust["level"], string> = {
  high: "bg-good-soft text-good",
  medium: "bg-medium-soft text-medium-ink",
  low: "bg-urgent-soft text-urgent-ink",
  unknown: "bg-low-soft text-low-ink",
};
const MARKS: Record<Trust["level"], string> = { high: "✓", medium: "!", low: "!!", unknown: "?" };

/** Trust badge: always text + score, never color alone. */
export function TrustBadge({ trust }: { trust: Trust }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[trust.level]}`}
      title={trust.score === null ? "Trust score: not checked yet" : `Trust score ${trust.score} of 100`}
    >
      <span aria-hidden="true">{MARKS[trust.level]}</span>
      {trust.badge}
      {trust.score !== null && <span className="tabular-nums opacity-80">· {trust.score}</span>}
    </span>
  );
}
