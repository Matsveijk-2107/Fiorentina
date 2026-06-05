<#
.SYNOPSIS
  Run the full quality gate (Windows / PowerShell): lint, format-check, types,
  security scan, and tests with the >=80% coverage gate.

.DESCRIPTION
  Mirrors `make quality`. Install the dev tools first with:
    pip install -e ".[dev]"
#>
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"

Write-Host "==> ruff (lint)" -ForegroundColor Cyan
python -m ruff check src tests

Write-Host "==> ruff (format check)" -ForegroundColor Cyan
python -m ruff format --check src tests

Write-Host "==> mypy (types)" -ForegroundColor Cyan
python -m mypy src/football_pipeline

Write-Host "==> bandit (security)" -ForegroundColor Cyan
python -m bandit -r src -q

Write-Host "==> pytest (+coverage gate)" -ForegroundColor Cyan
python -m pytest

Write-Host "`nAll quality checks passed." -ForegroundColor Green
