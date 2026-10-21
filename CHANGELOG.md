# Changelog

## 0.1.0 (2026-10-21)

First release.

- `pipeverify compare` and `pipeverify run` with PASS / REVIEW / BLOCK verdicts
- Checks: schema, row count, missing and new keys, duplicate keys, null rate, changed values,
  column totals, category values
- Sources: CSV, Parquet, JSON, DuckDB tables and queries, SQLAlchemy databases, pandas DataFrames
- Reports: terminal, Markdown (PR comments, job summary) and JSON
- GitHub Action with PR comment and verdict output
- Optional LLM explanation of findings (off by default; data values not sent)
- Retail demo with five realistic scenarios
