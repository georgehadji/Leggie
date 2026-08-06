# ── Build stage ──────────────────────────────────────────────────
FROM python:3.12-slim@sha256:829dd7cd37a5e64eaea744a246cf9fe31484a16e83e50a2e594ec8a0353e87b5 AS builder
WORKDIR /app

# System deps for pdfplumber / lxml
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    build-essential libffi-dev libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel pip-tools

# Install runtime dependencies only from lockfile
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ── Runtime stage ────────────────────────────────────────────────
FROM python:3.12-slim@sha256:829dd7cd37a5e64eaea744a246cf9fe31484a16e83e50a2e594ec8a0353e87b5
WORKDIR /app

# Runtime system deps
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy application source (no tests, no dev config)
COPY leggie/ ./leggie/
COPY config/ ./config/
COPY README.md ./

# Non-editable install from source (no dev deps)
RUN pip install -e . --no-deps -q && \
    # Verify the entry point resolves
    python -c "from leggie.interfaces.cli import entry_point"

# Non-root user for security
RUN addgroup --system leggie && adduser --system --ingroup leggie leggie
USER leggie

ENV PYTHONUNBUFFERED=1
ENV LEGGIE_LOG_LEVEL=INFO

# OCI labels
LABEL org.opencontainers.image.title="Leggie"
LABEL org.opencontainers.image.description="Greek legal bill analyzer"
LABEL org.opencontainers.image.licenses="MIT"

VOLUME ["/app/Outputs"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import leggie; print('ok')" || exit 1

ENTRYPOINT ["leggie"]
CMD ["--help"]
