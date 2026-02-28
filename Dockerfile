FROM python:3.12-slim

RUN useradd --create-home --no-log-init appuser

WORKDIR /app
RUN chown appuser:appuser /app

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

COPY --chown=appuser:appuser pyproject.toml uv.lock ./

USER appuser

RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY --chown=appuser:appuser src ./src
RUN uv sync --frozen --no-dev --no-cache

ENV PATH="/app/.venv/bin:$PATH"
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

ENV PORT=8080
EXPOSE 8080

CMD uvicorn ai_research_backend.api:app --host 0.0.0.0 --port ${PORT}
