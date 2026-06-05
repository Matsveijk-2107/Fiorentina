"""Incremental match-event pipeline.

Turns per-match JSON files into typed, queryable Parquet tables, processing
only new or changed matches on each run (true incremental + idempotent).

Public surface is intentionally small; orchestration lives in :mod:`ingest`,
the query layer in :mod:`queries`, and the CLI in :mod:`cli`.
"""

__version__ = "1.0.0"
