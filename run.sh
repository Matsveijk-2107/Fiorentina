#!/usr/bin/env bash
# One-command demo of the incremental football pipeline (macOS / Linux).
#
# Installs dependencies, then walks the incremental story:
#   1. ingest the initial batch (raw)
#   2. re-run -> idempotent no-op
#   3. add the update batch -> only new + corrected matches processed
#   4. answer a few questions about the data
set -euo pipefail
export PYTHONPATH=src

echo "==> Installing dependencies"
python -m pip install -q -r requirements.txt

echo; echo "==> [1/4] Initial batch (raw)"
python -m football_pipeline run --source candidate_dataset/data/raw

echo; echo "==> [2/4] Re-run identical input (should be a no-op)"
python -m football_pipeline run --source candidate_dataset/data/raw

echo; echo "==> [3/4] Add update batch (4 new + 1 corrected)"
python -m football_pipeline run --source candidate_dataset/data/raw --source candidate_dataset/data/raw_update

echo; echo "==> [4/4] Ask the data some questions"
python -m football_pipeline status
echo; echo "-- Matches per competition --"
python -m football_pipeline query matches_per_competition
echo; echo "-- Top scorers --"
python -m football_pipeline query top_scorers --limit 5
echo; echo "-- Corrected match 1003 (now 3-2) --"
python -m football_pipeline query match_summary --match-id 1003
echo; echo "-- Data quality: goals reconcile with score? --"
python -m football_pipeline query data_quality_goals_vs_score --format csv

echo; echo "Done. Try: python -m football_pipeline queries"
