<#
.SYNOPSIS
  One-command demo of the incremental football pipeline (Windows / PowerShell).

  Installs dependencies, then tells the incremental story end-to-end:
    1. ingest the initial batch (raw)
    2. re-run -> idempotent no-op
    3. add the update batch -> only new + corrected matches processed
    4. answer a few questions about the data

.EXAMPLE
  ./run.ps1
#>
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"

Write-Host "==> Installing dependencies" -ForegroundColor Cyan
python -m pip install -q -r requirements.txt

Write-Host "`n==> [1/4] Initial batch (raw)" -ForegroundColor Cyan
python -m football_pipeline run --source candidate_dataset/data/raw

Write-Host "`n==> [2/4] Re-run identical input (should be a no-op)" -ForegroundColor Cyan
python -m football_pipeline run --source candidate_dataset/data/raw

Write-Host "`n==> [3/4] Add update batch (4 new + 1 corrected)" -ForegroundColor Cyan
python -m football_pipeline run --source candidate_dataset/data/raw --source candidate_dataset/data/raw_update

Write-Host "`n==> [4/4] Ask the data some questions" -ForegroundColor Cyan
python -m football_pipeline status
Write-Host "`n-- Matches per competition --"
python -m football_pipeline query matches_per_competition
Write-Host "`n-- Top scorers --"
python -m football_pipeline query top_scorers --limit 5
Write-Host "`n-- Corrected match 1003 (now 3-2) --"
python -m football_pipeline query match_summary --match-id 1003
Write-Host "`n-- Data quality: goals reconcile with score? --"
python -m football_pipeline query data_quality_goals_vs_score --format csv

Write-Host "`nDone. Try: python -m football_pipeline queries" -ForegroundColor Green
