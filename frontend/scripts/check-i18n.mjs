// Fails when en.json and bs.json do not define the same keys (ignoring plural suffixes,
// since Bosnian needs a "few" form that English does not).
import { readFileSync } from "node:fs";

const load = (name) => JSON.parse(readFileSync(new URL(`../src/locales/${name}.json`, import.meta.url)));
const PLURAL = /_(zero|one|two|few|many|other)$/;

function keys(object, prefix = "") {
  return Object.entries(object).flatMap(([key, value]) =>
    typeof value === "object" ? keys(value, `${prefix}${key}.`) : [`${prefix}${key}`.replace(PLURAL, "")],
  );
}

const en = new Set(keys(load("en")));
const bs = new Set(keys(load("bs")));
const missingInBs = [...en].filter((k) => !bs.has(k));
const missingInEn = [...bs].filter((k) => !en.has(k));

if (missingInBs.length || missingInEn.length) {
  console.error("Missing in bs.json:", missingInBs);
  console.error("Missing in en.json:", missingInEn);
  process.exit(1);
}
console.log(`i18n OK: ${en.size} keys in both languages`);
