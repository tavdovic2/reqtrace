// Locates an evidence passage in a PDF page's text layer so it can be marked.
//
// pdf.js splits a page into text items (runs of text) differently from the
// backend's pypdfium2 text, so matching ignores everything that differs between
// the two: whitespace, hyphens (including hyphenation at line ends), table pipes,
// letter case and typographic quotes and dashes. Only an exact match on that
// normalised text is highlighted; when there is none, nothing is marked.

const SKIPPED = /[\s\-­‐-―−|]/;
const EQUIVALENT: Record<string, string> = {
  "‘": "'",
  "’": "'",
  "“": '"',
  "”": '"',
};

interface Position {
  item: number;
  offset: number;
}

/** Per text item, the [start, end) character range to mark. The match is contiguous,
 * so each item has at most one range. */
export type ItemRanges = Map<number, [number, number]>;

function normalise(char: string): string {
  const unified = EQUIVALENT[char] ?? char;
  return unified.normalize("NFKC").toLowerCase();
}

/** Characters of `text` that take part in matching, in normalised form. */
export function matchKey(text: string): string {
  let key = "";
  for (const char of text) if (!SKIPPED.test(char)) key += normalise(char);
  return key;
}

/** Find `passage` in the page's text items and return the ranges to mark, or null. */
export function locatePassage(items: string[], passage: string): ItemRanges | null {
  const needle = matchKey(passage);
  if (needle.length < 12) return null;

  let haystack = "";
  const positions: Position[] = [];
  items.forEach((text, item) => {
    for (let offset = 0; offset < text.length; offset++) {
      const char = text[offset];
      if (SKIPPED.test(char)) continue;
      const key = normalise(char);
      haystack += key;
      for (let i = 0; i < key.length; i++) positions.push({ item, offset });
    }
  });

  const start = haystack.indexOf(needle);
  if (start === -1) return null;

  const ranges: ItemRanges = new Map();
  for (const { item, offset } of positions.slice(start, start + needle.length)) {
    const range = ranges.get(item);
    if (range) range[1] = offset + 1;
    else ranges.set(item, [offset, offset + 1]);
  }
  return ranges;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** HTML for one text item with its marked range wrapped in <mark>. */
export function renderItem(text: string, range: [number, number] | undefined): string {
  if (!range) return escapeHtml(text);
  const [start, end] = range;
  return (
    escapeHtml(text.slice(0, start)) +
    `<mark class="evidence-mark">${escapeHtml(text.slice(start, end))}</mark>` +
    escapeHtml(text.slice(end))
  );
}
