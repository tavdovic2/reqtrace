# Design decisions

Decisions made while building reqTrace, especially where the original brief was unclear,
outdated or turned out not to work as written. Library facts were checked on 2026-11-13
against current documentation and the installed packages.

## Environment and library versions

- **Versions pinned.** Python 3.12, FastAPI 0.162.5, Pydantic 2.14.3, SQLAlchemy 2.1.8,
  qdrant-client 1.20.0 with the `qdrant/qdrant:v1.20.0` image, FastEmbed 0.9.1,
  Docling 2.150.0. These were the latest releases on the build date.
- **LLM model tag.** `qwen3:4b` exists in the Ollama library (2.5 GB, Q4_K_M) and was
  pulled and tested. `qwen3:8b` also exists (5.2 GB) and is documented as the optional
  higher-quality model. The newer `qwen3.5` family (for example `qwen3.5:4b`, 3.3 GB) is
  also available; the default stays `qwen3:4b` as the brief asked, and the README mentions
  `qwen3.5:4b` as an alternative worth evaluating.
- **Hybrid search syntax.** Current Qdrant docs express RRF as
  `query=models.RrfQuery(rrf=models.Rrf())`. `FusionQuery(fusion=Fusion.RRF)` still works,
  but the documented form is used.
- **Dense model.** FastEmbed supports `intfloat/multilingual-e5-large` (1024 dimensions,
  about 2.2 GB, 512-token limit), so it is the default. FastEmbed does **not** add the
  `query: ` / `passage: ` prefixes E5 models expect, so reqTrace adds them. They are
  settings so that a model without prefixes can be used by setting them to empty strings.
- **BM25 queries** are embedded with `query_embed`, which weights every query term
  equally, as FastEmbed recommends. Documents use `embed`.

## Missing test document

- **`samples/` did not exist.** Following the brief, the evaluation tooling was built
  against a generated document: `eval/make_sample_pdf.py` writes an 8-page datasheet for
  a fictional pressure transmitter (`eval/data/nx310_datasheet.pdf`), with sections,
  tables and "shall" statements. The questions in `eval/questions.jsonl` were written
  from that document's text. Its evaluation numbers are real runs, but they measure a
  clean, generated PDF. A run on a real datasheet is left to the owner (see README).

## Ingestion

- **OCR is off.** OCR is out of scope, so Docling runs with `do_ocr=False`. A PDF that
  produces no text is marked `failed` with a message saying it is probably scanned,
  instead of being indexed empty.
- **A chunk never spans pages.** Text under one heading is merged per page, so every
  chunk has exactly one page and citations are exact. Long sections are split with an
  overlap; tables are their own chunks with the heading path prepended, and long tables
  are split by rows with the header repeated.
- **Heading levels from numbering.** Docling reported every heading in the test PDF as
  level 1, which flattened the section path. Numbered headings ("6.2 Modbus RTU") now get
  their depth from the number; other headings keep Docling's level.
- **Table padding removed.** Docling pads Markdown table columns with spaces. The padding
  is stripped because it only costs tokens in an 8192-token context.
- **Ingestion runs in the background.** Upload returns `202` with status `processing`
  and the client polls the document list. Parsing an 8-page PDF on the CPU took about
  30 s once models were cached, which is too long for a blocking request. Documents left
  in `processing` by a crash are marked failed at the next start.
- **Extra payload field.** Besides the fields in the brief, each point stores
  `chunk_index` so a document's chunks can be read back in order (used by extraction).
- **Integer ids, never reused.** SQLite ids are integers, so the `doc_id` and `drawer_id`
  payload indexes are `INTEGER` indexes. Plain SQLite reuses the highest deleted id, and
  a real test run showed a new document getting the id of a deleted one. Drawers and
  documents use `AUTOINCREMENT`, so vectors or files left over from a failed delete can
  never attach to a new document. An existing `data/reqtrace.db` created before this
  change keeps the old behaviour until it is recreated.

## Question answering

- **Structured output for answers too.** The brief asked for structured output for
  extraction. Answers also use a JSON schema (`evidence`, `answered`, `answer`,
  `citations`) because it makes the citations and the "not found" decision explicit.
  The free-text `generate` method stays on the `LLMClient` interface as the brief
  specified, but the application does not call it yet.
- **Field order matters.** With `answered` as the first field, `qwen3:4b` abstained on
  questions it could answer: it had to decide before reading anything back. Allowing an
  empty `evidence` made it return empty strings for everything. The schema that worked
  asks for the most relevant evidence first, then the decision, then the answer.
- **No citation, no answer.** An answer is shown only if it cites at least one of the
  retrieved chunks. Citation numbers outside the list are ignored. An uncited answer is
  replaced by the "not found" message, because it cannot be checked.
- **Thinking off.** Thinking is off for every call by default (`LLM_THINK=false`). It
  adds latency and tokens on a 6 GB GPU, and structured calls always disable it.

## Requirement extraction

- **The page comes from the chunk, not the model.** Chunks never span pages, so the page
  is known exactly. Asking a 4B model to repeat it would only add a way to be wrong.
- **Quotes are checked against the PDF itself.** Verification reads the page's text
  layer with pypdfium2 rather than reqTrace's own chunks, so a requirement is checked
  against the original document, not against our parsing of it. Very short quotes
  (under `QUOTE_MIN_CHARS`) are rejected because they would match almost any page.
- **Deduplication never merges different numbers.** Fuzzy matching alone treated "shall
  not exceed 3.6 V" and "shall not exceed 5.5 V" as duplicates. Two texts are now merged
  only if they contain exactly the same numbers and are similar above `DEDUP_THRESHOLD`.
- **Extraction runs in the background** with progress (`done_chunks` / `total_chunks`)
  because it makes one LLM call per chunk. This added extraction status columns to the
  `documents` table, which the brief's data model did not have.
- **`verified` is always true for saved rows.** Requirements whose quotes fail are not
  saved, as the brief requires. The column is kept so the table and export say so
  explicitly. The count of rejected requirements is stored on the document.
- **Numbers in the restated text must appear in the quote.** The first real extraction run
  had requirements with verified quotes but wrongly copied values in the model's own
  wording ("4 to 2:0 mA", "RS-1485"). Such candidates are now rejected and counted with
  the quote failures. Wrong readings without numbers (a wrong variant name) can still pass;
  the README lists this as a limitation.
- **One invalid LLM reply stops the run.** After one retry, invalid JSON fails the whole
  extraction with a clear message, as the brief asks, instead of silently skipping a
  chunk and producing an incomplete table.

## Evaluation

- **Keywords match whole words.** "5" must not match "25", "0.1" must not match "0.15"
  and "no" must not match "not". An answer counts as correct only if every keyword (or
  one of its `a|b` alternatives) appears.
- **Several expected pages per question.** A fact can appear on more than one page (the
  accuracy is on the overview page and in the performance table), so a question lists
  every page where it is stated.
- **Separate index.** The evaluation uses its own Qdrant collection (`reqtrace_eval`) and
  data directory (`eval/.work/`), so it never touches the user's drawers.
- **No tuning to the test set.** After the evaluation, the only change was the answer
  schema fix described above, which is a general bug. The retrieval miss on the product
  weight was left as it is.
- **One keyword list changed after seeing a model answer.** For q17 ("Is the termination
  resistor built in?") the keyword was first `extern`. A debug run answered a correct "No",
  so the keywords became `no|not|external|externally`. The document states the resistor
  "is not built in and must be fitted externally", so every alternative is taken from the
  document. The change was still prompted by system output, so it is recorded here for
  review.

## Frontend

- **Versions.** React 19.3, Vite 8.3, TypeScript 7.0, react-pdf 11, react-i18next 18,
  i18next 27. Fonts come from `@fontsource` packages, so the UI makes no requests to
  external font servers.
- **Prettier added.** The frontend is formatted with Prettier (pinned) the same way Ruff
  formats the backend. CI checks formatting, the i18n key parity between `en.json` and
  `bs.json`, the TypeScript types and the production build.
- **The question scope switch has two positions,** "This drawer" and "Whole cabinet", as
  specified. Choosing individual documents for a question is available in the API
  (`doc_ids`) but not in the UI yet.
- **The PDF viewer is loaded lazily.** pdf.js is about 630 kB, so the viewer chunk loads
  only when needed and the main bundle stays around 300 kB.
- **The page is set during render, not in an effect.** react-pdf suspends while a page
  loads, and React re-runs effects when a suspended tree is shown again. An effect that
  set the cited page reset the viewer to that page after every "next".
- **The "not found" text is translated in the UI.** The API returns an English sentence
  with `answered: false`. The UI shows its own translated label so a Bosnian UI never
  shows English.

## Packaging

- **Pinned images.** `python:3.12.16-slim-bookworm`, `node:24.22.3-alpine`,
  `nginx:1.30.6-alpine` and `qdrant/qdrant:v1.20.0`.
- **CPU-only PyTorch in Docker and CI.** Docling depends on PyTorch. Installing the CPU
  wheel from the PyTorch index first keeps the CUDA build (several GB) out of the image.
- **Models are downloaded on the first upload, not at build time.** The Docling and
  FastEmbed models (about 2.5 GB) are cached on the `reqtrace_data` volume. This keeps the
  image small and survives rebuilds, but the first upload needs internet and takes
  several minutes. The README says so.
- **`.mjs` MIME type in nginx.** nginx's default MIME table has no `.mjs` entry, so the
  pdf.js worker was served as `application/octet-stream` and the viewer stayed empty in
  the Docker build only. The asset location now maps `.mjs` to `text/javascript`.
- **nginx serves the UI and proxies `/api`.** The browser sees one origin, so no CORS
  setup is needed in Docker. Proxy timeouts are 600 s because a cold model can be slow.
  The upload limit is above `MAX_UPLOAD_MB`.

## Code structure

- **Modules beyond the brief's list.** `documents.py` (drawer and document operations),
  `services.py` (wiring), `errors.py` (domain errors mapped to HTTP codes), `pdf.py`
  (pypdfium2 access) and `export.py` (CSV/XLSX) keep route handlers free of logic.

## Improving extraction quality, answers and layout

- **No content examples in the extraction prompt.** The prompt contained the example
  "The supply voltage of output A shall be between 9 and 32 V DC.", and the model copied
  it into results for a document that never mentions voltage. The prompt now shows the
  output format with angle-bracket placeholders only, and any result containing a
  placeholder is rejected. A test checks that the format line holds nothing but
  placeholders and that the prompt has no "e.g." or "for example".
- **A requirement must be supported by its quote.** Finding the quote on the page proves
  the quote is real, not that the requirement says the same thing. Every significant term
  of the paraphrase must occur in the quote (simple suffix stemming, prefix match of four
  or more letters). Articles, prepositions, modal verbs, generic subjects ("system",
  "application", "user") and generic requirement verbs ("allow", "provide", "ensure") are
  exempt. Numbers and code identifiers are strict: one missing fails the row.
  `REQUIREMENT_SUPPORT_THRESHOLD` (default 0.75) was chosen on labelled pairs kept in
  `tests/test_verification.py`. Faithful paraphrases scored 0.83 to 1.0 (1.0 once
  "between", "least" and "most" were exempted). Unrelated or invented requirements scored
  0.0 to 0.5 or failed a strict term. 0.75 sits between the two groups with margin.
- **Two rejection reasons.** "Quote not on page" and "requirement not supported by quote"
  are stored and shown separately. The second also covers placeholders, fragments
  (headings, cut-off phrases, text ending in a colon) and strengthened modality ("should"
  turned into "must"). The old `requirements_rejected` column stays as the total.
- **The stored quote is the exact page span.** `locate_quote` normalises with a
  character map back to the original text and stores `page_text[start:end]`. Model
  transcription errors therefore never reach the table. A quote cut off mid-sentence is
  completed to the end of the sentence if it ends within 80 characters, without crossing
  into a line that starts with a capital letter or digit (so a heading is not merged with
  the paragraph below).
- **Quote first, paraphrase second.** The API field `text` became `paraphrase`; the
  database column keeps its old name `text` so existing databases still work. Exports
  are ordered ID, Quote (verbatim), Page, Type, Paraphrase (model), Verified.
- **Empty is the expected result.** The prompt now says so, lists what counts (including
  imperative task sentences) and what does not, and defines every type. There is no
  keyword filter on "shall" or "must".
- **Two deterministic filters.** Learning objectives ("students should be able to ...", or
  a section heading containing "objective" or "learning outcome") are dropped: the
  model kept returning them despite the prompt. They are not counted as rejections,
  because they are not verification failures. Three kinds of table are recognised by
  their header and not sent to the model at all:
  - test tables (an "Input" column and an "Expected ..." column);
  - change logs ("Revision" or "Version" with "Date" or "Change");
  - default-value tables (a "Default" column).

  On the synthetic datasheet the revision history had turned into "Modbus RTU variant
  must be added". The fixtures confirm that no filter dropped a genuine requirement.
- **Fuzzy matches on table text are trimmed.** On table text the alignment window spilled
  into neighbouring rows ("Meaning A Output 4 to 20 mA ... HART 7 V"). Edge words the
  quote does not contain are dropped. The span also starts at one of the quote's first two
  words and ends at one of its last two, when those are present.
- **Parts of one sentence are joined.** The model sometimes split one sentence into two
  items with adjacent quotes. These are joined when nothing but spaces, commas, colons or
  dashes separates them, and the second part does not start a new line with a capital
  letter or digit. A semicolon does not join: in a feature list it separates separate
  obligations.
- **One row per quote.** Candidates whose quote spans overlap by at least 80 percent on
  the same page are merged, keeping the longer quote. The existing near-duplicate rule
  for paraphrases (never across different numbers) still applies.
- **New type `process`** for instructions about how the work is done (version control,
  testing, submission).
- **Stray colons come from the model, not from parsing.** Chunk text (Docling) and page
  text (pypdfium2) for the same passages contained no stray colons; the stored
  requirements did ("2:0 mA", "50:0 ms", "°:C"). Re-running the old extraction prompt on
  the same chunks reproduced them in 4 of 8 chunks with the JSON schema and 2 of 8 with
  Ollama's plain JSON mode, in text chunks as well as tables. Generation without any
  format did not finish within 15 minutes, so that comparison was not possible. The fix
  is at the boundary: the quote comes from the page, colons that the quote does not
  contain are removed from the paraphrase, and a paraphrase with a number broken by a
  colon is replaced by the quote, because the model also changes the digits around the
  colon ("100" became "10:00"). Without this, the genuine requirement "The list must update within 100 ms"
  was rejected for an unsupported number.
- **The support check is lexical, and that has a price.** It rejects faithful paraphrases
  that use synonyms ("minimum height" for "at least 44 high"). The prompt asks the model
  to repeat a clear quote unchanged, which reduced but did not remove such rejections.
  Loosening the check would let invented requirements with real quotes through again,
  which was the most serious problem, so the stricter behaviour was kept.
- **Tables reach the model as readable rows.** Markdown tables are rewritten as
  "Columns: a; b; c" and "- x; y; z" in the extraction prompt, so the model never sees
  pipes. Pipes that still appear in a paraphrase are replaced by commas.
- **Answers are claims with their own citations.** The old schema had a single evidence
  string, so the model picked one excerpt. In a synthetic reproduction of the
  FlatList/SectionList question, the FlatList chunk was retrieved (rank 2) but not cited.
  The schema now has evidence per excerpt, `answered`, and a list of claims, each with the
  excerpts that state it. Claims with no valid citation, or with a number that neither
  the question nor the cited excerpts contain, are dropped. Sources are exactly the
  excerpts cited by the remaining claims.
- **The answer language is named explicitly.** "Use the language of the question" was
  not enough: a Bosnian question about an English document got an English answer. A small
  word-list and diacritics detector (`app/language.py`) names Bosnian or English in the
  prompt. For other languages the old generic instruction remains.
- **Evidence highlighting.** Each source carries `highlight`, the evidence passage as an
  exact span of the page text (found with the same `locate_quote`), or null. Requirement
  rows use their stored quote. The UI marks the passage in the pdf.js text layer by
  matching on text with whitespace, hyphens, pipes, case and typographic quotes removed.
  pdf.js splits text into items differently from pypdfium2, and these are exactly the
  characters that differ. Only an exact match on that key is marked; otherwise nothing is.
- **Full-window layout.** The page no longer scrolls on screens 1000 px and wider; each
  column scrolls inside itself. The cabinet is 280 px. The cited-page panel exists only
  while a page is open and takes `minmax(420px, 33%)`.
- **No sideways scrolling in the requirements table.** The table uses a fixed layout with
  narrow fixed columns and wrapping text. When the table area is narrower than 980 px
  (for example at 1280 or 1536 px with a page open), each row is shown as a card with the
  same fields, including the stamp, instead of six cramped columns. A container query
  decides, so it depends on the space the table actually has.
- **Frontend unit tests use Node's built-in test runner** (`npm test`), so no test
  framework dependency was added.

## Narrowing the scope of extraction and speeding it up

- **Two kinds of item, decided by rules.** Extraction now keeps only obligations and
  limits, as defined in the README. The kind is decided by deterministic rules in
  `app/extraction_scope.py`, not by the model:
  - obligation wording ("shall", "must", "should", "recommended", "required", "do not",
    "avoid", "ensure", ...) makes an item an obligation;
  - otherwise, a row with values in a ratings or operating-conditions section is a limit;
  - otherwise, an instruction verb at the start of a sentence makes it an obligation
    (introductory clauses such as "To prevent oxidation," and list markers are skipped);
  - otherwise, numbers next to boundary words ("maximum", "at least", "up to") make it a
    limit;
  - everything else is a description and is dropped.

  The model no longer outputs a kind, which also shortens its output. Asking a 4B model
  to judge "is this a requirement" was the main source of the 334 rows on TMP117.
- **Rule details that came from real failures on TMP117.**
  - "Recommended Operating Conditions" is a table name, not a recommendation.
  - Numbers inside names ("ADD0", "TMP117", "C1") and footnote markers are not values.
  - Footnotes and table condition lines ("Over free-air temperature range unless
    otherwise noted") are never limits.
  - A word in capitals ("STOP condition setup time") is a signal name, not an imperative.
  - A text with numbers must end in punctuation to count as an instruction, so "Load
    capacitance up to 400 pF" is not read as "Load ...".
  - In a ratings section a row is a limit before any imperative reading, so "Supply
    voltage ..." is not read as "Supply ...".
- **Code is never an obligation.** "const trails = require('./trails.json')" matched
  "require". Calls such as `require(` no longer count as wording, and declarations, calls
  with string arguments and arrow functions are dropped as code.
- **Skipping before the model.** Chunks whose heading path contains a word from
  `EXTRACTION_SKIP_SECTIONS` are skipped. The list covers features, description,
  revision history, characteristics, register maps, documentation support, mechanical,
  packaging, orderable, legal notice and similar. Tables are also skipped by header
  shape: test steps, change logs, default values, bit fields, register maps and
  troubleshooting. The count shown as "N sections skipped" includes both. The skipped
  labels are logged.
- **Heading paths had to be fixed first.** Docling reported table captions ("Table 7-8.
  ... Field Descriptions"), "Note", the page header and package drawing titles as level-1
  headings. That replaced the real path, so register tables were no longer under "Register
  Maps" and could not be skipped. When a document uses numbered headings, an unnumbered
  heading now becomes a child of the deepest numbered one. On TMP117 this alone took the
  chunks sent to the model from 120 to 31 (before grouping). The change also affects
  section paths in answers. Documents ingested earlier keep their old paths until they
  are uploaded again.
- **Ratings tables are read without the model.** Every data row with values in a table
  under a ratings or operating-conditions heading is a limit by definition. The model
  skipped rows of these tables from run to run (4, 7 or 12 limits on the same document).
  Reading the rows directly gave the same 8 TMP117 limits every time. The type is taken
  from the last unit in the row (V, A, Ω → electrical; °C → environmental; s, Hz →
  performance).
- **Table rows are stored whole.** The model often quoted only the label cell of a row.
  A quote found in a table is widened to the full line(s) of page text, so the values and
  units are part of the stored quote. Quotes in the prompt's row format ("- a; b; c") are
  matched again without that format when the first match fails.
- **The paraphrase is optional and off by default.** It never changed which rows were
  kept, and it roughly doubled the generated text. Times per call were 2.3 s against
  3.7 s on the lab, 6.7 s against 10.1 s on the datasheet fixture, and 89 s against 142 s
  for TMP117. When it is on, a paraphrase that fails the support check, strengthens the
  modality or adds must/shall to a quote without it is removed, and the row is kept with
  the quote only. That reverses the earlier rule, under which such rows were rejected; it lost
  genuine tasks whose paraphrase used synonyms.
- **Rejection reasons were renamed.** "Requirement not supported by quote" no longer
  exists, because unsupported paraphrases are removed, not rejected. Rows are now rejected
  as "quote not on page" or "not an obligation or limit". The second also covers
  fragments, headings, placeholders and learning objectives. The database column keeps
  its old name (`rejected_unsupported`).
- **Grouped model calls.** Consecutive in-scope chunks of the same top-level section are
  sent in one call, up to `EXTRACTION_GROUP_CHARS` (3000). A quote is located on any page
  of its group. On TMP117, 27 chunks became 9 calls.
- **Thinking mode confirmed off; time is output length.** Every Ollama call is now
  logged with prompt tokens, output tokens, duration and thinking characters. The
  earlier run on TMP117 had 0 thinking characters across 187 calls. It used 135k prompt
  and 52k output tokens in 1184 s of model time, about 23 ms per output token. Time was
  almost entirely generation, so fewer calls and shorter outputs were the levers.
- **No parallel requests.** A second Ollama server with `OLLAMA_NUM_PARALLEL=2` loaded
  the model at 5.4 GB with 21% on the CPU, because the KV cache doubles. Two concurrent
  calls took 101 s. One call on that server took 141 s, and one call on the normal server
  (100% GPU) took 89 s. `EXTRACTION_CONCURRENCY` exists (default 1) for larger GPUs but is
  not used here. The test used a separate server on port 11435 so the owner's Ollama
  settings were not changed.
- **Fixture label changed by the definition, not by results.** In the lab fixture, "Each
  row shows the trail name and its length in kilometres." was a genuine requirement in
  the first version. Under the new definition it is a description (no obligation wording, no
  instruction), so it is now listed as not to be extracted. The README states this
  trade-off. Two imperative tasks and a troubleshooting table were added to the lab
  fixture. The new datasheet fixture was written before the final rules were tuned on
  TMP117, and none of its labels were changed after seeing results.
- **Answers are shown line by line.** The API returns `lines`, one per claim, each with
  the indexes of its sources. Consecutive claims that repeat the same opening words (three
  or more) are merged into one line ("Accuracy is 0.1 °C ...; 0.15 °C ..."). `answer` is
  the lines joined by newlines.
- **Quotes on one line for people.** The stored quote stays an exact span of page text
  with its line breaks. The API and the exports show it with whitespace collapsed; a
  hyphen at a line end is kept and joined.
- **Highlight bug: a second chip on the same page.** The marks were computed only when
  pdf.js loaded the page text. Clicking another quote on a page that was already open
  never recomputed them. On the earlier Docker build the first chip showed 2 marks and the
  second chip on the same page 0. The page text is now cached and the marks are recomputed
  for every new passage; the same test shows 2 marks for both.
- **Progress shows elapsed time and an estimate.** The API returns `elapsed_seconds`
  (from new start and finish timestamps). The UI estimates the remaining time as elapsed
  divided by calls done, times calls left.

## Refused answers, debug details, imperatives and ingestion

- **Cross-language retrieval: search the question and its English translation.** A
  Bosnian question about an English datasheet ("Koliko uređaja može biti na jednoj I2C
  sabirnici?") found the right TMP117 passage only at rank 18, outside the five chunks
  the model saw. Multilingual embeddings alone do not close that gap on long technical
  documents. For a Bosnian question the model now writes an English phrasing
  (structured output, one field), and both phrasings are searched in one Qdrant query:
  dense and BM25 prefetches for each, fused with RRF. English questions are not
  translated, so they cost no extra call. The answer is still written in the question's
  language. Free-text translation was tried first and rejected: the 4B model sometimes
  returned its reasoning instead of the translation.
- **Eight chunks, at most two per page (was five).** For "How much current does the
  device typically consume?" the typical standby value was at rank 7 and the first five
  chunks came from two pages. The model now sees 8 chunks (`RETRIEVAL_TOP_K`, maximum 10)
  and at most 2 come from one page (`RETRIEVAL_MAX_PER_PAGE`), so one dense page cannot
  crowd out the rest. 8 chunks of at most 1,500 characters stay well inside the 8,192
  token context. The prompt now asks for each value with its condition when the
  excerpts give several.
- **Citation markers inside a statement no longer drop the claim.** The model sometimes
  wrote "(excerpt 3)" or "[1, 2]" inside the statement; the marker's numbers were then
  checked as values ("number 3 is not in the cited excerpts") and the correct claim was
  discarded. Markers are removed before the check. A claim that cites no excerpt but
  states numbers is matched to the evidence passages that contain all of them; without
  numbers it is still dropped.
- **Number matching covers notation, not meaning.** The check already normalised the
  micro sign (U+00B5 and U+03BC), hyphens and spacing ("0.1-µF" supports "0.1 μF"). It
  now also reads number words in the source ("four", "četiri" support "4"), English and
  Bosnian up to twenty. Unit conversion ("100 nF" supporting "0.1 µF") is deliberately
  not done: the claim must use a number that is printed in a cited passage. On TMP117
  both "100 nF" and "0.1 µF" are printed, so both are kept.
- **Debug details per request.** `POST /ask` takes `debug` (default false). With it the
  response carries the searched phrasings, the retrieved chunks (rank, page, section,
  score, snippet), whether the model answered before verification, and each discarded
  claim with a reason, a code (`no_citation`, `number_missing`) and the missing numbers,
  so the UI can translate the reason. The UI always sets `debug`: it costs no model call,
  and a "not in the documents" answer can show "Why?" without asking again (asking again
  could give a different answer).
- **Imperatives after any short introductory phrase.** The intro clause was a fixed word
  list (to, if, when, before, ...), so "Below the summary row, add ..." was missed, and
  "read" was not an instruction verb. Now the text after the first comma is tried when
  the phrase before it has at most 15 words. A finite verb within the next words ("the
  empty state says so", "power consumption is 250 nA") marks a subject with its verb, so
  such a sentence stays a description. Added verbs: read, review, complete, finish,
  prepare, study, watch.
- **Lead-in sentences are not requirements.** "Check your work with the following
  steps." only introduces the table after it; sentences ending in "the following ..." or
  "as follows" are dropped.
- **Process type by rule.** An instruction starting with test, commit, push, submit or
  upload gets type `process`, overriding the model's type, so the same sentence always
  gets the same type.
- **Timing rows are never limits.** Before, a timing row became a limit only when it
  contained a boundary word, so one row of the TMP117 timing table was a limit and the
  others were not. Timing and switching-characteristics sections are now excluded as a
  whole and "timing requirement" no longer marks a limit section. The README documents it.
- **Ingestion: models are not downloaded again; they are loaded in the background.** In
  Docker the FastEmbed and Hugging Face caches already live on the data volume
  (`MODELS_CACHE_DIR`, `HF_HOME`), so a rebuild keeps them. What made the first upload
  after a start slow was loading the models into memory. The app now loads the Docling
  pipeline and the embedding models in a background thread at startup; a failure there
  is logged and the models load on first use as before.
- **Ingestion progress from Docling's page events.** Docling 2.x reports each finished
  page through `progress_callback`, so the document is still converted in one call (no
  page batches that could split tables). The page count is stored in
  `documents.ingest_pages_done` (added by the column migration) and shown as "Reading…
  N / M pages", then "Indexing…". Each ingestion logs parse, embed and upsert time
  separately.

## Duplicate chunks, noise chunks and developer mode

- **Duplicates came from the same PDF indexed more than once.** The reported list
  ("Koja je cijena", whole cabinet) had 4 distinct chunks in 8 slots. The Docker
  database held TMP117 three times (documents 5, 6 and 9 in three drawers). The copies
  have identical vectors, so each copy of a chunk ranks right after the other in every
  prefetch, and RRF gives them consecutive, falling scores (0.643, 0.500, 0.450). The
  per-page cap counts per (document, page), so three copies of page 40 passed it. Qdrant
  itself never returned one point twice: RRF fuses by point id.
- **Retrieval deduplicates by point id and by text before the page cap.** Candidates are
  taken in fused order; a chunk is skipped when its point id or its normalised text was
  already picked, then the page cap applies, and the next distinct candidates fill the
  slots. All fused candidates are fetched (each prefetch returns at most
  `RETRIEVAL_PREFETCH_LIMIT`), so enough distinct chunks remain. Identical text from two
  uploads is the same evidence, so the best-ranked copy is enough; the debug list is
  built from the same list, so it shows each chunk once.
- **Identical text within a document is indexed once.** On TMP117 a table condition line
  and "Return to Register Map." were stored on several pages. The first occurrence is
  kept. Uploading the same file twice is still allowed (drawers are independent);
  retrieval handles the copies.
- **Developer mode hides "Why?".** The panel is a diagnostic tool, not part of the
  answer. A "Developer mode" checkbox next to the language buttons, off by default and
  stored in `localStorage` (`reqtrace.developerMode`), controls it. With it off the
  frontend sends no `debug` field (the API default stays off) and shows no "Why?"; with
  it on every `/ask` carries `debug: true`, as the UI did before the switch existed. The request body is built
  by one helper (`askBody`), which the frontend tests check. The setting is shared
  between components through `useSyncExternalStore`, so switching it applies to the next
  question without a reload; answers already shown keep their debug data hidden or shown
  with the switch.
- **Noise filter at ingestion, on parser blocks.** Labels from package drawings ("SCALE
  5.500"), bare dates, part codes and repeated headers were indexed and ranked first for
  questions with no answer. The filter (`app/noise.py`) works on the parser's blocks
  before chunking, so noise inside a page's text is removed too, and table chunks are
  judged on their cells, not on the heading path prepended to them. Headings are never
  dropped: the section path and the extraction skip rules are built from them.
  - *Repeated furniture:* a text block of at most `INGEST_REPEATED_MAX_CHARS` (60) found
    on at least `INGEST_REPEATED_MIN_PAGES` (3) pages. The exact text always counts; text
    that differs only in numbers ("Page 3 of 50") counts only if it carries no content,
    so "Commit the work of task 1." to "... task 5." on five pages is kept. The length
    limit keeps a repeated measurement condition (84 characters on TMP117) in the index.
  - *Almost no words:* fewer than `INGEST_MIN_WORDS` (1) words, where a word has two or
    more letters and, in text of up to 60 characters, capitals-only tokens ("SCALE",
    "REVISED", "MAX") do not count. A value with a unit or an instruction keeps a block
    regardless. A threshold of 2 words was tried first on TMP117 and dropped one-word list
    items ("- Thermostats", "- NIST traceability"), so it is 1. Length alone is not used
    for the same reason.
  - A value with a unit must be a number standing on its own: the first Docker run kept
    the part code "DRV0006B" because "0006B" read as 6 bytes.
  - On TMP117 (Windows Docling) the filter drops 29 repeated and 20 wordless blocks of
    543; together with the duplicate-text rule, 187 chunks become 161. Nothing it drops
    holds an obligation or a value with a unit.
  - Documents ingested earlier keep their chunks; they must be uploaded again.
