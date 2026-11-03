# Build the Python package and capture the source commit.
FROM python:3.13-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/whist
COPY pyproject.toml ./
COPY src ./src
COPY .git ./.git

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir . \
    && git rev-parse --short HEAD > /opt/whist-commit

# Only the installed application and commit identifier reach runtime.
FROM python:3.13-slim

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/whist-commit /etc/whist-commit

RUN useradd --uid 10001 --create-home whist

ENV PATH="/opt/venv/bin:${PATH}" \
    WHIST_HOME=/var/lib/whist \
    PYTHONUNBUFFERED=1

USER whist
EXPOSE 8000

# Preserve the existing WHIST_GIT_COMMIT interface used by the footer.
CMD ["sh", "-c", "export WHIST_GIT_COMMIT=\"$(cat /etc/whist-commit)\"; exec whistapp serve --host 0.0.0.0 --port 8000"]
