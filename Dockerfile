# Single-stage: the app has four runtime dependencies and no build step.
FROM python:3.12-slim

# uv is pinned. A floating installer would make the image non-reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a source change does not invalidate the layer.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# One worker, deliberately. Inventory and its locks live in process memory, so a
# second worker would hold a second copy of both and overselling would return.
# See the Limitations section of README.md.
CMD ["uvicorn", "inventory.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
