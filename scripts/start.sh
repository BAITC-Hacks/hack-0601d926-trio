#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r backend/requirements.txt
if [ "${1:-}" = "--pipeline-only" ]; then
  exec .venv/bin/python -m backend.app.analytics
fi
cd frontend
npm ci
npm run build
cd ..
echo 'Graph Money: http://127.0.0.1:8000 | API docs: http://127.0.0.1:8000/docs'
exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
