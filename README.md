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
