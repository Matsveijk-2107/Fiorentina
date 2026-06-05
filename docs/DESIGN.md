# Design decisions & trade-offs

The brief leaves the tools, structure, and formats up to the candidate and says
they'll ask you to justify them (*"Strumenti, struttura e formati li scegli tu,
ti chiederemo di spiegarci le scelte."*). So here's the reasoning.

## 1. What I optimised for

Roughly in this order:

1. A correct incremental engine. That's the core of the exercise: incremental,
   idempotent, and able to absorb a corrected re-export without disturbing
   anything else.
2. Coherence with the company's actual architecture. The practical pipeline
   should *be* a miniature of what the brief describes, open Parquet with a light
   query engine on top, so the two halves of the take-home tell the same story.
3. Small, readable modules. One job per file, typed boundaries, nothing clever
   for its own sake.
4. Reproducibility. One command, two dependencies, and output you can delete and
   rebuild from scratch.

## 2. Stack: Python + pyarrow (Parquet) + DuckDB

| Need | Choice | Why |
|------|--------|-----|
| Clean storage format | Parquet | Columnar, typed, compressed, open, portable to object storage, no lock-in. Exactly what the company already keeps processed data in. |
| Query engine | DuckDB | Zero-infra light engine over files. Reads Parquet in place, SQL, fast. It's the local stand-in for Athena, BigQuery-external, or Trino. |
| Writers / typing | pyarrow | Explicit, stable column schemas so types survive round-trips. |
| Glue | Python stdlib | `json`, `hashlib`, `dataclasses`, `pathlib`, `argparse`, `logging`. No framework needed. |

I skipped pandas on purpose. pyarrow and DuckDB already handle the reading,
typing, and querying, so it would be a heavy dependency earning its keep nowhere.
An orchestration framework would be worse: pure ceremony around a single batch
step, and it would bury the logic that actually matters.

## 3. Storage layout: one Parquet file per match

```
warehouse/{matches,players,events}/<match_id>.parquet   # silver
warehouse/gold/player_{match,season}_stats.parquet       # gold
warehouse/_state/manifest.json                           # incremental memory
```

Why one file per match, rather than partitioning by competition or keeping a
single table? Because a match is the unit of change. When a correction arrives I
want it to rewrite as little as possible, ideally one small file and nothing
next to it. That's what this layout buys: an update rewrites a single file,
DuckDB globs the directory (`read_parquet('events/*.parquet')`), and the whole
thing still reads as one logical table.

The cost is the small-file problem. This doesn't stretch to millions of matches,
and at that point you'd partition by `competition/season` and compact, which I
get into in `THEORY.md`. For a few thousand matches it's the clearest thing that
works.

## 4. The incremental mechanism: content hashing + a manifest

The run as a whole looks like this:

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

mtime lies the moment a file is copied or re-checked-out, so change detection
hashes the bytes instead, with SHA-256. An unchanged file is then provably
unchanged, and the corrected `1003`, which differs by content, gets caught.

The manifest is a plain JSON file that remembers, per source file, the hash last
ingested and which source currently owns each `match_id`. It's what turns a
second run into a no-op. JSON specifically because it reads well, diffs cleanly
in review, and drops straight onto object storage later.

Both the Parquet and the manifest are written atomically (temp file, then
`os.replace`), so a run killed halfway through never leaves corrupt state behind.

### Ownership resolution, the subtle part

`1003` lives in both `raw/` and `raw_update/`, and the corrected copy has to win
every time, not just when the filesystem happens to list it last. The engine:

1. reads each file's `match_id` once,
2. builds `match_id -> owning source`, with later source dirs winning (precedence
   follows CLI order, and the default puts `raw_update` last),
3. writes only the owning file, and records the loser as shadowed so a future
   edit to it is still noticed.

Nothing here depends on directory iteration order. Re-run it unchanged and both
files are skipped, with the on-disk output already showing the corrected `1003`.

## 5. Validation at the boundary

Raw JSON gets no trust. `validation.py` coerces and checks every field against
`SCHEMA.md`, and it splits problems into two kinds.

Hard failures reject the match outright, so malformed data never lands in the
warehouse: a missing or mistyped required field, an unknown event type, an
illegal outcome for its type, `x/y` outside `[0,100]`, `xg` outside `[0,1]`. One
bad match doesn't take the rest of the batch down with it.

Soft warnings are logged rather than raised: events implying a different score
than `score`, or an actor who isn't on the roster. That's deliberate. A real
pipeline flags suspect data and keeps going instead of failing a whole load over
it.

Events are written in a deterministic `(minute, second, event_id)` order, so the
output is stable and diffs cleanly.

## 6. Keys and a data-modelling note

`event_id` is unique within a match but resets between matches. I checked, and
the same id turns up in as many as 24 of them. So the real key for an event is
`(match_id, event_id)`, and that's what the code uses throughout. It's the sort
of assumption ("ids are global") that looks fine until it quietly corrupts a
join, so I nailed it down rather than hope.

## 7. Layers: silver vs gold, and incremental gold

Silver is the clean, typed facts: matches, players, events, one row per record.
Gold sits on top, `player_match_stats` for the per match-player building blocks
and `player_season_stats` for the per-90 rates that let you compare players with
different minutes.

Gold only recomputes when silver actually changed in a run. A full recompute here
is sub-second, so I rebuild it wholesale rather than diffing partitions, which is
simpler and plainly correct. At real scale you'd recompute only the affected
matches and re-aggregate the season and player groups they touch; the code keeps
that change confined to `metrics.py`.

## 8. Security & safety touches

- Query parameters bind through DuckDB variables, never string interpolation, so
  nothing typed at the CLI can reach the SQL as code.
- No secrets, no network, no shell-out, and all input is validated at the
  boundary.
- Every write is atomic, and the output directory is disposable and git-ignored.
- bandit runs clean. Its one real flag is the view-creation SQL string in
  `warehouse_db.py`, which is quote-escaped and reachable only by internal,
  non-user input; it's annotated with a justified `# nosec`.

## 9. Quality gate

Wired for the usual Python toolchain, configured in `pyproject.toml` and run on
every push by a GitHub Actions workflow (`.github/workflows/ci.yml`):

- ruff for lint (pyflakes, pycodestyle, isort, bugbear, pyupgrade, naming) and
  formatting. Clean. (`python -m ruff check src tests`)
- mypy for type checking across `src/`. Clean.
- bandit for the security scan. Clean.
- pytest plus pytest-cov: 50 tests, 92% coverage, gate fails under 80%.

There's also a `verify` command (`python -m football_pipeline verify`) that
re-reads the written Parquet and independently re-asserts the runtime invariants:
goals reconcile with the score, event ids are unique within a match, required
columns are never null, and the manifest agrees with what's on disk.

## 10. What I'd add with more time

- Schema-drift detection in the manifest, to warn when a new field appears.
- Partitioning by competition, plus compaction, once match counts climb.
- A persistent DuckDB catalog if interactive querying becomes a regular thing.
