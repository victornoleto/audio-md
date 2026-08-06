FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${GID}" app \
    && useradd -m -u "${UID}" -g "${GID}" -d /home/app app

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
RUN uv sync --frozen --extra transcribe --extra gpu \
    && chown -R app:app /app

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app

ENV CLAUDE_CONFIG_DIR=/home/app/.claude \
    HF_HOME=/home/app/.cache/huggingface \
    PATH=/app/.venv/bin:/home/app/.local/bin:$PATH

RUN curl -fsSL https://claude.ai/install.sh | bash \
    && mkdir -p "$CLAUDE_CONFIG_DIR" "$HF_HOME"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["audio-md-web"]
