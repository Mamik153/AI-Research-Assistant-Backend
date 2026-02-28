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

# Pre-download the sentence-transformers embedding model so it is baked into
# the image layer instead of downloaded at runtime (saves ~90 MB bandwidth and
# avoids memory spikes from concurrent download + model load).
ENV PATH="/app/.venv/bin:$PATH"
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

RUN useradd --create-home --no-log-init appuser \
    && chown -R appuser:appuser /app

USER appuser

# Put venv binaries (python, uvicorn, etc.) on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080
EXPOSE 8080

CMD uvicorn ai_research_backend.api:app --host 0.0.0.0 --port ${PORT}
