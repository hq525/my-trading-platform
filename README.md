# my-trading-platform

Personal paper-trading platform for practicing swing trading of US stocks/ETFs.
Spec: docs/superpowers/specs/2026-07-03-paper-trading-platform-design.md

## Backend quickstart

    cd backend
    uv sync
    cp .env.example .env   # then edit PT_PASSWORD and PT_SECRET_KEY
    uv run uvicorn --factory app.main:create_app --port 8000

API at http://localhost:8000/api (docs at /docs). Log in via POST /api/login.
Without Alpaca keys it falls back to yfinance automatically.

## Tests

    cd backend
    uv run pytest -q

## Strategies

Drop a Python file in backend/strategies/ subclassing
app.strategy.base.Strategy, restart, then enable it via POST
/api/strategies/{name}/toggle. Each strategy trades its own account.

## Run everything with Docker

    cp backend/.env.example backend/.env   # then edit PT_PASSWORD and PT_SECRET_KEY
    docker compose up --build -d

Open http://localhost:3000 and log in with PT_PASSWORD. The backend is not
exposed on the host; the UI proxies /api/* to it inside the compose network.
The SQLite database persists in the db-data volume (back it up with
`docker compose cp backend:/data/paper_trading.db ./backup.db`).

On a VPS: install Docker, clone the repo, same two commands, then put the
box behind your firewall of choice with only port 3000 (or a reverse proxy
with TLS) reachable.

## Dev mode (hot reload)

    cd backend && uv run uvicorn --factory app.main:create_app --port 8000
    cd frontend && npm install && npm run dev   # http://localhost:3000

The Next dev server proxies /api/* to http://localhost:8000 (override with
BACKEND_URL).
