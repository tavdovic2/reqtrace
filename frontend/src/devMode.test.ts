import assert from "node:assert/strict";
import { test } from "node:test";

import { askBody, DEV_MODE_KEY, readDevMode, writeDevMode } from "./devMode.ts";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
  };
}

test("developer mode is off by default and remembered when switched on", () => {
  const storage = memoryStorage();
  assert.equal(readDevMode(storage), false);
  writeDevMode(storage, true);
  assert.equal(storage.getItem(DEV_MODE_KEY), "on");
  assert.equal(readDevMode(storage), true);
  writeDevMode(storage, false);
  assert.equal(readDevMode(storage), false);
});

test("developer mode is off when storage is missing or throws", () => {
  assert.equal(readDevMode(null), false);
  const broken = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  assert.equal(readDevMode(broken), false);
  assert.doesNotThrow(() => writeDevMode(broken, true));
});

test("the debug field is requested only in developer mode", () => {
  assert.deepEqual(askBody("Koja je cijena", 3, false), { question: "Koja je cijena", drawer_id: 3 });
  assert.equal("debug" in askBody("Koja je cijena", null, false), false);
  assert.deepEqual(askBody("Koja je cijena", null, true), {
    question: "Koja je cijena",
    drawer_id: null,
    debug: true,
  });
});
