# Configuration and operations

[Back to the README](../README.md) · [Full example YAML](../config.yaml) · [Environment example](../.env.example)

## Configure data sources

Copy `config.yaml` to the ignored `config.yml`, then edit it. `prometheus.url` and at least one host with `id` and `name` are required. A host's `instance` must match its Prometheus `instance` label. Set `show_on_overview: true` on no more than two hosts. The host `name`, node filesystem `label`, and TrueNAS `pool_labels` control text shown on cards. Command Deck includes CaskaydiaCove Nerd Font for glyphs in these labels. TrueNAS `pools` selects pools by raw name; `pool_labels` changes only their display names.

| Source | Setup |
| --- | --- |
| Prometheus | Point `prometheus.url` at your server and set each host's `instance`. Node exporter supplies CPU, memory, and filesystem metrics. List the mountpoints you want to show under `filesystems`; each needs a `label`. If Prometheus needs a bearer token, set `COMMAND_DECK_PROMETHEUS_BEARER_TOKEN` in `.env`. |
| Healthchecks | Tag the relevant checks with `healthchecks.backup_tag` (default `backup`). Set a project-scoped, read-only Management API key as `COMMAND_DECK_HEALTHCHECKS_API_KEY` in `.env`. |
| Uptime Kuma | Set `uptime_kuma.enabled: true` and its `url`, or set `COMMAND_DECK_UPTIME_KUMA_URL` in `.env` (which also enables it). Set `COMMAND_DECK_UPTIME_KUMA_API_KEY` only if `/metrics` requires one. Paused monitors are excluded. |
| TrueNAS SCALE 25.04+ | Set the host's `source: truenas_graphite`, `instance`, and `graphite_prefix` for CPU and memory. Set `truenas.host_id` to that host's ID. For storage, set `COMMAND_DECK_TRUENAS_URL`, `COMMAND_DECK_TRUENAS_USERNAME`, and `COMMAND_DECK_TRUENAS_API_KEY` in `.env`. The URL must be an HTTPS origin with a trusted certificate; use a dedicated account with `POOL_READ` and `DATASET_READ`. |

TrueNAS storage reports each root dataset's available space. Its total is used plus available, which can differ from raw disk capacity. The TrueNAS API is required for these storage values; Graphite alone supplies CPU and memory. Older TrueNAS API versions are unsupported. Keep credentials out of YAML and URLs.

Command Deck marks missing or stale source data unknown or disconnected. The Servers page fetches five minutes of Prometheus history when you select a host. Healthchecks “last success” requires a confirmed completion signal from a currently healthy check.

## Run and access

The [Compose file](../compose.yaml) mounts `./config.yml` read-only at `/app/config.yaml`, reads `.env`, and pulls the published `latest` image. To use another config file or image tag, change its mount or image line directly.

```sh
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose logs -f command-deck
```

The `8000:8000` mapping listens on all host interfaces. To limit it to this machine, change the line to `127.0.0.1:8000:8000`; for a specific LAN interface, put its address first. There is no built-in login, so limit access through your network or proxy. `/healthz` checks process liveness only.

## Develop locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js, and npm. Copy and configure the example files, then run:

```sh
uv sync --group dev
npm install
npm run build:assets
COMMAND_DECK_CONFIG_FILE=config.yml uv run --env-file .env uvicorn command_deck.main:app --reload
```

Open <http://127.0.0.1:8000>. Check changes with `uv run pytest -q` and `uv run ruff check .`.

## Images and releases

Pull requests run version checks, asset compilation, lint, tests, and a container build. Passing pushes to `develop` publish `develop` and `dev-sha-COMMIT_SHA` images for amd64 and arm64. Pushes to `main` publish `latest` and `sha-COMMIT_SHA`; the first build for a version also publishes `vX.Y.Z` and a GitHub Release. To prepare a release, update both package versions on a feature branch:

```sh
uv version 0.2.1 --no-sync
npm version 0.2.1 --no-git-tag-version
```

These update `pyproject.toml`, `uv.lock`, `package.json`, and `package-lock.json`; CI checks that the versions agree. Replace `0.2.1` with the intended release version. GitHub may initially create packages from forks as private, so fork owners who want anonymous pulls must make their published package public.
