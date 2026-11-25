// Locates evidence passages in a PDF page's text layer so they can be marked.
//
// pdf.js splits a page into text items (runs of text) differently from the backend's
// pypdfium2 text, so matching ignores what differs between the two: whitespace and line
// breaks, hyphenation at line ends, table pipes, letter case, ligatures, the micro sign
// versus Greek mu, and typographic quotes and dashes. A dash directly before a digit is
// kept as a minus sign, so "-0.3 V" never matches "0.3 V".
//
// A passage is first searched as one contiguous run of text. A table row whose cells the
// text layer orders differently is then searched within single visual rows: every token
// must be found in one row, and the row must hold exactly the passage's values. When
// neither finds the passage with confidence, nothing is marked.

/** A text item of a rendered page: its text and, for table rows, where it sits. */
export interface PageItem {
  str: string;
  /** Baseline in PDF units (pdf.js transform[5]); larger is higher on the page. */
  y?: number;
  height?: number;
}

/** A marked character range [start, end) of one item, belonging to passage `passage`. */
export interface MarkRange {
  start: number;
  end: number;
  passage: number;
}

export interface PassageMatches {
  /** Per item index, the ranges to mark, in order. */
  ranges: Map<number, MarkRange[]>;
  /** Indexes (into the passages given) of the passages that were found. */
  found: number[];
}

const MIN_KEY_LENGTH = 12;
const SPACE = /[\s­|]/;
const DASH = /[-‐-―−]/;
const DIGIT = /\d/;
const EQUIVALENT: Record<string, string> = { "‘": "'", "’": "'", "“": '"', "”": '"' };
// A table row needs this many tokens, at least one of them a value, to be matched by row.
const MIN_ROW_TOKENS = 3;
const VALUE = /^[-+]?\d+(?:[.,]\d+)?$/;
const FOOTNOTE = /^\(\d{1,2}\)$/;

function normalise(char: string): string {
  return (EQUIVALENT[char] ?? char).normalize("NFKC").toLowerCase();
}

/** The matching key of `text` and, for every key character, the index it came from. */
function keyOf(text: string): { key: string; origin: number[] } {
  let key = "";
  const origin: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (SPACE.test(char)) continue;
    let out: string;
    if (DASH.test(char)) {
      // A sign before a value is kept; a hyphen or dash anywhere else is ignored.
      const before = i === 0 ? " " : text[i - 1];
      if (!DIGIT.test(text[i + 1] ?? "") || /[\p{L}\p{N}]/u.test(before)) continue;
      out = "-";
    } else {
      out = normalise(char);
    }
    for (const c of out) {
      key += c;
      origin.push(i);
    }
  }
  return { key, origin };
}

/** Characters of `text` that take part in matching, in normalised form. */
export function matchKey(text: string): string {
  return keyOf(text).key;
}

/** Item and offset of every character of the page string built from `items`. */
function pageString(items: PageItem[]): { text: string; item: number[]; offset: number[] } {
  let text = "";
  const item: number[] = [];
  const offset: number[] = [];
  items.forEach((entry, index) => {
    for (let i = 0; i < entry.str.length; i++) {
      text += entry.str[i];
      item.push(index);
      offset.push(i);
    }
    // Item boundaries count as spaces (pdf.js often omits the space between items).
    text += "\n";
    item.push(-1);
    offset.push(-1);
  });
  return { text, item, offset };
}

function addRange(ranges: Map<number, MarkRange[]>, item: number, start: number, end: number, passage: number) {
  const list = ranges.get(item) ?? [];
  const last = list[list.length - 1];
  if (last && last.passage === passage && start <= last.end) last.end = Math.max(last.end, end);
  else list.push({ start, end, passage });
  ranges.set(item, list);
}

/** Mark `passage` as a contiguous run of text; false if it is not on the page. */
function markContiguous(
  page: ReturnType<typeof pageString>,
  haystack: { key: string; origin: number[] },
  passage: string,
  index: number,
  ranges: Map<number, MarkRange[]>,
): boolean {
  const needle = matchKey(passage);
  if (needle.length < MIN_KEY_LENGTH) return false;
  const at = haystack.key.indexOf(needle);
  if (at === -1) return false;
  const spans = new Map<number, [number, number]>();
  for (let k = at; k < at + needle.length; k++) {
    const i = haystack.origin[k];
    const item = page.item[i];
    const offset = page.offset[i];
    const span = spans.get(item);
    if (span) span[1] = offset + 1;
    else spans.set(item, [offset, offset + 1]);
  }
  for (const [item, [start, end]] of spans) addRange(ranges, item, start, end, index);
  return true;
}

interface Token {
  key: string;
  item: number;
  start: number;
  end: number;
}

function tokensOf(items: PageItem[], members: number[]): Token[] {
  const tokens: Token[] = [];
  for (const item of members) {
    for (const match of items[item].str.matchAll(/\S+/g)) {
      const key = matchKey(match[0]);
      if (key) tokens.push({ key, item, start: match.index, end: match.index + match[0].length });
    }
  }
  return tokens;
}

/** Items grouped into visual rows: items whose vertical extents overlap by at least half. */
export function visualRows(items: PageItem[]): number[][] {
  const placed = items
    .map((entry, index) => ({ index, y: entry.y, height: entry.height || 0 }))
    .filter((entry) => entry.y !== undefined && entry.height > 0 && items[entry.index].str.trim())
    .sort((a, b) => b.y! - a.y!);
  const rows: { top: number; bottom: number; members: number[] }[] = [];
  for (const entry of placed) {
    const bottom = entry.y!;
    const top = bottom + entry.height;
    const row = rows.find((r) => Math.min(r.top, top) - Math.max(r.bottom, bottom) >= 0.5 * entry.height);
    if (row) {
      row.members.push(entry.index);
      row.top = Math.max(row.top, top);
      row.bottom = Math.min(row.bottom, bottom);
    } else {
      rows.push({ top, bottom, members: [entry.index] });
    }
  }
  return rows.map((r) => r.members.sort((a, b) => a - b));
}

function values(keys: string[]): string[] {
  return keys.filter((k) => VALUE.test(k) && !FOOTNOTE.test(k)).sort();
}

/** Mark a table-row passage whose tokens all lie in exactly one visual row. */
function markRow(items: PageItem[], passage: string, index: number, ranges: Map<number, MarkRange[]>): boolean {
  const wanted = passage
    .split(/\s+/)
    .map(matchKey)
    .filter((k) => k && !FOOTNOTE.test(k));
  const wantedValues = values(wanted);
  if (wanted.length < MIN_ROW_TOKENS || wantedValues.length === 0) return false;
  const candidates: Token[][] = [];
  for (const members of visualRows(items)) {
    const tokens = tokensOf(items, members);
    if (values(tokens.map((t) => t.key)).join(" ") !== wantedValues.join(" ")) continue;
    const used = new Set<number>();
    const matched: Token[] = [];
    for (const key of wanted) {
      const position = tokens.findIndex((t, i) => !used.has(i) && t.key === key);
      if (position === -1) break;
      used.add(position);
      matched.push(tokens[position]);
    }
    if (matched.length === wanted.length) candidates.push(matched);
  }
  // Two rows with the same tokens and values: not confident which one is meant.
  if (candidates.length !== 1) return false;
  for (const token of candidates[0].sort((a, b) => a.item - b.item || a.start - b.start)) {
    addRange(ranges, token.item, token.start, token.end, index);
  }
  return true;
}

/** Find each passage in the page's text items and return what to mark. */
export function locatePassages(items: PageItem[], passages: string[]): PassageMatches {
  const page = pageString(items);
  const haystack = keyOf(page.text);
  const ranges = new Map<number, MarkRange[]>();
  const found: number[] = [];
  passages.forEach((passage, index) => {
    if (markContiguous(page, haystack, passage, index, ranges) || markRow(items, passage, index, ranges)) {
      found.push(index);
    }
  });
  for (const list of ranges.values()) list.sort((a, b) => a.start - b.start);
  return { ranges, found };
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** HTML for one text item with its marked ranges wrapped in <mark>; marks of the current
 *  passage get an extra class. */
export function renderItem(text: string, ranges: MarkRange[] | undefined, current = -1): string {
  if (!ranges || ranges.length === 0) return escapeHtml(text);
  let html = "";
  let at = 0;
  for (const { start, end, passage } of ranges) {
    if (start < at) continue;
    const className = passage === current ? "evidence-mark is-current" : "evidence-mark";
    html +=
      escapeHtml(text.slice(at, start)) +
      `<mark class="${className}" data-passage="${passage}">${escapeHtml(text.slice(start, end))}</mark>`;
    at = end;
  }
  return html + escapeHtml(text.slice(at));
}
