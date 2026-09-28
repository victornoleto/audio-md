FROM node:22-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node

ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${GID}" app \
    && useradd -m -u "${UID}" -g "${GID}" -d /home/app app \
    && mkdir -p /app && chown app:app /app

USER app
WORKDIR /app

ENV CLAUDE_CONFIG_DIR=/home/app/.claude \
    HF_HOME=/home/app/.cache/huggingface \
    PATH=/app/.venv/bin:/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

RUN curl -fsSL https://claude.ai/install.sh | bash \
    && mkdir -p "$CLAUDE_CONFIG_DIR" "$HF_HOME"

# Dependencies first (change rarely, cost GBs with the GPU wheels), project last:
# editing src/ only re-runs the final sync, so a rebuild takes seconds.
COPY --chown=app:app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --extra transcribe --extra gpu

COPY --chown=app:app README.md ./
COPY --chown=app:app src ./src
RUN uv sync --frozen --no-dev --extra transcribe --extra gpu

CMD ["audio-md-web"]
