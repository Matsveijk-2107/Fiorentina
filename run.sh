#!/usr/bin/env bash
# One-command demo of the incremental football pipeline (macOS / Linux).
# Mirrors run.ps1. Installs the two dependencies, then walks the story:
#   1. initial batch (raw)
#   2. identical re-run -> idempotent no-op
#   3. update batch -> only the new + corrected matches are processed
#   4. a few example questions
set -euo pipefail

# Prefer python3 (the usual name on macOS/Linux), fall back to python.
PY=${PYTHON:-$(command -v python3 || command -v python)}
export PYTHONPATH=src

echo "==> Installing dependencies"
"$PY" -m pip install -q -r requirements.txt

echo; echo "==> [1/4] Initial batch (raw)"
"$PY" -m football_pipeline run --source candidate_dataset/data/raw

echo; echo "==> [2/4] Re-run identical input (should be a no-op)"
"$PY" -m football_pipeline run --source candidate_dataset/data/raw

echo; echo "==> [3/4] Add update batch (4 new + 1 corrected)"
"$PY" -m football_pipeline run --source candidate_dataset/data/raw --source candidate_dataset/data/raw_update

echo; echo "==> [4/4] Ask the data some questions"
"$PY" -m football_pipeline status
echo; echo "-- Matches per competition --"
"$PY" -m football_pipeline query matches_per_competition
echo; echo "-- Top scorers --"
"$PY" -m football_pipeline query top_scorers --limit 5
echo; echo "-- Corrected match 1003 (now 3-2) --"
"$PY" -m football_pipeline query match_summary --match-id 1003
echo; echo "-- Data quality: goals reconcile with score? --"
"$PY" -m football_pipeline query data_quality_goals_vs_score --format csv

echo; echo "Done. Try: \"$PY\" -m football_pipeline queries"
