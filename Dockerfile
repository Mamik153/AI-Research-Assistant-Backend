# Use Python 3.12 slim for smaller image
FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (better layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev) into system Python
ENV UV_SYSTEM_PYTHON=1
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY src ./src

# Cloud Run sets PORT (default 8080)
ENV PORT=8080
EXPOSE 8080

# Run with PORT so Cloud Run works; use 0.0.0.0 for Cloud Run
CMD python -m uvicorn src.ai_research_backend.api:app --host 0.0.0.0 --port ${PORT}
