#!/bin/bash
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  uv venv .venv
fi

if ! .venv/bin/python -c "import fastapi" 2>/dev/null; then
  echo "Installing dependencies..."
  uv pip install -r requirements.txt
fi

echo "Starting local server at http://localhost:8000"
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
