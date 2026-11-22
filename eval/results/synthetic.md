| Mode | Hit rate @5 | Hit rate @8 | Answer accuracy | Refusal accuracy | Avg answer time |
|---|---|---|---|---|---|
| dense | 97% | 100% | 100% | 100% | 4.5 s |
| bm25 | 97% | 97% | 97% | 100% | 4.0 s |
| hybrid | 97% | 100% | 100% | 100% | 3.8 s |

30 answerable and 7 unanswerable questions.

| Mode | Language | Answer accuracy | Refusal accuracy |
|---|---|---|---|
| dense | bs | 100% (4) | 100% (1) |
| dense | en | 100% (26) | 100% (6) |
| bm25 | bs | 100% (4) | 100% (1) |
| bm25 | en | 96% (26) | 100% (6) |
| hybrid | bs | 100% (4) | 100% (1) |
| hybrid | en | 100% (26) | 100% (6) |

- Dataset: synthetic (nx310_datasheet.pdf, SHA-256 b6f9eff3fd4b…)
- LLM: qwen3:4b
- Dense embeddings: intfloat/multilingual-e5-large
- Chunks given to the model: 8
- Date: 2026-11-20
- Ingestion: 28 s
- Duration: 499 s
