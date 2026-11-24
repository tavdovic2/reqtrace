import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyPatch,
  countRows,
  filterRows,
  isTypingTarget,
  moveIndex,
  restoreRows,
  reviewKeyAction,
  type Reviewable,
} from "./review.ts";

function row(id: number, status: Reviewable["status"], notFound = false): Reviewable {
  return { id, status, not_found: notFound, edited_text: null, reviewer_note: null, reviewed_at: null };
}

const ROWS = [row(1, "proposed"), row(2, "accepted"), row(3, "rejected", true), row(4, "accepted", true)];

test("the status filter shows the rows of one status, or the rows no longer found", () => {
  assert.deepEqual(
    filterRows(ROWS, "all").map((r) => r.id),
    [1, 2, 3, 4],
  );
  assert.deepEqual(
    filterRows(ROWS, "proposed").map((r) => r.id),
    [1],
  );
  assert.deepEqual(
    filterRows(ROWS, "accepted").map((r) => r.id),
    [2, 4],
  );
  assert.deepEqual(
    filterRows(ROWS, "rejected").map((r) => r.id),
    [3],
  );
  assert.deepEqual(
    filterRows(ROWS, "not_found").map((r) => r.id),
    [3, 4],
  );
});

test("counts cover every filter; rows no longer found also count under their status", () => {
  assert.deepEqual(countRows(ROWS), { all: 4, proposed: 1, accepted: 2, rejected: 1, not_found: 2 });
  assert.deepEqual(countRows([]), { all: 0, proposed: 0, accepted: 0, rejected: 0, not_found: 0 });
});

test("an optimistic status change sets the review time, and reset clears it", () => {
  const now = new Date("2026-11-24T10:00:00Z");
  const accepted = applyPatch(ROWS, [1], { status: "accepted" }, now);
  assert.equal(accepted[0].status, "accepted");
  assert.equal(accepted[0].reviewed_at, "2026-11-24T10:00:00.000Z");
  assert.equal(accepted[1], ROWS[1], "other rows are untouched");
  const reset = applyPatch(accepted, [1], { status: "proposed" }, now);
  assert.equal(reset[0].reviewed_at, null);
});

test("an optimistic edit and note change only those fields; a blank note is removed", () => {
  const now = new Date();
  const [edited] = applyPatch([row(1, "proposed")], [1], { edited_text: "New text", reviewer_note: "  " }, now);
  assert.equal(edited.edited_text, "New text");
  assert.equal(edited.reviewer_note, null);
  assert.equal(edited.status, "proposed");
  assert.equal(edited.reviewed_at, null);
});

test("a rollback restores only the rows of the failed request", () => {
  const now = new Date();
  const first = applyPatch(ROWS, [1], { status: "rejected" }, now);
  const second = applyPatch(first, [2], { status: "rejected" }, now);
  const rolledBack = restoreRows(second, [ROWS[1]]);
  assert.equal(rolledBack[0].status, "rejected", "the other change stays");
  assert.equal(rolledBack[1].status, "accepted", "the failed change is undone");
});

test("review shortcuts map to actions", () => {
  const on = (key: string) => reviewKeyAction({ key, target: { tagName: "TR" } });
  assert.deepEqual(on("ArrowDown"), { type: "move", delta: 1 });
  assert.deepEqual(on("j"), { type: "move", delta: 1 });
  assert.deepEqual(on("ArrowUp"), { type: "move", delta: -1 });
  assert.deepEqual(on("K"), { type: "move", delta: -1 });
  assert.deepEqual(on("a"), { type: "status", status: "accepted" });
  assert.deepEqual(on("R"), { type: "status", status: "rejected" });
  assert.deepEqual(on("u"), { type: "status", status: "proposed" });
  assert.deepEqual(on("e"), { type: "edit" });
  assert.deepEqual(on("n"), { type: "note" });
  assert.deepEqual(on(" "), { type: "select" });
  assert.deepEqual(on("?"), { type: "help" });
  assert.deepEqual(on("Escape"), { type: "cancel" });
  assert.equal(on("x"), null);
});

test("no shortcuts fire while typing in a field, except Escape to cancel", () => {
  for (const target of [
    { tagName: "TEXTAREA" },
    { tagName: "INPUT", type: "text" },
    { tagName: "INPUT" },
    { tagName: "SELECT" },
    { tagName: "DIV", isContentEditable: true },
  ]) {
    for (const key of ["a", "r", "u", "e", "n", "j", "k", "ArrowDown", " ", "?"]) {
      assert.equal(reviewKeyAction({ key, target }), null, `${key} in ${target.tagName}`);
    }
    assert.deepEqual(reviewKeyAction({ key: "Escape", target }), { type: "cancel" });
  }
});

test("browser shortcuts and native Space on buttons and checkboxes stay intact", () => {
  assert.equal(reviewKeyAction({ key: "a", ctrlKey: true, target: { tagName: "TR" } }), null);
  assert.equal(reviewKeyAction({ key: "r", metaKey: true, target: { tagName: "TR" } }), null);
  assert.equal(reviewKeyAction({ key: " ", target: { tagName: "BUTTON" } }), null);
  assert.equal(reviewKeyAction({ key: " ", target: { tagName: "INPUT", type: "checkbox" } }), null);
  assert.deepEqual(reviewKeyAction({ key: "a", target: { tagName: "INPUT", type: "checkbox" } }), {
    type: "status",
    status: "accepted",
  });
});

test("typing targets are recognised", () => {
  assert.equal(isTypingTarget(null), false);
  assert.equal(isTypingTarget({ tagName: "INPUT", type: "checkbox" }), false);
  assert.equal(isTypingTarget({ tagName: "input", type: "search" }), true);
  assert.equal(isTypingTarget({ tagName: "BUTTON" }), false);
});

test("moving stays inside the list and starts at the edge when nothing is focused", () => {
  assert.equal(moveIndex(-1, 1, 3), 0);
  assert.equal(moveIndex(-1, -1, 3), 2);
  assert.equal(moveIndex(0, -1, 3), 0);
  assert.equal(moveIndex(2, 1, 3), 2);
  assert.equal(moveIndex(1, 1, 3), 2);
  assert.equal(moveIndex(0, 1, 0), -1);
});
