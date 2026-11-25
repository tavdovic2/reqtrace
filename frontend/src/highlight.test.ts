// Run with: npm test (Node's built-in test runner; Node 24 strips the types itself).
import assert from "node:assert/strict";
import { test } from "node:test";

import { locatePassages, matchKey, renderItem, visualRows, type PageItem } from "./highlight.ts";

const lines = (...texts: string[]): PageItem[] => texts.map((str, i) => ({ str, y: 700 - i * 12, height: 10 }));

/** The marked text of each item, for readable assertions. */
function marked(items: PageItem[], passages: string[]): string[] {
  const { ranges } = locatePassages(items, passages);
  return [...ranges.entries()].flatMap(([item, list]) => list.map((r) => items[item].str.slice(r.start, r.end)));
}

const PROSE = lines(
  "6.2 Modbus RTU",
  "The default slave address is 247; addresses",
  "from 1 to 247 can be config-",
  "ured. The bus termination resistor",
  "must be 120 Ω.",
);

test("matching ignores whitespace, hyphenation, case and typographic quotes", () => {
  assert.equal(matchKey("Config-\r\nured “OK”"), 'configured"ok"');
});

test("a one-line quote is marked alone, not its whole item", () => {
  assert.deepEqual(marked(PROSE, ["The bus termination resistor"]), ["The bus termination resistor"]);
});

test("a quote across a line break and a hyphenated line end is marked in each item", () => {
  // The hyphen at the line end is not part of the word, so it stays unmarked.
  assert.deepEqual(marked(PROSE, ["addresses from 1 to 247 can be configured."]), [
    "addresses",
    "from 1 to 247 can be config",
    "ured.",
  ]);
});

test("a quote that starts and ends inside items leaves the neighbouring words unmarked", () => {
  const { ranges } = locatePassages(PROSE, ["slave address is 247; addresses from 1"]);
  assert.deepEqual(ranges.get(1), [{ start: 12, end: 43, passage: 0 }]);
  assert.deepEqual(ranges.get(2), [{ start: 0, end: 6, passage: 0 }]);
  assert.equal(PROSE[2].str.slice(0, 6), "from 1");
});

test("text from the backend's page layer matches despite different line breaks", () => {
  const backend = "The default slave address is 247; addresses\r\nfrom 1 to 247 can be config-\r\nured.";
  assert.deepEqual(locatePassages(PROSE, [backend]).found, [0]);
});

test("ligatures and the micro sign match their plain forms", () => {
  const items = lines("Use a 0.1-µF capacitor for eﬃcient ﬁltering.");
  assert.deepEqual(marked(items, ["Use a 0.1-μF capacitor for efficient filtering."]), [
    "Use a 0.1-µF capacitor for eﬃcient ﬁltering.",
  ]);
});

const RATINGS: PageItem[] = [
  { str: "PARAMETER", y: 600, height: 8 },
  { str: "MIN", y: 600, height: 8 },
  { str: "MAX", y: 600, height: 8 },
  { str: "UNIT", y: 600, height: 8 },
  // Cells of one row, in another order than the stored quote (value cells first).
  { str: "–0.3", y: 588, height: 8 },
  { str: "6", y: 588, height: 8 },
  { str: "Supply voltage", y: 588.5, height: 8 },
  { str: "V+", y: 588, height: 8 },
  { str: "V", y: 588, height: 8 },
  { str: "–0.3", y: 576, height: 8 },
  { str: "6", y: 576, height: 8 },
  { str: "Voltage at SCL, SDA", y: 576, height: 8 },
  { str: "V", y: 576, height: 8 },
  { str: "–55", y: 564, height: 8 },
  { str: "155", y: 564, height: 8 },
  { str: "Junction temperature", y: 564, height: 8 },
  { str: "°C", y: 564, height: 8 },
];

test("visual rows group items whose vertical positions overlap", () => {
  assert.deepEqual(visualRows(RATINGS), [
    [0, 1, 2, 3],
    [4, 5, 6, 7, 8],
    [9, 10, 11, 12],
    [13, 14, 15, 16],
  ]);
});

test("a table row whose cells come in another order is marked within its own row", () => {
  const { ranges, found } = locatePassages(RATINGS, ["Supply voltage V+ -0.3 6 V"]);
  assert.deepEqual(found, [0]);
  assert.deepEqual(
    [...ranges.keys()].sort((a, b) => a - b),
    [4, 5, 6, 7, 8],
  );
});

test("a table row with a different value is not marked, even if its words match", () => {
  assert.deepEqual(locatePassages(RATINGS, ["Supply voltage V+ -0.3 5.5 V"]).found, []);
  assert.deepEqual(locatePassages(RATINGS, ["Junction temperature -55 150 °C"]).found, []);
  // A minus sign is a value, not a hyphen.
  assert.deepEqual(locatePassages(RATINGS, ["Supply voltage V+ 0.3 6 V"]).found, []);
});

test("a passage that is not on the page marks nothing", () => {
  assert.deepEqual(locatePassages(PROSE, ["The default slave address is 1; addresses"]), {
    ranges: new Map(),
    found: [],
  });
});

test("a near miss with a different value does not match", () => {
  assert.deepEqual(locatePassages(PROSE, ["The bus termination resistor must be 100 Ω."]).found, []);
});

test("very short passages are not highlighted", () => {
  assert.deepEqual(locatePassages(PROSE, ["247"]).found, []);
});

test("several passages are found independently and numbered by their position in the list", () => {
  const { found, ranges } = locatePassages(PROSE, [
    "termination resistor must be 120 Ω.",
    "not on this page at all",
    "The default slave address is 247",
  ]);
  assert.deepEqual(found, [0, 2]);
  assert.deepEqual(ranges.get(1), [{ start: 0, end: 32, passage: 2 }]);
  assert.deepEqual(ranges.get(4), [{ start: 0, end: 14, passage: 0 }]);
});

test("rendered items escape HTML and wrap only the ranges, marking the current passage", () => {
  assert.equal(
    renderItem("a <b> c", [{ start: 2, end: 5, passage: 0 }]),
    'a <mark class="evidence-mark" data-passage="0">&lt;b&gt;</mark> c',
  );
  assert.equal(
    renderItem(
      "ab cd",
      [
        { start: 0, end: 2, passage: 0 },
        { start: 3, end: 5, passage: 1 },
      ],
      1,
    ),
    '<mark class="evidence-mark" data-passage="0">ab</mark> <mark class="evidence-mark is-current" data-passage="1">cd</mark>',
  );
  assert.equal(renderItem("x & y", undefined), "x &amp; y");
});
