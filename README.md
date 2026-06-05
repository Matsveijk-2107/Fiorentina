# Fiorentina: incremental match-event pipeline

A small, reproducible pipeline that turns per-match JSON into typed, queryable
Parquet and re-runs incrementally. Only new or changed matches get reprocessed,
a re-run with no changes does nothing, and a corrected re-export overwrites
exactly its own match and nothing else.

This is the practical deliverable for the take-home. The original brief is kept
verbatim in [`docs/BRIEF.md`](docs/BRIEF.md). The theory answers are in
[`THEORY.md`](THEORY.md) (Italian). The reasoning behind the design and the
trade-offs I made are in [`docs/DESIGN.md`](docs/DESIGN.md).

---

## Quick start

One command runs the whole thing.

Windows (PowerShell):
```powershell
./run.ps1
```

macOS / Linux:
```bash
./run.sh
```

Either script installs the two dependencies and walks the whole story: initial
batch, then an idempotent re-run, then the update batch, then a few example
questions.

If you'd rather drive it yourself, you need two dependencies (`duckdb` and
`pyarrow`):

```bash
pip install -r requirements.txt
export PYTHONPATH=src                 # PowerShell: $env:PYTHONPATH="src"

# 1) initial batch
python -m football_pipeline run --source candidate_dataset/data/raw
# 2) later: add the update batch, only new/changed matches are processed
python -m football_pipeline run --source candidate_dataset/data/raw --source candidate_dataset/data/raw_update
# 3) ask questions
python -m football_pipeline query top_scorers --limit 10
```

By default `run` uses both batches (`raw` + `raw_update`), so a plain
`python -m football_pipeline run` also works end to end.

---

## Why this stack (Python + Parquet + DuckDB)

The choice is deliberate, and it mirrors the architecture in the theory half of
the brief.

Parquet on disk is the "open files on object storage" the company already keeps
its processed data in: columnar, typed, compressed, and portable, with nobody
owning the format. DuckDB is the "lightweight engine that queries those files".
No server, no cluster, nothing copied into a proprietary store; it just reads the
Parquet and pushes the filters down into it. pyarrow does the writing, with
explicit schemas so the column types stay put across runs. Everything else, the
validation, the hashing, the manifest, the CLI, is plain standard library.

So the practical pipeline isn't a toy sitting next to the production design;
it's the same shape at small scale. And it grows along the path `THEORY.md` lays
out: keep the files, swap DuckDB for Athena or BigQuery external tables once the
volume calls for it.

---

## How the incremental engine works

```
candidate_dataset/data/raw/<Competition>/<match_id>.json   ─┐
candidate_dataset/data/raw_update/<Competition>/<id>.json  ─┤  (precedence: update wins)
                                                            │
            ┌───────────────────────────────────────────────┘
            ▼
   discover → resolve ownership → hash → parse+validate (only if changed) → write
                                                            │
                                                            ▼
   warehouse/
     matches/<id>.parquet   players/<id>.parquet   events/<id>.parquet     ← silver (one file/match)
     gold/player_match_stats.parquet  gold/player_season_stats.parquet     ← gold (metrics)
     _state/manifest.json                                                   ← incremental memory
```

1. Discover every `*.json` under the source dirs, in precedence order.
2. Resolve ownership. When a `match_id` shows up in more than one batch (the
   corrected `1003` is in both `raw/` and `raw_update/`), the later batch wins.
   That decision is deterministic and doesn't depend on filesystem ordering.
3. Hash and compare. Each owning file's SHA-256 is checked against the manifest.
   A match is parsed and rewritten only if it's new, changed, owner-switched, or
   its output is missing. Everything else is skipped.
4. Validate at the boundary. Structural violations (bad type, illegal outcome,
   out-of-range coordinate) are rejected; soft issues, such as goals that don't
   reconcile with the score, are logged as warnings rather than silently dropped.
5. Write one Parquet file per match, so a changed match rewrites exactly one
   small file. Gold is recomputed only when silver actually changed.
6. Handle deletions: if a match's source disappears, its files are pruned.

Three properties fall out of this. Only changed matches ever touch disk. An
identical re-run does no work at all. A re-export overwrites its own match in
place and leaves the rest alone. Incremental, idempotent, and self-correcting,
built into the plumbing rather than bolted on after.

You can watch all three happen in one go via `run.ps1` / `run.sh`. Typical
output:

```
[1/4] Initial batch  -> processed 24, unchanged 0
[2/4] Re-run         -> processed 0,  unchanged 24      (idempotent: gold not recomputed)
[3/4] Update batch   -> processed 5,  unchanged 23      (1003 corrected + 1013/1014/2013/2014 new)
match 1003 -> "Genoa FC 3-2 Parma FC", corrected = true
data quality -> 28/28 matches reconcile goals vs score
```

---

## Asking questions

```bash
python -m football_pipeline queries          # list available questions
python -m football_pipeline query <name> [--format table|csv|json] [params]
```

| Query | Answers |
|-------|---------|
| `matches_per_competition` | matches & total goals per competition/season |
| `goals_per_competition` | goals, shots, average xG per shot |
| `top_scorers --limit N` | leading scorers (goals, xG, goals−xG) |
| `top_xg --limit N` | highest cumulative xG |
| `best_pass_completion --min-passes N` | most accurate passers (volume-gated) |
| `top_tacklers --min-tackles N` | best tackle win % |
| `match_summary --match-id ID` | one-match summary |
| `data_quality_goals_vs_score` | independent goals-vs-score reconciliation |

For anything not pre-baked, there's a raw-SQL escape hatch:
```bash
python -m football_pipeline query --sql "SELECT competition, COUNT(*) FROM matches GROUP BY 1"
```
The views `matches`, `players`, `events`, `player_match_stats`, and
`player_season_stats` are all available to raw SQL.

---

## Per-player metrics (optional bonus, implemented)

`gold/player_match_stats.parquet` and `player_season_stats.parquet` implement
the three metrics described in `THEORY.md`:

1. Finishing: xG, goals, and goals minus xG (over/under-performance).
2. Passing: completion percentage and volume.
3. Defensive contribution: tackle win percentage and tackles per 90.

There are a few extras too (shot conversion, dribbles, fouls). Season rows
expose per-90 rates so players with different minutes are comparable, and those
rates are `NULL` below a small minutes threshold to avoid noise.

A note on the synthetic data: each match ships its own `player_id` space (for
example, `1003xx` belongs to match 1003), so every player appears in exactly one
match. The season aggregation is therefore 1:1 here. The code still does the
real cross-match aggregation; it just has nothing to combine in this dataset.

---

## Tests

```bash
pip install -e ".[dev]"     # pytest, pytest-cov, ruff, mypy, bandit
python -m pytest            # runs the tests and the coverage gate
```

39 tests cover validation (types, ranges, enums, reconciliation warnings),
content hashing, the incremental guarantees (idempotency, change-only
reprocessing, correction override, deletion, full-refresh), and the
metrics/queries on a small known dataset. Coverage sits at 92%, with the gate
set to fail under 80%.

`make quality` (or `quality.ps1` on Windows) also runs ruff, mypy, and bandit;
their settings live in `pyproject.toml`.

---

## Project layout

```
src/football_pipeline/
  config.py        warehouse paths & source precedence
  models.py        typed dataclasses + event/outcome enums
  validation.py    raw JSON -> typed records (boundary validation)
  hashing.py       SHA-256 content hashing (change detection)
  state.py         the manifest (incremental memory; atomic writes)
  parquet_io.py    per-match Parquet writers (explicit Arrow schemas)
  ingest.py        the incremental engine (ownership, skip, upsert, prune)
  warehouse_db.py  DuckDB connection with views over the Parquet
  metrics.py       gold layer (SQL): per-player metrics
  queries.py       named, parameterised analytical queries
  cli.py           run / metrics / query / status / queries
tests/             pytest suite
docs/BRIEF.md      original take-home brief (preserved)
docs/DESIGN.md     design decisions & trade-offs
THEORY.md          theory answers (Italian)
run.ps1 / run.sh   one-command demo
quality.ps1        lint, format, type, security, and test checks
pyproject.toml     dependencies and tool config (ruff/mypy/bandit/coverage)
```

The `warehouse/` directory is fully derived and git-ignored. Delete it and
re-run to reproduce from scratch.
