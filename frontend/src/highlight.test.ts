// Run with: npm test (Node's built-in test runner; Node 24 strips the types itself).
import assert from "node:assert/strict";
import { test } from "node:test";

import { locatePassage, matchKey, renderItem } from "./highlight.ts";

const ITEMS = [
  "6.2 Modbus RTU",
  "The default slave address is 247; addresses",
  "from 1 to 247 can be config-",
  "ured. The bus termination resistor",
];

test("matching ignores whitespace, hyphenation, case and typographic quotes", () => {
  assert.equal(matchKey("Config-\r\nured “OK”"), 'configured"ok"');
});

test("a passage spanning several items is marked in each of them", () => {
  const ranges = locatePassage(ITEMS, "addresses from 1 to 247 can be configured.");
  assert.ok(ranges);
  assert.deepEqual(
    [...ranges.entries()],
    [
      [1, [34, 43]],
      [2, [0, 27]],
      [3, [0, 5]],
    ],
  );
  assert.equal(ITEMS[1].slice(34, 43), "addresses");
  assert.equal(ITEMS[2].slice(0, 27), "from 1 to 247 can be config");
  assert.equal(ITEMS[3].slice(0, 5), "ured.");
});

test("text from the backend's page layer matches despite different line breaks", () => {
  const backend = "The default slave address is 247; addresses\r\nfrom 1 to 247 can be config-\r\nured.";
  assert.ok(locatePassage(ITEMS, backend));
});

test("a passage that is not on the page marks nothing", () => {
  assert.equal(locatePassage(ITEMS, "The default slave address is 1; addresses"), null);
});

test("very short passages are not highlighted", () => {
  assert.equal(locatePassage(ITEMS, "247"), null);
});

test("rendered items escape HTML and wrap only the range", () => {
  assert.equal(renderItem("a <b> c", [2, 5]), 'a <mark class="evidence-mark">&lt;b&gt;</mark> c');
  assert.equal(renderItem("x & y", undefined), "x &amp; y");
});
