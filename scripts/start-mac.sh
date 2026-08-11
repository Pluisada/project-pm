#!/bin/bash

# PM - Native Dev Start Script (Mac/Linux)
# Runs backend (uvicorn) and frontend (next dev) as separate local processes,
# without Docker. See CLAUDE.md: this is how the app has actually been tested.

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Load environment variables from .env if it exists (needed for OPENROUTER_API_KEY,
# since the backend does not auto-load .env when run natively). Skip PORT: it's
# meant for the Docker/uvicorn setup, but `next dev` also honors PORT and would
# bind the frontend to 8000 instead of 3000 if it leaked into its environment.
if [ -f .env ]; then
  export $(grep -vE '^(#|PORT=)' .env | xargs)
  echo "Loaded environment variables from .env"
fi

# Prefer `uv` if available, otherwise fall back to the backend's own venv
if command -v uv > /dev/null 2>&1; then
  BACKEND_CMD="uv run uvicorn main:app --reload --port 8000"
elif [ -x backend/.venv/bin/uvicorn ]; then
  BACKEND_CMD="./.venv/bin/uvicorn main:app --reload --port 8000"
else
  echo "✗ Neither 'uv' nor backend/.venv found. Run 'cd backend && uv sync' first."
  exit 1
fi

cleanup() {
  echo ""
  echo "Stopping..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting backend (uvicorn on :8000)..."
(cd backend && $BACKEND_CMD) &
BACKEND_PID=$!

echo "Starting frontend (next dev on :3000)..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

echo "Waiting for backend to be ready..."
for _ in $(seq 30); do
  if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "Backend is ready"
    break
  fi
  sleep 1
done

echo ""
echo "PM is running:"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop both."

wait
