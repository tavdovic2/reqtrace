// Review logic of the Requirements tab, kept free of React so it can be tested with
// node --test: status filters and counts, optimistic row updates, and the keyboard map.

export type ReviewStatus = "proposed" | "accepted" | "rejected";
export type StatusFilter = "all" | ReviewStatus | "not_found";

export const STATUS_FILTERS: StatusFilter[] = ["all", "proposed", "accepted", "rejected", "not_found"];

/** The fields of a requirement row that review touches. */
export interface Reviewable {
  id: number;
  status: ReviewStatus;
  not_found: boolean;
  edited_text: string | null;
  reviewer_note: string | null;
  reviewed_at: string | null;
}

export function matchesFilter(row: Pick<Reviewable, "status" | "not_found">, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "not_found") return row.not_found;
  return row.status === filter;
}

export function filterRows<T extends Pick<Reviewable, "status" | "not_found">>(rows: T[], filter: StatusFilter): T[] {
  return rows.filter((row) => matchesFilter(row, filter));
}

/** Rows per filter. "not_found" rows are also counted under their status. */
export function countRows(rows: Pick<Reviewable, "status" | "not_found">[]): Record<StatusFilter, number> {
  const counts: Record<StatusFilter, number> = { all: 0, proposed: 0, accepted: 0, rejected: 0, not_found: 0 };
  for (const row of rows) {
    counts.all += 1;
    counts[row.status] += 1;
    if (row.not_found) counts.not_found += 1;
  }
  return counts;
}

/** A review change as sent to the API: only the fields present are changed. */
export interface ReviewPatch {
  status?: ReviewStatus;
  edited_text?: string | null;
  reviewer_note?: string | null;
}

/** The rows with `patch` applied to those in `ids`, as the server will store them:
 *  a status sets the review time (now), "proposed" clears it. Other rows are unchanged. */
export function applyPatch<T extends Reviewable>(rows: T[], ids: Iterable<number>, patch: ReviewPatch, now: Date): T[] {
  const targets = new Set(ids);
  return rows.map((row) => {
    if (!targets.has(row.id)) return row;
    const next = { ...row };
    if (patch.status !== undefined) {
      next.status = patch.status;
      next.reviewed_at = patch.status === "proposed" ? null : now.toISOString();
    }
    if (patch.edited_text !== undefined) next.edited_text = patch.edited_text;
    if (patch.reviewer_note !== undefined) next.reviewer_note = patch.reviewer_note?.trim() || null;
    return next;
  });
}

/** Put back the previous version of the given rows (rollback after a failed request),
 *  leaving every other row as it is now. */
export function restoreRows<T extends Reviewable>(rows: T[], previous: T[]): T[] {
  const byId = new Map(previous.map((row) => [row.id, row]));
  return rows.map((row) => byId.get(row.id) ?? row);
}

/** Replace rows by id with the server's version. */
export function replaceRows<T extends Reviewable>(rows: T[], updated: T[]): T[] {
  return restoreRows(rows, updated);
}

export type ReviewAction =
  | { type: "move"; delta: 1 | -1 }
  | { type: "status"; status: ReviewStatus }
  | { type: "edit" }
  | { type: "note" }
  | { type: "select" }
  | { type: "cancel" }
  | { type: "help" };

/** What a keyboard handler needs to know about the element that has focus. */
export interface FocusTarget {
  tagName?: string;
  type?: string;
  isContentEditable?: boolean;
}

export interface KeyInput {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  target?: FocusTarget | null;
}

const TEXT_INPUT_TYPES = new Set(["", "text", "search", "email", "url", "tel", "password", "number"]);

/** True while the user is typing: a text input, a text area, a select or editable text. */
export function isTypingTarget(target: FocusTarget | null | undefined): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = (target.tagName ?? "").toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  return tag === "INPUT" && TEXT_INPUT_TYPES.has((target.type ?? "").toLowerCase());
}

/** Controls that Space already activates on their own. */
function activatesWithSpace(target: FocusTarget | null | undefined): boolean {
  const tag = (target?.tagName ?? "").toUpperCase();
  return tag === "BUTTON" || tag === "A" || (tag === "INPUT" && !isTypingTarget(target));
}

/** The review action for a key press, or null when the key is not a shortcut here.
 *
 *  ↑/↓ and K/J move between rows, A accept, R reject, U reset to proposed, E edit text,
 *  N note, Space select, ? shortcut list, Escape cancel. While typing in a field only
 *  Escape works; with Ctrl, Alt or Cmd nothing does, so browser shortcuts stay intact. */
export function reviewKeyAction(input: KeyInput): ReviewAction | null {
  if (input.ctrlKey || input.metaKey || input.altKey) return null;
  if (input.key === "Escape") return { type: "cancel" };
  if (isTypingTarget(input.target)) return null;
  switch (input.key) {
    case "ArrowDown":
    case "j":
    case "J":
      return { type: "move", delta: 1 };
    case "ArrowUp":
    case "k":
    case "K":
      return { type: "move", delta: -1 };
    case "a":
    case "A":
      return { type: "status", status: "accepted" };
    case "r":
    case "R":
      return { type: "status", status: "rejected" };
    case "u":
    case "U":
      return { type: "status", status: "proposed" };
    case "e":
    case "E":
      return { type: "edit" };
    case "n":
    case "N":
      return { type: "note" };
    case " ":
    case "Spacebar":
      return activatesWithSpace(input.target) ? null : { type: "select" };
    case "?":
      return { type: "help" };
    default:
      return null;
  }
}

/** The index after moving `delta` rows, kept inside the list (no wrap-around). */
export function moveIndex(current: number, delta: number, length: number): number {
  if (length === 0) return -1;
  if (current < 0) return delta > 0 ? 0 : length - 1;
  return Math.min(length - 1, Math.max(0, current + delta));
}
