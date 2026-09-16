#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$(pwd)"
export PYTHONUTF8=1
echo "== Ruff =="
python -m ruff check src tests main.py || ruff check src tests main.py
echo "== Pytest =="
python -m pytest
echo "== Windows markers (expect skip on Linux) =="
python -m pytest -m windows -v || true
echo "== ALL GREEN (Linux subset) =="
