# Configuration and operations

[Back to the README](../README.md) · [Full example YAML](../config.yaml) · [Environment example](../.env.example)

## Configure data sources

Copy `config.yaml` to the ignored `config.yml`, then edit it. At least one host with `id` and `name` is required. Configure `prometheus.url` for Prometheus sources; a Proxmox-only setup can omit it. A host's `instance` must match its Prometheus `instance` label. Set `show_on_overview: true` on no more than two hosts. The host `name`, node filesystem `label`, and TrueNAS `pool_labels` control text shown on cards. Storage rows use a database Nerd Font icon by default on both Overview and Servers. Set `icon` on a filesystem or use `pool_icons` keyed by raw pool name to choose another glyph. Quote glyphs in YAML. Remove glyphs from existing storage labels when migrating, or they will appear beside the new icon. Command Deck includes CaskaydiaCove Nerd Font for these glyphs. TrueNAS `pools` selects pools by raw name; `pool_labels` and `pool_icons` change only their display.

| Source | Setup |
| --- | --- |
| Proxmox VE | Native physical node CPU, RAM, and root filesystem capacity. See the [Proxmox setup guide](proxmox.md) for read-only API token permissions, TLS, and configuration. |
| Prometheus | Point `prometheus.url` at your server and set each host's `instance`. Node exporter supplies CPU, memory, and filesystem metrics. List the mountpoints you want to show under `filesystems`; each needs a `label`. If Prometheus needs a bearer token, set `COMMAND_DECK_PROMETHEUS_BEARER_TOKEN` in `.env`. |
| Healthchecks | Tag the relevant checks with `healthchecks.backup_tag` (default `backup`). Set a project-scoped, read-only Management API key as `COMMAND_DECK_HEALTHCHECKS_API_KEY` in `.env`. |
| Uptime Kuma | Set `uptime_kuma.enabled: true` and its `url`, or set `COMMAND_DECK_UPTIME_KUMA_URL` in `.env` (which also enables it). Set `COMMAND_DECK_UPTIME_KUMA_API_KEY` only if `/metrics` requires one. Paused monitors are excluded. |
| TrueNAS SCALE 25.04+ | Set the host's `source: truenas_graphite`, `instance`, and `graphite_prefix` for CPU and memory. Set `truenas.host_id` to that host's ID. For storage, set `COMMAND_DECK_TRUENAS_URL`, `COMMAND_DECK_TRUENAS_USERNAME`, and `COMMAND_DECK_TRUENAS_API_KEY` in `.env`. The URL must be an HTTPS origin with a trusted certificate; use a dedicated account with `POOL_READ` and `DATASET_READ`. |

TrueNAS storage reports each root dataset's available space. Its total is used plus available, which can differ from raw disk capacity. The TrueNAS API is required for these storage values; Graphite alone supplies CPU and memory. Older TrueNAS API versions are unsupported. Keep credentials out of YAML and URLs.

Command Deck marks missing or stale source data unknown or disconnected. The Servers page fetches five minutes of Prometheus history when you select a Prometheus host. Proxmox hosts explicitly show that history is unavailable. Healthchecks “last success” requires a confirmed completion signal from a currently healthy check.

## Run and access

The [Compose file](../compose.yaml) mounts `./config.yml` read-only at `/app/config.yaml`, reads `.env`, and pulls the published `latest` image. To use another config file or image tag, change its mount or image line directly.

```sh
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose logs -f command-deck
```

The `8000:8000` mapping listens on all host interfaces. To limit it to this machine, change the line to `127.0.0.1:8000:8000`; for a specific LAN interface, put its address first. There is no built-in login, so limit access through your network or proxy. `/healthz` checks process liveness only.

### Add to a home screen

On iPhone or iPad, open Command Deck in Safari, then use Share → Add to Home Screen. On Android, open it in a supporting browser and use its Install app or Add to Home screen option. The installed app uses the same live dashboard and needs a connection to Command Deck; it does not cache monitoring data for offline use. Browser installation on Android generally requires HTTPS, or `localhost` for local development.

## Container logs

Run `docker compose logs -f command-deck` to follow stdout. At startup, Command Deck checks its configured sources in the background and logs the first connection state for each source and host. Later state changes, including recovery, are logged when a page request refreshes the source; unchanged failures do not repeat. Disabled integrations are marked not configured. These status messages contain source names and configured host IDs, but no credentials, source URLs, or monitor names. Routine HTTP access logs stay off.

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
