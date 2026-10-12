# Command Deck

A small dashboard for homelab health. It reads Prometheus, Healthchecks, Uptime Kuma, and optional TrueNAS data without access to Docker or monitored files.

## Screenshots

| Healthy overview | Overview with an alert |
| --- | --- |
| ![Mock Command Deck overview with healthy hosts](docs/screenshots/overview-healthy.png) | ![Mock Command Deck overview with an active alert](docs/screenshots/overview-alert.png) |

| Server detail | NAS detail |
| --- | --- |
| ![Mock server detail with resource history and disks](docs/screenshots/server.png) | ![Mock NAS detail with resource history and pools](docs/screenshots/nas.png) |

![Command Deck component examples](docs/screenshots/components.png)

*Screenshots use illustrative data, not live hosts.*

## What it shows

- **Overview:** up to two chosen hosts, backup status, active service monitors, and the most urgent current issue.
- **Servers:** current CPU and memory, five-minute trends, sampled CPU peak and average, and storage. Select any configured host for detail.
- **Events:** current warnings and source failures. Failure and recovery history is not connected yet.

Missing or stale data is marked unknown or disconnected; it is never shown as healthy. An open page warns when it loses its connection to Command Deck.

## Run with Docker

Copy the examples and set your source addresses in `config.local.yaml`. Put API keys and optional URL overrides in `.env`. Both files are ignored by Git.

```sh
cp config.yaml config.local.yaml
cp .env.example .env
# Edit config.local.yaml and .env for your sources.
docker compose config --quiet
docker compose up -d --build
```

Open `http://127.0.0.1:8765`. The example configuration has no live scrape targets, so telemetry stays unknown until you configure them. The container runs as a non-root user, mounts its configuration read-only, and does not mount the Docker socket.

## Configure sources

| Source | What to set |
| --- | --- |
| Prometheus | Set its URL, each host's `instance` label, and any filesystems in `config.local.yaml` (copied from [`config.yaml`](config.yaml)). Node exporter supplies CPU, memory, and filesystem values. Set `show_on_overview: true` on at most two hosts. |
| Healthchecks | Tag backup checks with `healthchecks.backup_tag` and set a project-scoped, read-only Management API key in `.env`. |
| Uptime Kuma | Set `COMMAND_DECK_UPTIME_KUMA_URL` in `.env`; add an API key only if `/metrics` requires one. Its count covers active monitors, not paused monitors. |
| TrueNAS | For SCALE 25.04+, set the host's `source` to `truenas_graphite` and configure its `instance` and `graphite_prefix` for CPU and memory. For storage, set the TrueNAS HTTPS origin, username, and API key in `.env`, and `truenas.host_id` in `config.local.yaml`. Use a dedicated account with `POOL_READ` and `DATASET_READ` and a trusted certificate. |

TrueNAS storage shows each root dataset's **available** space. Its displayed total is used plus available, not the raw capacity of the pool's disks. If the API connection is missing, storage stays unknown. Older TrueNAS releases use a different API and are not supported. See [`config.yaml`](config.yaml) and [`.env.example`](.env.example) for all settings. Keep credentials out of YAML and URLs.

The app reads [Healthchecks Management API v3](https://healthchecks.io/docs/api/) and [Uptime Kuma Prometheus metrics](https://github.com/louislam/uptime-kuma/wiki/Prometheus-Integration). The Servers graph fetches five minutes of Prometheus history when a host is selected.

## Develop locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js, and npm. Copy and configure the example files as above, then run:

```sh
uv sync --group dev
npm install
npm run build:assets
COMMAND_DECK_CONFIG_FILE=config.local.yaml uv run --env-file .env uvicorn command_deck.main:app --reload
```

Open `http://127.0.0.1:8000`. Check changes with `uv run pytest -q` and `uv run ruff check .`.

## Deploy on your network

Docker binds to loopback by default. To view the dashboard from another device, set `COMMAND_DECK_BIND_ADDRESS` in `.env` to the host's private address and run `docker compose up -d` again to apply the new port binding. There is no built-in login; restrict access with your network or proxy. `/healthz` checks process liveness only.

## License

Command Deck is licensed under the [GNU AGPLv3](LICENSE). Bundled fonts and htmx retain the licenses included alongside them in `src/command_deck/static`.
