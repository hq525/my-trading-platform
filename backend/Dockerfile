FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY strategies ./strategies
ENV PT_DB_PATH=/data/paper_trading.db
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
