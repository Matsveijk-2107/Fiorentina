# Fiorentina: incremental match-event pipeline

A small, reproducible pipeline that turns per-match JSON into typed, queryable
Parquet and re-runs incrementally: only new or changed matches are reprocessed,
an identical re-run does nothing, and a corrected re-export overwrites just its
own match.

The original brief is in [`docs/BRIEF.md`](docs/BRIEF.md). The reasoning behind
the choices is in [`docs/DESIGN.md`](docs/DESIGN.md); the theory answers are in
[`THEORY.md`](THEORY.md) (Italian).

## Quick start

```bash
./run.sh        # macOS / Linux
./run.ps1       # Windows (PowerShell)
```

That installs the two dependencies (`duckdb`, `pyarrow`) and walks the whole
story: initial batch, an idempotent re-run, the update batch with the corrected
1003, and a few example questions. To drive it by hand:

```bash
pip install -r requirements.txt
export PYTHONPATH=src            # PowerShell: $env:PYTHONPATH="src"

python -m football_pipeline run                          # ingest both batches, incrementally
python -m football_pipeline query top_scorers --limit 10
python -m football_pipeline queries                      # list the questions you can ask
```

## What it does

Each match JSON under `candidate_dataset/data/{raw,raw_update}/` becomes one
Parquet file per match (silver), plus per-player gold metrics, queried in place
by DuckDB. A SHA-256 manifest makes re-runs incremental: a match is reprocessed
only when it is new, changed, or has switched owner. When a `match_id` appears in
both batches the later one wins, deterministically, so the corrected 1003
overwrites just itself. Validation rejects malformed data at the boundary and
records softer issues (e.g. goals not reconciling with the score) as warnings
rather than dropping the load. The why behind each of these is in
[`docs/DESIGN.md`](docs/DESIGN.md).

## Asking questions

`python -m football_pipeline query <name> [--format table|csv|json] [params]`

| Query | Answers |
|-------|---------|
| `matches_per_competition` | matches & total goals per competition/season |
| `goals_per_competition` | goals, shots, average xG per shot |
| `top_scorers --limit N` | leading scorers (goals, xG, goals−xG) |
| `top_xg --limit N --min-shots N` | highest cumulative xG (shot-volume gated) |
| `best_pass_completion --min-passes N --limit N` | most accurate passers |
| `top_tacklers --min-tackles N --limit N` | best tackle win % |
| `match_summary --match-id ID` | one-match summary |
| `data_quality_goals_vs_score` | goals-vs-score reconciliation |

`--sql "..."` runs raw SQL against the `matches`, `players`, `events`,
`player_match_stats`, and `player_season_stats` views.

## Tests & checks

```bash
pip install -e ".[dev]"
python -m pytest          # 51 tests, ~92% coverage (gate fails under 80%)
```

ruff, mypy, and bandit are configured in `pyproject.toml` and run in CI
(`.github/workflows/ci.yml`) on every push. `python -m football_pipeline verify`
re-reads the warehouse and re-asserts its invariants as an independent check.

## Layout

```
src/football_pipeline/   config, models, validation, hashing, state, parquet_io,
                         ingest, warehouse_db, metrics, queries, verify, cli
tests/                   pytest suite
docs/BRIEF.md            original brief (preserved)
docs/DESIGN.md           design decisions & trade-offs
THEORY.md                theory answers (Italian)
run.sh / run.ps1         one-command demo
```

`warehouse/` is fully derived and git-ignored: delete it and re-run to rebuild.
