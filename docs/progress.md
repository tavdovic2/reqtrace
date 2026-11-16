# Progress log

One entry per phase: what was built, which checks ran with their real results, and a
suggested commit message. Work was done on 2026-11-13 on Windows 11, Python 3.12.15,
an RTX 3050 (6 GB), Ollama 0.40.16 with `qwen3:4b`, and Qdrant v1.20.0 in Docker.

The phases were built in one pass, and some files were touched again in later phases
(for example `app/qa.py` after the evaluation and `app/requirements.py` after the UI test).
The file lists below say which phase each file belongs to; the final state is what was
tested.

## Phase 1: ingestion

**Built**
- Project skeleton: `pyproject.toml` (pinned dependencies, Ruff, pytest), `app/config.py`
  (pydantic-settings), `app/db.py` (SQLAlchemy models for drawers, documents and
  requirements), `app/errors.py`, `app/services.py`, `app/pdf.py`.
- `app/ingestion.py`: Docling parsing (OCR off), heading depth taken from numbering,
  compact Markdown tables, structure-aware chunking (per section and page, one chunk per
  table, long sections split with overlap, long tables split by rows).
- `app/embeddings.py` (FastEmbed `multilingual-e5-large` + BM25, CPU, E5 prefixes).
- `app/vector_store.py`: collection with named `dense` + `bm25` vectors (IDF modifier),
  integer payload indexes on `doc_id` and `drawer_id`, upsert and delete.
- `app/documents.py` and the routes in `app/main.py`: drawer CRUD, upload with validation
  (PDF magic bytes, size limit, pypdfium2 open), ingestion in the background, document
  list, file download, delete (row + vectors + file), cleanup of expired temporary
  documents at startup and periodically, and interrupted ingestions marked failed.
- Tests: `tests/test_chunking.py`, `tests/test_documents_api.py`, `tests/conftest.py`
  (in-memory Qdrant, hash embedder, pypdfium2-based parser, fake LLM, fpdf2 PDFs).

**Checks**
- `pytest`: all pass (21 at the end of this phase, offline).
- Real pipeline (Docling + FastEmbed + Qdrant v1.20.0 in Docker) on the generated 8-page
  datasheet: 22 chunks with correct page numbers 1–8, tables as separate chunks. Ingestion
  took 292 s on the first run, including the model downloads, and about 33 s once models
  were cached.
- Real API: upload returned `202` and the document became `ready`; Qdrant point count was
  22.
- Delete through the API was verified by tests (row, vectors and file gone). Through the
  real stack, two drawers (each with one document) were deleted with `204`. A later
  cross-check found exactly the same document ids in Qdrant, SQLite and the upload folder
  (22 points per remaining document), so the deleted documents left no trace. The same
  check showed SQLite reusing a deleted id, so drawers and documents now use
  `AUTOINCREMENT` (with a test).

**Suggested commit:** `feat: ingest PDFs into drawers with structure-aware chunking and hybrid vectors`

## Phase 2: question answering

**Built**
- `app/retrieval.py`: scope filter (drawer, documents, or everything) and search in
  `dense`, `bm25` or `hybrid` mode (two prefetches fused with `RrfQuery`), top 5.
- `app/qa.py`: numbered excerpts, a structured answer (`evidence`, `answered`, `answer`,
  `citations`), sources built only from valid cited chunks, and "not found" when the
  model abstains or cites nothing valid. An empty drawer never calls the LLM.
- `app/llm.py`: `LLMClient` interface and `OllamaClient` (JSON schema, temperature 0,
  thinking off, one retry on invalid JSON, clear errors for unreachable server or missing
  model, health check).
- `POST /ask`.
- Tests: `tests/test_qa.py` (filters, every mode, sources from cited chunks only,
  not-found paths, empty drawer, 404/422), `tests/test_llm.py` (mocked HTTP transport).

**Checks**
- `pytest`: all pass.
- Real `/ask` with `qwen3:4b`: the first schema (`answered` first) wrongly returned
  "not found" for "What is the default Modbus slave address?". After moving `evidence`
  first and making it required, the API answered "247" citing page 5, and "What is the
  warranty period?" returned `answered: false`. Bosnian questions were also answered
  correctly.

**Suggested commit:** `feat: answer questions with hybrid retrieval and cited sources`

## Phase 3: evaluation

**Built**
- `samples/` did not exist, so `eval/make_sample_pdf.py` generates
  `eval/data/nx310_datasheet.pdf` (a fictional pressure transmitter, 8 pages, tables).
- `eval/questions.jsonl`: 25 answerable questions (one in Bosnian) and 6 unanswerable
  ones. Keywords and pages are taken from the document text.
- `eval/run_eval.py`: separate collection and data directory, `--mode dense|bm25|hybrid|all`,
  `--pdf`, `--questions`. It writes `eval/results.md` (table) and `eval/results.json`
  (per-question details).
- Tests: `tests/test_eval.py` (keyword matching, metrics, table format, question set
  shape, every keyword present on its expected page).

**Checks**
- First real run: hit rate 96% in every mode, but answer accuracy only 24–28%. The
  model abstained because `evidence` had a default value, so it was not required in the
  schema and the model skipped it. After the fix, the second real run (214 s):

  | Mode | Retrieval hit rate @5 | Answer accuracy | Abstention accuracy |
  |---|---|---|---|
  | dense | 96% | 96% | 100% |
  | bm25 | 96% | 92% | 100% |
  | hybrid | 96% | 96% | 100% |

- The only retrieval miss in every mode is q19 (weight, in a long table on page 6).
- `pytest`: all pass.

**Suggested commit:** `test: add evaluation set and script comparing dense, bm25 and hybrid retrieval`

## Phase 4: requirement extraction

**Built**
- `app/verification.py`: normalisation (NFKC, hyphenation across line breaks, typographic
  quotes and dashes, table pipes, whitespace), quote verification (exact, otherwise
  rapidfuzz `partial_ratio` ≥ threshold, minimum length), and number consistency between
  the requirement text and its quote.
- `app/requirements.py`: chunk-by-chunk extraction with a JSON schema, page taken from the
  chunk, verification against the PDF's own text layer (pypdfium2), deduplication that
  never merges different numbers, `REQ-001` codes, storage, background run with progress,
  and interrupted runs marked failed at startup.
- `app/export.py` (CSV with BOM, XLSX), `POST/GET /documents/{id}/requirements`
  (`?format=csv|xlsx`).
- Tests: `tests/test_verification.py` (including a fabricated quote, a changed value, a
  quote from another page and a wrong number in the text), `tests/test_requirements.py`
  (deduplication, codes, CSV/XLSX round trip, full API flow where a fabricated quote is
  rejected, re-run replaces rows, invalid LLM output fails the run).

**Checks**
- `pytest`: all pass. A test failure caught deduplication merging "3.6 V" with "5.5 V";
  fixed.
- Real extraction with `qwen3:4b` on the test document, before the number check: 70 saved
  and 42 rejected. The saved rows included wrongly copied values ("4 to 2:0 mA",
  "RS-1485"), which led to the number check.
- After the number check (138 s): 58 saved and 54 rejected. An independent script found
  56 of the 58 quotes verbatim on their stated page; the other 2 differ by small copying
  errors (fuzzy score 98).
- Known remaining issue: some rows restate a table row wrongly without numbers (for
  example "output B" instead of "V", and pin assignments on page 8).

**Suggested commit:** `feat: extract requirements with quote verification, deduplication and CSV/XLSX export`

## Phase 5: frontend

**Built**
- `frontend/`: React 19 + Vite 8 + TypeScript 7, `react-i18next` (English default,
  Bosnian, choice saved in localStorage, Bosnian plural forms), `react-pdf` viewer loaded
  lazily, self-hosted IBM Plex fonts.
- Filing cabinet (search, drawers with label and handle, folder tabs, drop zone, rename
  and delete, temporary drawers drawn as a paper stack with the 24-hour note, new drawer
  form). Notepad with Conversation (This drawer / Whole cabinet switch, history, sources
  as highlighter tags) and Requirements (document picker, extraction progress, table with
  page chips and the VERIFIED stamp, CSV/Excel links). Cited-page viewer with previous and
  next.
- Loading, empty and error states, a health banner (API, Qdrant, Ollama, missing model),
  semantic elements, keyboard tabs, visible focus, `prefers-reduced-motion`.
- `scripts/check-i18n.mjs` (key parity), Prettier.

**Checks**
- `npm run build` (includes `tsc -b`) passes; `npm run check:i18n` reports 108 keys in
  both languages; Prettier check is clean.
- End-to-end in headless Chromium (Playwright script, not committed) against the real
  API, Qdrant and `qwen3:4b`:
  - create a drawer
  - upload the PDF and wait until it is ready
  - ask in "This drawer": the answer "247" with the source "p. 5"
  - click the source: the viewer shows "Page 5 of 8", and next goes to page 6
  - an unanswerable question shows "Not in the documents"
  - extract requirements, click a page chip, download the CSV (71 lines) and the XLSX
  - switch to Bosnian: labels, the stamp and the answer "19200 baud" are in Bosnian;
    the choice survives a reload
  - 400 px wide layout screenshot
  - no console errors
- Bugs found by this test and fixed:
  - the hidden radio inputs of the scope switch overlapped and blocked clicks
  - the pager disappeared or reset to the cited page after "next" (Suspense re-running
    effects)
  - the table columns were too narrow
  - the quiet buttons were styled as primary buttons
- Not checked with tools: colour contrast was calculated by hand for the main colour
  pairs, not measured with an automated checker, and no screen reader was used.

**Suggested commit:** `feat: add bilingual filing-cabinet web UI with cited page viewer`

## Phase 6: packaging and documentation

**Built**
- `Dockerfile` (Python 3.12.16 slim, CPU-only PyTorch, dependencies cached in their own
  layer, non-root user, health check, model caches on the data volume),
  `frontend/Dockerfile` (Node 24 build, nginx 1.30 serve), `frontend/nginx.conf` (`/api`
  proxy, upload limit, long timeouts, `.mjs` MIME type), `docker-compose.yml` (Qdrant
  v1.20.0, API, frontend; Ollama on the host through `host.docker.internal`), and
  `.dockerignore` files.
- `.github/workflows/ci.yml`: backend job (CPU PyTorch, Ruff lint and format, pytest) and
  frontend job (i18n key parity, Prettier, typecheck and build).
- `README.md` (description, real screenshot, features, Mermaid architecture, quick start,
  configuration, API, evaluation results with their caveats, design decisions,
  limitations, roadmap), `docs/architecture.md` (module map, ingestion, question and
  extraction flows), `.env.example`, `.gitignore`, `LICENSE` (MIT).

**Checks**
- `docker compose build` and `docker compose up -d`: all three containers start. The API
  becomes healthy and reaches Ollama on the host and Qdrant. The UI is served on :8080.
- In Docker: create a drawer, upload the PDF. The first upload downloaded the models into
  the volume and finished with 18 chunks (the Windows host produced 22; see the README
  limitation on heading detection). `/api/ask` through nginx answered "247" (page 5) and
  "IP67" (page 6), and declined the warranty question.
- A Playwright run against the Docker UI found the PDF viewer empty: nginx served the
  pdf.js `.mjs` worker as `application/octet-stream`. After the fix the content type is
  `text/javascript`, the viewer shows "Page 5 of 8", and there are no console errors. The
  README screenshot was taken from this run.
- Fresh-clone simulation: the 75 files git would track (from `git add -n` in a scratch copy;
  nothing was committed) were copied to an empty folder. There, Ruff lint and format
  pass, `pytest` passes 68 tests, `npm ci`, the i18n check, the Prettier check and
  `npm run build` pass, and `docker compose build` succeeds.
- Not run: GitHub Actions itself (there is no remote). `docker compose up` on a machine
  without the model caches was only exercised through this machine's empty Docker
  volume. A Linux host (where `host-gateway` matters) was not tested.

**Suggested commit:** `build: add Docker Compose packaging, CI workflow and documentation`

## Fix round 1: extraction quality, answer grounding, layout

The owner tested the build by hand on two real PDFs (a 19-page course reading and a
9-page lab assignment). Those PDFs are not in the repository; every check below uses
synthetic fixtures written for it.

**Root causes found**
- A (invented requirement passed): the extraction prompt contained the content example
  "The supply voltage of output A shall be between 9 and 32 V DC.", which the model copied.
  Verification only located the quote and never compared the requirement with it.
- B (descriptions turned into obligations): the prompt defined requirements broadly
  ("limits and ratings, allowed ranges, interface parameters") and listed nothing that
  does not count, so default-value tables, objectives and remarks qualified.
- C (one sentence, several rows): deduplication compared paraphrases only, never quotes.
- D (types): seven types without definitions, and no type for working instructions.
- E (text corruption): the colons come from the model during JSON generation. Chunk text
  and page text were clean. Re-running the old prompt reproduced them in 4 of 8 chunks
  (JSON schema) and 2 of 8 (plain JSON mode). The pipe came from Markdown tables sent to
  the model as they were. Truncated quotes and headings were stored because the model's
  transcription was stored and fragments were not checked.
- F (answers): the language instruction was generic ("the language of the question").
  The single `evidence` string made the model pick one excerpt. In a synthetic
  reproduction the FlatList chunk was retrieved (rank 2) but not cited.
- G (interface): there was no highlight at all. The layout used a centred max-width
  container with content-height columns, and the table had a fixed minimum width of
  860 px.
- H (speed): one LLM call per chunk, including chunks that cannot hold requirements, and
  long outputs (many false candidates).

**Changed**
- `app/verification.py`:
  - normalisation with an index map;
  - `locate_quote`, which returns the exact page span (fuzzy edges trimmed, cut-off
    sentences completed without crossing into a new line);
  - `requirement_support`, `modality_preserved`, `drop_foreign_colons`, `is_fragment`.
- `app/requirements.py`:
  - new prompt: placeholders only, "empty is normal", what counts and what does not,
    type definitions including `process`;
  - tables sent to the model as readable rows;
  - placeholder guard, learning-objective filter and non-requirement-table filter;
  - two rejection reasons;
  - a paraphrase with a colon-broken number falls back to the quote;
  - merging of sentence parts, one row per quote.
- `app/qa.py`, `app/language.py`: claims with their own citations, a number check against
  the question and the cited excerpts, an explicit answer language, and `highlight` per
  source.
- `app/db.py`: two rejection columns, `paraphrase` mapped to the old `text` column, and a
  migration for existing databases.
- `app/schemas.py`, `app/export.py` (quote first), `app/config.py`
  (`REQUIREMENT_SUPPORT_THRESHOLD`).
- `eval/extraction_fixtures.py`, `eval/run_extraction_eval.py`: fixtures with known
  requirements and the measurement script.
- Frontend:
  - `highlight.ts` and `highlight.test.ts`;
  - `PdfPane.tsx`: marks the evidence and scrolls to it;
  - `Requirements.tsx`: quote column first, paraphrase labelled, both rejection reasons;
  - `App.tsx`: the viewer exists only while a page is open;
  - `styles.css`: full-window layout, per-column scrolling, fixed-layout table with a
    card fallback;
  - locales.
- Tests: `test_verification.py`, `test_requirements.py` and `test_qa.py` rewritten or
  extended. 118 backend tests and 6 frontend tests.

**Acceptance checks**
1. **A document with no requirements yields zero rows.** A real `qwen3:4b` run on the
   reading fixture gives 0 rows (before: 5). Its default-value table is no longer sent to
   the model.
2. **An unrelated requirement is rejected with the new reason.** Test:
   `test_requirement_unrelated_to_its_real_quote_is_rejected_as_unsupported`, plus five
   unrelated calibration pairs in `test_verification.py`.
3. **No prompt-example text reaches the output.** Tests:
   `test_text_copied_from_a_prompt_example_never_reaches_the_table` and
   `test_prompt_shows_the_format_with_placeholders_only`.
4. **Imperative tasks are extracted.** In a real run the lab fixture gives 11 of 11
   genuine requirements. Seven of them are imperatives or statements without "shall" or
   "must".
5. **Objectives, type definitions, code lines and test inputs are not extracted.** In a
   real run the lab fixture has 0 non-requirement rows (before: 6). Unit tests cover both
   filters.
6. **One sentence gives one row.** The lab fixture has 0 split rows (before: 1). Tests:
   `test_one_sentence_with_one_obligation_yields_one_row` and
   `test_two_parts_of_one_sentence_become_one_row`.
7. **Stored quotes are exact page spans.** Tests:
   `test_stored_quote_is_exact_span_of_page_text` and
   `test_verified_requirement_is_saved_with_exact_page_quote`. On the synthetic datasheet,
   all 49 stored quotes are substrings of `extract_page_text` for their page.
8. **A Bosnian question gets a Bosnian answer.** Unit tests check that the prompt names
   the language. Real runs answered "Zadana Modbus adresa uređaja je 247" and "Zadani
   Modbus baud rate je 19200" (in development and in Docker).
9. **Layout.** Measured with Playwright at 1280×720, 1536×864 and 1920×1080, in English
   and Bosnian, with and without a cited page (12 cases):
   - no horizontal page scroll, no vertical page scroll and no horizontal table scroll;
   - the notepad and the viewer end 16 px above the window bottom;
   - the cabinet is 280 px wide; the viewer is 420, 494 or 620 px wide;
   - the table shows six columns when its area is at least 980 px wide (1536 and 1920
     without a page open) and switches to cards otherwise (1280 always, and 1536 or 1920
     with a page open);
   - evidence was marked in all six "page open" cases.
10. **All checks pass.** `pytest`: 118 passed. Ruff is clean. Frontend: the i18n check,
    Prettier, `npm test` (6 passed) and `npm run build` pass.

**Extraction measurements (real `qwen3:4b` runs on the fixtures)**

| Fixture | Version | Rows | Genuine found | Non-requirement rows | Split rows | Types correct | s/chunk |
|---|---|---|---|---|---|---|---|
| reading | before | 5 | n/a | 5 | 0 | n/a | 4.0 |
| reading | after | 0 | n/a | 0 | 0 | n/a | 0.8 |
| lab | before | 17 | 10/11 | 6 | 1 | 8/10 | 3.7 |
| lab | after | 11 | 11/11 | 0 | 0 | 10/11 | 2.7 |

"Before" used the old prompt, schema and verification on the same chunks (a scratch
script, not committed). The "after" row came out the same in each of the four runs made
during the last code changes (temperature 0).

On the synthetic datasheet, through the API:
- before: 58 saved and 54 rejected, in 138 s;
- after: 49 saved, 3 rejected as "quote not on page" and 15 as "not supported", in 103 s
  (22 chunks, 4.7 s per chunk including verification).

In Docker, where Linux Docling produces 18 chunks: 43 saved, 3 + 17 rejected, 103 s.

**Question-answering evaluation (`eval/run_eval.py`, all modes)**

| Mode | Hit rate @5 | Answer accuracy before → after | Abstention before → after |
|---|---|---|---|
| dense | 96% | 96% → 96% | 100% → 100% |
| bm25 | 96% | 92% → 96% | 100% → 100% |
| hybrid | 96% | 96% → 96% | 100% → 100% |

The first run after the change scored 92% in every mode. The new number check had
dropped the claim "the NX-310 is 36 V DC" because "310" came from the question, not from
the excerpt. Numbers from the question are now allowed, with a regression test.

One thing got worse: answering takes longer, 387 s instead of 214 s for the three modes,
because the model now writes evidence per excerpt and claims with citations.

**Not verified**
- The owner's two real PDFs, by design: they stay out of the repository. The FlatList
  investigation used a synthetic reproduction, not the original document.
- The pasted "Week 6 Reading" tasks were run once locally in a scratch folder (not
  committed): 5 rows for 5 tasks. "Each chip must be at least 44 high" was rejected
  because the paraphrase used synonyms. That is the known cost of the lexical support
  check.
- Highlighting on scanned or unusually encoded PDFs, where the pdf.js and pypdfium2 text
  layers may differ by more than whitespace and hyphens.
- GitHub Actions (there is no remote).

**Suggested commit:** `fix: verify requirements against their quotes, ground answers per claim and use the full window`

## Fix round 2: obligations and limits only, section skipping, speed

The owner tested on the lab assignment and on the public TI TMP117 datasheet (50 pages).
For local measurement TMP117 was downloaded to `samples/tmp117.pdf`; `samples/*.pdf` is
git-ignored and no text from it is in tests or docs beyond short row labels.

**Root causes**
- A (341 "requirements" on TMP117):
  - The model decided what a requirement is, with a broad definition, and was asked about
    every chunk (187 calls), including features, register maps and legal pages.
  - Docling reported table captions, notes and page headers as level-1 headings. That
    destroyed the section path, so even a heading-based filter could not have found
    "Register Maps".
- B (invented "must"): only "should becomes must" was checked. A paraphrase adding "must"
  to a plain statement passed.
- C (lab):
  - The test table header was not recognised in some layouts, and troubleshooting tables
    were not handled.
  - Genuine tasks were rejected when their paraphrase used synonyms (the round-1 support
    check rejected the whole row).
  - "require" inside `require('./trails.json')` counted as obligation wording.
- D (speed): measured on TMP117 with the round-1 code (local rerun):
  - 187 calls, 1193 s, 0 thinking characters;
  - 135k prompt tokens and 52k output tokens, about 23 ms per output token.

  Time is generation, so it scaled with the number of calls and the amount of output.
  The model was 100% on the GPU.
- E:
  - Answer claims were joined with spaces.
  - Exported quotes kept the page's own line breaks, by design for matching.
  - Highlight marks were computed only when pdf.js loaded a page's text, so a second
    chip on an already open page showed nothing. On the round-1 Docker build: first chip
    2 marks, second chip on the same page 0.

**Changed**
- `app/extraction_scope.py` (new):
  - the definition of the two kinds;
  - deterministic classification (obligation wording, imperatives, limits in ratings
    sections or next to boundary words);
  - code detection;
  - the configurable section skip list;
  - non-requirement table shapes (test, change log, defaults, bit fields, register map,
    troubleshooting);
  - ratings-row parsing and type from the unit;
  - the invented-obligation check.
- `app/requirements.py`:
  - planning: skip, read ratings tables directly, group the rest per top-level section;
  - new prompt; optional paraphrase (`EXTRACTION_PARAPHRASE`, off by default);
  - quotes located per group page, re-matched without the prompt's row format;
  - table quotes widened to whole lines;
  - an untrustworthy paraphrase is removed, not the row;
  - optional concurrency;
  - start and finish timestamps, skipped count, removed-paraphrase count.
- `app/ingestion.py`: an unnumbered heading under a numbered one becomes its child.
- `app/llm.py`: per-call statistics (prompt and output tokens, seconds, thinking
  characters), logged and kept for the evaluation.
- `app/qa.py`, `app/schemas.py`: `lines` (one per claim, with source indexes); claims
  repeating the same opening are merged.
- `app/db.py` (columns `kind`, skipped count, removed paraphrases, start and finish times,
  with migration), `app/config.py` (four settings), `app/export.py` (Kind column),
  `app/verification.py` (`display_quote`).
- `eval/extraction_fixtures.py`:
  - a new datasheet fixture with labelled kinds;
  - the lab fixture has two more imperative tasks and a troubleshooting table;
  - "Each row shows ..." relabelled as a description under the new definition.
- `eval/run_extraction_eval.py`: precision and recall per kind; `--pdf` for unlabelled
  documents with tokens and time; `--paraphrase`, `--concurrency`, `--ollama-url`.
- Frontend:
  - Kind / type column, with the paraphrase column hidden when empty;
  - summary with quote-not-found, not-an-obligation-or-limit, sections skipped and time;
  - elapsed and remaining time during extraction;
  - answer lines with their own source chips;
  - highlight recomputed from the cached page text.
- `docker-compose.yml`: `FRONTEND_PORT` (8080 by default). Ports 8080 and 8081 were taken
  by other servers on the owner's machine during this round.
- Tests: `tests/test_extraction_scope.py` (new), `tests/test_requirements.py` (rewritten),
  plus tests in `test_chunking.py` and `test_qa.py`. 204 backend tests and 6 frontend
  tests.

**Acceptance checks**
1. **Nothing from out-of-scope sections on the datasheet fixture.** No row comes from the
   feature list, overview, revision history, register tables, troubleshooting table or
   legal notice. Precision is 1.00 for both kinds, and all of those sections and tables
   are skipped before any model call (`test_plan_skips_sections_and_tables_and_groups_by_top_section`,
   real run).
2. **Ratings rows are limits.** Rows of the ratings and operating-conditions tables are
   stored as `limit` with values and units: 5 of 6 on the fixture and 8 of 8 on TMP117.
   The fixture miss is a two-line cell that Docling reads in the wrong word order.
3. **Imperatives and recommendations are obligations.** Obligation recall is 1.00 on the
   datasheet fixture (6/6, including "Avoid ...", "Do not ...", "To prevent oxidation,
   cover ..." and "... is recommended ...") and on the lab (12/12).
4. **Descriptions never extracted; no invented must/shall.** Tests:
   `test_descriptive_sentence_is_never_extracted` and
   `test_paraphrase_that_invents_obligation_is_removed_and_row_kept`, plus the
   classification tests.
5. **Genuine item with an unsupported paraphrase is kept with the quote only.** Test:
   `test_genuine_item_with_unsupported_paraphrase_is_kept_with_quote_only`.
6. **Test inputs and troubleshooting rows are not extracted; tasks still are.** The lab
   fixture has 0 false positives with 12/12 tasks found (real run), and table-shape tests
   cover both table kinds.
7. **Multi-claim answers render one claim per line.** API test (`lines`) and a real UI
   run on TMP117: the Bosnian accuracy question rendered 2 lines, each with its own chips;
   the repeated "Tačnost mjerenja je ..." claims were merged into one line.
8. **Exported quotes have no raw line breaks.** Test
   `test_stored_quotes_are_shown_and_exported_on_one_line`; in Docker, 0 of 49 TMP117
   quotes contain a newline.
9. **All checks pass.** `pytest` 204 passed, Ruff clean, frontend i18n, Prettier,
   `npm test` (6) and build pass.

**Fixture results (real `qwen3:4b` runs)**

| Fixture | Paraphrase | Rows | P oblig. | R oblig. | P limit | R limit | s/call |
|---|---|---|---|---|---|---|---|
| reading | off | 0 | n/a | n/a | n/a | n/a | 1.9 |
| lab | off | 12 | 1.00 | 1.00 | n/a | n/a | 2.4 |
| datasheet | off | 11 | 1.00 | 1.00 | 1.00 | 0.83 | 4.2 |
| reading | on | 0 | n/a | n/a | n/a | n/a | 2.9 |
| lab | on | 12 | 1.00 | 1.00 | n/a | n/a | 3.7 |
| datasheet | on | 11 | 1.00 | 1.00 | 1.00 | 0.83 | 10.1 |

The "on" rows come from the run before ratings tables were read directly. The quality
was the same and only the speed differed, which is what the comparison was for.

**TMP117 (local, Windows, RTX 3050, model 3.9 GB, 100% GPU)**

| | Round 1 (rerun) | Round 2, paraphrase off | Round 2, paraphrase on |
|---|---|---|---|
| Chunks / model calls | 187 / 187 | 27 / 9 (+2 ratings tables read directly) | 27 / 9 |
| Sections and tables skipped | 0 | 78 | 78 |
| Rows saved | 334 | 50 (40 obligations, 10 limits) | 53 (42 / 11) |
| Rejected: quote not on page / other | 160 / 96 | 1 / 33 | 1 / 35 |
| Output tokens | 51,964 | 3,447 | 5,776 |
| Extraction time | 1,193 s | 89 s | 142 s |

- **Through the API in development:** 50 rows, 1 min 32 s.
- **In Docker (Linux Docling):** 49 rows (39 obligations, 10 limits), 77 skipped, 9
  calls, 94 s.
- **Parallel test** (separate Ollama server on port 11435 with `OLLAMA_NUM_PARALLEL=2`):
  the model used 5.4 GB with 21% on the CPU. Two concurrent calls took 101 s and one call
  141 s on that server, against 89 s on the normal server. Not kept.

**Question answering:** `eval/run_eval.py` after the heading change gave 96% / 96% / 96%
answer accuracy and 100% abstention in all modes, unchanged. Its duration (465 s) is not
comparable because TMP117 was being ingested at the same time.

**Not verified**
- The owner's lab PDF (by design). Lab behaviour was checked only on the synthetic lab
  fixture.
- Recall on TMP117: there is no ground truth. The rows were read by hand. They are the
  eight ratings rows, the pin-table instructions and the application, power-supply and
  layout rules. Remaining doubtful rows include "The recommended PCB layout minimizes
  ..." and the bus-timing row "tBUF ... 1300 ns", which counts as a limit because of the
  word "between".
- Highlighting was checked on TMP117 for a ratings row (5 marks), a long multi-line quote
  (8 marks), a pin-table row (6 marks) and the same-page second chip. It was not checked
  on scanned or unusually encoded PDFs.
- GitHub Actions (no remote).

**Suggested commit:** `feat: extract only obligations and limits, skip out-of-scope sections and cut extraction time 13x`

## Fix round 3: refused answers, debug details, imperatives, ingestion

**Refused answerable questions on TMP117 (local runs with `debug`).**
- "Koliko uređaja može biti na jednoj I2C sabirnici?": the passage with "up to four"
  devices (page 21) was at rank 18 for the Bosnian question, so the model never saw it.
  The number check was not the cause. Fixed by searching an English phrasing together
  with the original; now ranks 2 and 8, answer "4", page 21.
- "What value is recommended for the supply bypass capacitor?": retrieval was fine
  (pages 33, 35, 36). The model wrote citation markers into the statement and the
  marker numbers failed the number check. Fixed by stripping markers; answer "100 nF or
  0.1 μF". The µ/μ and hyphen cases were already normalised; tests now cover them.
- "How much current does the device typically consume?": the first five chunks came
  from two pages, and the typical standby current was at rank 7. Fixed with 8 chunks,
  at most 2 per page, and a prompt line asking for each value with its condition.
- Pullup question (found while re-running): the claim "5 kΩ" cited no excerpt. A claim
  with numbers now gets the evidence passages containing all of them.

**Checks**
- `pytest`: 235 passed. `ruff format` / `ruff check`: clean. Frontend: i18n check (127
  keys), Prettier, 6 tests and the build pass.
- QA evaluation (synthetic datasheet, 30 answerable incl. 5 new cross-language and
  notation questions, 7 unanswerable incl. 1 new):

  | | dense | bm25 | hybrid |
  |---|---|---|---|
  | Answer accuracy before | 93% | 90% | 93% |
  | Answer accuracy after | 97% | 97% | 97% |
  | Abstention before / after | 100% / 100% | 100% / 100% | 100% / 100% |

  Before, q28 (Bosnian, "četiri" transmitters) was refused in all modes and q29 in BM25.
  After, the only miss is the weight question (see README).
- Extraction fixtures: lab 15 rows, precision and recall 1.00 (the "empty state says
  so" sentence stays out; the first run kept the lead-in "Check your work with the
  following steps.", which led to the lead-in rule); datasheet unchanged (1.00 / 0.92);
  reading 0 rows.
- The ten TMP117 questions (development, final code): 1–8 answered with correct pages
  (1.7 V to 5.5 V; ±0.1 °C; 0.0078 °C; 250 nA shutdown, 135 µA active, 1.25 µA standby,
  3.5 µA; 4; 5 kΩ; 100 nF or 0.1 μF; 6 V), 9 (price) and 10 (SPI) refused.
- Ingestion of TMP117 with models already loaded:
  - development (Windows): parse 221 / 230 s, embed 238 / 205 s (187 chunks), upsert
    0.3 / 0.4 s, total 460 / 436 s (two runs); model warm-up 18.6 s;
  - Docker after a rebuild: parse 223 s, embed 226 s (180 chunks), upsert 0.6 s, total
    450 s; warm-up at startup 22.1 s.
- Docker after `docker compose build`: two uploads of the synthetic datasheet (30.5 s
  and 26.2 s) downloaded no model. Docling only checks the model revision on
  huggingface.co (two metadata requests) and loads the snapshot from `/data/models`.
- TMP117 extraction in Docker after the timing change: 48 rows (39 obligations, 9
  limits), 77 skipped, 100 s; the bus-timing row "tBUF" is no longer a limit.

**Not verified**
- The "Why?" panel and the page progress were built and type-checked, but not looked
  at in a browser.
- Ingestion progress on a slow upload was checked through the API (`ingest_pages_done`
  reached 8/8 and 50/50), not watched live in the UI.
- Bosnian questions beyond the tested ones; the translation step uses the same 4B model.
- GitHub Actions (no remote).

**Suggested commit:** `fix: answer cross-language and notation questions, explain refusals, recognise introductory imperatives, show ingestion progress`
