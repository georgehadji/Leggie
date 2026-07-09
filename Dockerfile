# ── Build stage ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install dependencies only (layer caching)
COPY pyproject.toml setup.py ./
RUN pip install --no-cache-dir -e ".[dev]" && \
    pip uninstall -y leggie

# ── Runtime stage ────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy application source
COPY leggie/ ./leggie/
COPY config/ ./config/
COPY tests/ ./tests/
COPY README.md ./

# Install leggie in editable mode
RUN pip install -e . --no-deps -q

ENV PYTHONUNBUFFERED=1
ENV LEGGIE_LOG_LEVEL=INFO

ENTRYPOINT ["leggie"]
CMD ["--help"]
