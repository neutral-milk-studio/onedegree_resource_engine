import type { ReactNode } from "react";

import { SourceBadge } from "./Badges";
import { diffValues, type DiffPart } from "./wordDiff";

export interface SplitSide {
  /** A source id for a badge, or plain text such as "Current record". */
  source?: string;
  label?: string;
  when?: string;
  link?: string;
  value: string;
}

function Highlighted({ parts }: { parts: DiffPart[] }) {
  return (
    <>
      {parts.map((part, index) => {
        if (part.kind === "removed") return <del key={index}>{part.text}</del>;
        if (part.kind === "added") return <ins key={index}>{part.text}</ins>;
        return <span key={index}>{part.text}</span>;
      })}
    </>
  );
}

function SideHeader({ side }: { side: SplitSide }) {
  return (
    <div className="split-header">
      {side.source ? <SourceBadge source={side.source} /> : <span className="split-label">{side.label}</span>}
      {side.when && <span className="split-when">{side.when}</span>}
      {side.link && (
        <a href={side.link} target="_blank" rel="noreferrer">
          Open source<span className="visually-hidden"> (opens in a new tab)</span> ↗
        </a>
      )}
    </div>
  );
}

interface SplitDiffProps {
  left: SplitSide;
  right: SplitSide;
  /** Shown instead of the right value, for a source that couldn't be read. */
  rightBody?: ReactNode;
}

export function SplitDiff({ left, right, rightBody }: SplitDiffProps) {
  const { left: leftParts, right: rightParts } = diffValues(left.value, right.value);
  return (
    <div className="split">
      <div className="split-side split-left">
        <SideHeader side={left} />
        <p className="split-value">{rightBody ? left.value : <Highlighted parts={leftParts} />}</p>
      </div>
      <div className="split-side split-right">
        <SideHeader side={right} />
        {rightBody ?? (
          <p className="split-value">
            <Highlighted parts={rightParts} />
          </p>
        )}
      </div>
    </div>
  );
}

export function countDifferences(before: string, after: string): number {
  return diffValues(before, after).changes;
}

export function DifferenceCount({ count }: { count: number }) {
  return (
    <span className="diff-count">
      {count} {count === 1 ? "difference" : "differences"}
    </span>
  );
}
