#!/bin/bash
set -euo pipefail

pnpm install --frozen-lockfile
uv sync --frozen --inexact
python -m py_compile \
  osv-dashboard/app.py \
  osv-dashboard/historical_service.py
