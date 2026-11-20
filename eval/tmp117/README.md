# TMP117 benchmark data

A benchmark on a real, public datasheet: the Texas Instruments TMP117 high-accuracy
digital temperature sensor.

| | |
|---|---|
| Source | <https://www.ti.com/lit/ds/symlink/tmp117.pdf> |
| Document | SNOSD82D, Revision D (June 2018, revised September 2022), 50 pages |
| SHA-256 | `78212e91e478035a91165397a8de8a90bfc776759dc445243c033537f0c4b789` |
| Size | 1,717,888 bytes |
| Downloaded | 2026-11-16 and 2026-11-20 (same file both times) |

The PDF is TI's copyrighted document and is **not** in this repository. Fetch it with:

```bash
python -m eval.fetch_samples      # downloads into samples/tmp117.pdf and verifies the hash
python -m eval.run_eval --dataset tmp117
```

TI regenerates the packaging pages at the end of its datasheets (they carry the date of
generation) without issuing a new revision. If the hash differs but the file is still
revision SNOSD82D with 50 pages, the fetcher keeps it and says so; results may then
differ slightly from the published ones.

## questions.jsonl

32 questions: 26 answerable and 6 unanswerable, 24 in English and 8 in Bosnian, marked
`easy` (first page, ratings tables) or `hard` (application and layout sections, values
with conditions, values printed on several pages or in several notations). They include
the ten questions used during development (t01–t10).

Every expected value and page was taken by reading the PDF text, never from the system's
output. `tests/test_eval_datasets.py` checks this when the PDF is present: each expected
value must be printed, with its unit, on at least one expected page, and each expected
page must print at least one expected value.

Fields: `expected_keywords` are values ("1.7 V") or short phrases; "a|b" accepts either
alternative, and all keywords must be in the answer. Values are matched by number and
unit, independent of notation (see `eval/matching.py`). `note` explains special cases,
for example t13: page 14 still states the shutdown current as 250 nA, while the
specification table, the features and the revision history give 150 nA.

## requirements.jsonl

109 hand-labelled obligations and limits, found by reading the datasheet section by
section, including sections the extractor skips (register map, packaging, legal). Each
label stores the page, the kind and a few short identifying terms or values, not the
datasheet's sentences. A row extracted from the same page matches a label when it
contains all of its terms.

The labels follow the definitions in the main README:

- **limit**: a boundary the device must be operated within: the absolute maximum ratings
  and recommended operating conditions rows (8 required labels);
- **obligation**: the designer or user is told to do or not to do something: "must",
  "should", "is recommended", "is required", or an instruction (67 required labels).

34 labels are **optional**: real requirements that the documented scope leaves open. An
extracted optional item is not counted as an error, and a missing one is not counted
against recall:

- the 14 rows of the two-wire timing table (timing tables are never limits, by design);
- the ESD ratings, the EEPROM write endurance, and the supply range restated in the
  power-supply section;
- generic I2C protocol rules ("the master must first address ..."), calculation hints
  ("Use Equation 1 ..."), conditional advice ("may require additional decoupling
  capacitors") and legal or packaging boilerplate.

Judgement calls are recorded in each label's `note`. The labels were written from the
PDF text alone and were not changed after seeing this benchmark's extraction results.
(The extractor had been run on TMP117 during earlier development, so the labeller had
seen some of its rows before.)
