// Word-level diff, like the inline highlighting in a code review.

export type DiffKind = "same" | "removed" | "added";

export interface DiffPart {
  kind: DiffKind;
  text: string;
}

const tokenize = (text: string): string[] => text.match(/[\p{L}\p{N}'’%.:@/_-]+|\s+|[^\s]/gu) ?? [];
const normalize = (token: string): string => token.toLowerCase().replace(/[.,;:]+$/, "");

function merge(parts: DiffPart[]): DiffPart[] {
  // Spaces between two changed words belong to the change, so "La Honda" counts once.
  const joined = parts.map((part, index) => {
    const before = parts[index - 1]?.kind;
    const between = /^\s+$/.test(part.text) && before && before !== "same" && before === parts[index + 1]?.kind;
    return between ? { kind: before, text: part.text } : part;
  });
  const merged: DiffPart[] = [];
  for (const part of joined) {
    const last = merged.at(-1);
    if (last?.kind === part.kind) last.text += part.text;
    else merged.push({ ...part });
  }
  return merged;
}

const MAX_LIST_ITEM_WORDS = 3;

interface ListSegments {
  /** Items at even positions, and the exact separators between them at odd positions. */
  segments: string[];
  items: string[];
}

const itemKey = (item: string) => item.trim().toLowerCase();

/**
 * Splits a list, keeping its exact text. Semicolons always mean a list. Commas only do when every
 * item is short, because a sentence can contain commas too.
 */
function parseList(text: string): ListSegments | null {
  const hasSemicolon = text.includes(";");
  const segments = text.split(hasSemicolon ? /(\s*;\s*(?:and\s+)?)/i : /(\s*,\s*(?:and\s+)?)/i);
  const items = segments.filter((_, index) => index % 2 === 0);
  if (items.length < 2 || items.some((item) => !item.trim())) return null;
  if (!hasSemicolon && items.some((item) => item.trim().split(/\s+/).length > MAX_LIST_ITEM_WORDS)) return null;
  return { segments, items };
}

function highlightSegments(list: ListSegments, other: Set<string>, kind: DiffKind): DiffPart[] {
  return merge(
    list.segments.map((text, index) => ({
      kind: index % 2 === 0 && !other.has(itemKey(text)) ? kind : ("same" as DiffKind),
      text,
    })),
  );
}

/**
 * Compares two values and returns what each side should highlight, always keeping the exact text.
 * Lists are compared item by item in any order, because a different order isn't a difference in fact.
 */
export function diffValues(before: string, after: string): { left: DiffPart[]; right: DiffPart[]; changes: number } {
  const a = parseList(before);
  const b = parseList(after);
  if (!a || !b) return diffWords(before, after);
  const setA = new Set(a.items.map(itemKey));
  const setB = new Set(b.items.map(itemKey));
  return {
    left: highlightSegments(a, setB, "removed"),
    right: highlightSegments(b, setA, "added"),
    changes: [...setA].filter((item) => !setB.has(item)).length + [...setB].filter((item) => !setA.has(item)).length,
  };
}

export function diffWords(before: string, after: string): { left: DiffPart[]; right: DiffPart[]; changes: number } {
  const a = tokenize(before);
  const b = tokenize(after);
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        normalize(a[i]) === normalize(b[j]) ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const left: DiffPart[] = [];
  const right: DiffPart[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && normalize(a[i]) === normalize(b[j])) {
      left.push({ kind: "same", text: a[i] });
      right.push({ kind: "same", text: b[j] });
      i += 1;
      j += 1;
    } else if (j < b.length && (i === a.length || table[i][j + 1] >= table[i + 1][j])) {
      right.push({ kind: /^\s+$/.test(b[j]) ? "same" : "added", text: b[j] });
      j += 1;
    } else {
      left.push({ kind: /^\s+$/.test(a[i]) ? "same" : "removed", text: a[i] });
      i += 1;
    }
  }
  const mergedLeft = merge(left);
  const mergedRight = merge(right);
  const count = (parts: DiffPart[], kind: DiffKind) => parts.filter((part) => part.kind === kind).length;
  return {
    left: mergedLeft,
    right: mergedRight,
    changes: Math.max(count(mergedLeft, "removed"), count(mergedRight, "added")),
  };
}
