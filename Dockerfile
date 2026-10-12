FROM node:22-bookworm-slim AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY src/command_deck/static/source.css src/command_deck/static/source.css
COPY src/command_deck/templates src/command_deck/templates
COPY design/themes.css design/themes.css
COPY scripts/copy-assets.mjs scripts/copy-assets.mjs
COPY design/fonts/CaskaydiaCoveNerdFont-Regular.ttf design/fonts/CaskaydiaCoveNerdFont-Regular.ttf
COPY design/fonts/LICENSE-CaskaydiaCove.txt design/fonts/LICENSE-CaskaydiaCove.txt
RUN npm run build:assets

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY --from=assets /build/src/command_deck/static ./src/command_deck/static
COPY config.yaml ./config.yaml
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    COMMAND_DECK_CONFIG_FILE=/app/config.yaml
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["uvicorn", "command_deck.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
