# Architecture

reqTrace is one FastAPI service with three backing stores and two model runtimes:

| Component | Role |
|---|---|
| FastAPI (`app/`) | HTTP API; route handlers only translate HTTP to service calls |
| SQLite (`app/db.py`) | Drawers, documents (status, expiry, extraction progress), requirements |
| File volume | Uploaded PDFs, stored as `<document id>.pdf` |
| Qdrant (`app/vector_store.py`) | One collection, named vectors `dense` (cosine) and `bm25` (sparse, IDF modifier); payload indexes on `doc_id` and `drawer_id` |
| Docling (`app/ingestion.py`) | PDF → headings, text and tables with page numbers (OCR off) |
| FastEmbed (`app/embeddings.py`) | `multilingual-e5-large` dense vectors and BM25 sparse vectors, on the CPU |
| Ollama (`app/llm.py`) | `qwen3:4b` on the host GPU, behind the `LLMClient` interface |

`app/services.py` builds these collaborators once. Tests build the same container with
in-memory Qdrant, a fake embedder, an offline parser and a fake LLM.

## Module map

```
main.py          routes, error handlers, startup (collection, interrupted jobs), cleanup loop
documents.py     drawers and documents: CRUD, upload validation, ingestion, expiry
ingestion.py     Docling parsing → blocks; pure chunking of blocks → chunks
embeddings.py    FastEmbed dense + sparse
vector_store.py  Qdrant collection, upsert, delete, dense / bm25 / hybrid search
retrieval.py     scope filter (drawer, documents) + search
qa.py            prompt, structured answer, citation → sources
requirements.py  planning, grouped model calls, verification, deduplication, storage
extraction_scope.py  what is in scope: skipped, screened and full sections, tables, obligation and limit rules
verification.py  normalisation with index map, quote location (exact page span),
                 paraphrase support, modality, fragments
language.py      Bosnian / English detection for the answer language
export.py        CSV and XLSX
pdf.py           pypdfium2: validation, page count, page text
llm.py           LLMClient interface, OllamaClient
```

## Ingestion flow

```mermaid
sequenceDiagram
    participant UI
    participant API as FastAPI
    participant FS as File volume
    participant DB as SQLite
    participant DL as Docling
    participant FE as FastEmbed
    participant Q as Qdrant

    UI->>API: POST /drawers/{id}/documents (PDF)
    API->>API: size limit, %PDF- magic, pypdfium2 opens it
    API->>DB: document (status=processing, expires_at if temporary)
    API->>FS: store <id>.pdf
    API-->>UI: 202 Accepted
    Note over API: background task
    API->>DL: convert (no OCR)
    DL-->>API: headings, text, tables + page numbers
    API->>API: chunk: per section and page; one chunk per table
    API->>FE: embed chunks (passage prefix + heading path)
    FE-->>API: dense + sparse vectors
    API->>Q: upsert points with payload
    API->>DB: status=ready (or failed + message; partial vectors removed)
    UI->>API: GET /drawers/{id}/documents (polling)
```

Chunking rules (`chunk_blocks`):

- A heading starts a new section; numbered headings ("6.2") get their depth from the number.
- Text under one heading on one page becomes one chunk, split at sentence or word
  boundaries with overlap when it exceeds `CHUNK_MAX_CHARS`.
- Every table is its own chunk with the heading path prepended. Long tables are split by
  rows with the header repeated.
- No chunk spans two pages.

Deletion removes the SQLite row (requirements cascade), the Qdrant points (filter on
`doc_id` or `drawer_id`) and the file. The cleanup loop runs at startup and every
`CLEANUP_INTERVAL_MINUTES`, and does the same for documents past `expires_at`.

## Question flow

```mermaid
sequenceDiagram
    participant UI
    participant API as FastAPI
    participant FE as FastEmbed
    participant Q as Qdrant
    participant LLM as Ollama

    UI->>API: POST /ask {question, drawer_id?, doc_ids?, debug?}
    opt question not in English
        API->>LLM: translate to English (JSON {english})
    end
    API->>FE: embed each phrasing (query prefix; BM25 query weights)
    API->>Q: query_points: prefetch dense (20) + bm25 (20) per phrasing, filtered → RRF → 24 candidates
    Q-->>API: candidates with payload
    API->>API: keep the top 8, at most 2 per page
    alt no chunks
        API-->>UI: answered=false
    else
        API->>LLM: numbered excerpts + question, answer language named,<br/>JSON schema {evidence[excerpt, quote], answered, claims[statement, excerpts]}
        LLM-->>API: structured reply
        API->>API: strip citation markers; drop claims without a valid citation or with numbers<br/>(digits or number words) absent from question and cited excerpts
        API->>API: sources = excerpts cited by the remaining claims, one per document page;<br/>highlight = evidence located in the page text (exact span) or null
        API-->>UI: answer + sources, or answered=false if no claim survives;<br/>with debug: phrasings, retrieved chunks, discarded claims and reasons
        UI->>UI: on source click: open the page, mark the highlight in the pdf.js text layer
    end
```

## Requirement extraction flow

```mermaid
sequenceDiagram
    participant UI
    participant API as FastAPI
    participant Q as Qdrant
    participant LLM as Ollama
    participant PDF as PDF text layer
    participant DB as SQLite

    UI->>API: POST /documents/{id}/requirements
    API->>API: Ollama reachable? document ready? not already running?
    API-->>UI: 202 (status=running, started_at)
    API->>Q: scroll all chunks of the document
    API->>API: plan by whole heading: skip (EXTRACTION_SKIP_SECTIONS), screen descriptive sections<br/>(keep only sentences with obligation wording), skip tables by header shape;<br/>ratings / operating-conditions tables aside; group per top-level section, screened sentences apart
    loop every ratings table
        API->>PDF: locate each data row with values (or its words in any order, values exact) → limit
    end
    loop every group (one model call)
        API->>LLM: excerpts (tables as plain rows) → JSON schema {requirements: [quote, type, (paraphrase)]}
        API->>PDF: locate the quote on one of the group's pages (exact, fuzzy, or without row format)
        API->>API: table quote → whole line; classify: obligation / limit / description (dropped);<br/>from screened sentences only obligations
        API->>API: optional paraphrase kept only if faithful, else removed (row kept)
        API->>DB: progress (calls done / total)
    end
    API->>API: passive recommendations in the sent text, by rule, through the same checks
    API->>API: join parts of one sentence; one row per quote
    API->>DB: replace requirements (kind, quote, ...), rejection counts, skipped count, finished_at
    UI->>API: GET /documents/{id}/requirements (polling: elapsed_seconds, done/total; then ?format=csv|xlsx)
```

Kinds are decided by `app/extraction_scope.py`: obligation wording, then values in a
ratings section, then an instruction verb at the start of a sentence, then numbers next
to boundary words. Anything else is a description and is not stored.
