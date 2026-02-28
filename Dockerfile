FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies only — project itself is deferred for caching
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code, then install the project (deps already cached)
COPY src ./src
RUN uv sync --frozen --no-dev

# Put venv binaries (python, uvicorn, etc.) on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080
EXPOSE 8080

CMD uvicorn ai_research_backend.api:app --host 0.0.0.0 --port ${PORT}
