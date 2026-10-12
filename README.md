# Command Deck

A small, server-rendered dashboard for homelab health. It reads Prometheus, Healthchecks, and Uptime Kuma; it does not require access to Docker or monitored files.

## What it shows

- **Overview:** up to two selected servers, backup status, active Uptime Kuma monitor count, and the most urgent current issue.
- **Servers:** live CPU and memory, five-minute trends, sampled CPU peak, memory used and total, and configured storage capacity. Choose which server cards appear on Overview with `show_on_overview` in `config.local.yaml` (at most two). Every configured server remains available on the Servers page. Tapping an Overview server opens its details.
- **Events:** current warnings and source failures. Historical failure and recovery events are not connected yet.

Node exporter supplies host and filesystem metrics. A TrueNAS Graphite feed can supply CPU and memory for a NAS. TrueNAS usable storage comes from a separate read-only JSON-RPC connection; storage remains unknown until that connection is configured.

The page refreshes its data without exposing upstream API credentials to the browser. If an already open page loses its connection to Command Deck, it displays a connection warning. A fresh load still requires the dashboard host to be reachable.

## Local development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js, and npm.

```sh
cp config.yaml config.local.yaml
cp .env.example .env
uv sync --group dev
npm install
npm run build:assets
COMMAND_DECK_CONFIG_FILE=config.local.yaml uv run --env-file .env uvicorn command_deck.main:app --reload
```

Open `http://127.0.0.1:8000`. The example configuration has no live scrape targets, so server telemetry stays unknown until you add your own. Run `uv run pytest -q` and `uv run ruff check .` to check changes.

## Configuration

Keep site-specific targets in the ignored `config.local.yaml`, copied from [`config.yaml`](config.yaml). Set the Prometheus URL, host order, each host's Prometheus `instance` label, and the filesystems you want to display. For a TrueNAS Graphite host, set `source: truenas_graphite` and the validated `graphite_prefix` that matches its metric names. Set `show_on_overview: true` on up to two hosts.

Use the ignored `.env` for source URLs and credentials; [`.env.example`](.env.example) lists supported overrides. Do not commit `.env` or `config.local.yaml`, paste keys into YAML, or put keys in a URL. A Healthchecks project-scoped read-only Management API key enables the backup summary. Tag the relevant Healthchecks checks with the `healthchecks.backup_tag` value. Set `COMMAND_DECK_UPTIME_KUMA_URL` to enable the Services summary; set `COMMAND_DECK_UPTIME_KUMA_API_KEY` only if that server's `/metrics` endpoint requires it. Kuma's metrics omit paused monitors, so the denominator represents active monitors. A missing source or failed request is shown as unknown or disconnected, not healthy.

Existing local `.env` values with the former `HCC_` prefix continue to work. If both prefixes are present for a setting, `COMMAND_DECK_` takes precedence. Use the new prefix for future settings.

For TrueNAS SCALE 25.04 or newer, set `COMMAND_DECK_TRUENAS_URL`, `COMMAND_DECK_TRUENAS_USERNAME`, and `COMMAND_DECK_TRUENAS_API_KEY` in `.env`. Use a dedicated account with the read-only `POOL_READ` and `DATASET_READ` roles and a trusted HTTPS certificate. The URL is the HTTPS origin; the app connects to its versioned WebSocket API. Set `truenas.host_id` to the host that this API represents. In that host entry, `pools: []` selects every pool, or list the pool names to display. The app reads pool status plus each root dataset's used and available space. The displayed total is used plus available, which reflects writable capacity rather than raw pool size. Older TrueNAS releases have a different API and are not supported by this collector yet.

The app reads [Healthchecks Management API v3](https://healthchecks.io/docs/api/) and [Uptime Kuma Prometheus metrics](https://github.com/louislam/uptime-kuma/wiki/Prometheus-Integration). The Servers graph requests five minutes of Prometheus history when a server is selected.

## Container deployment

[`compose.yaml`](compose.yaml) builds a non-root container and mounts `config.local.yaml` read-only. Copy the two example configuration files as above, fill in your own source addresses, and validate before starting:

```sh
docker compose config --quiet
docker compose up -d --build
```

The dashboard binds to loopback by default on port 8765. Set `COMMAND_DECK_BIND_ADDRESS` in `.env` to a specific private address if other devices need access. There is no built-in login; protect access at the network or proxy boundary. The container has no Docker socket mount, and `/healthz` reports only process liveness.

The supplied examples contain no deployment addresses or credentials. The standalone design previews in [`design/`](design/) use illustrative values.
