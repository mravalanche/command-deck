# Proxmox VE telemetry

[Configuration guide](guide.md) · [README](../README.md)

Command Deck can read physical Proxmox VE node CPU usage, logical CPU count, RAM usage, and root filesystem capacity directly from the Proxmox API. No exporter or Prometheus server is needed for Proxmox hosts. Existing Prometheus hosts can stay in the same dashboard.

This feature is initially available in source. Build a checkout containing it, or use a development image known to include it; the published `latest` image may not include it yet.

## 1. Prepare read-only access in Proxmox

Perform these administrative steps yourself in the Proxmox web interface. Command Deck does not create accounts, tokens, roles, or permissions.

1. Under **Datacenter → Permissions → Users**, add a dedicated user such as `command-deck@pve`, using the Proxmox VE authentication realm. Keep this account enabled and separate from your administrative account.
2. Under **Datacenter → Permissions → Roles**, create `CommandDeckNodeAudit` with only `Sys.Audit`. The node status endpoint requires that privilege on `/nodes/<node>`. [Official endpoint implementation](https://github.com/proxmox/pve-manager/blob/master/PVE/API2/Nodes.pm)
3. Under **Datacenter → Permissions → API Tokens**, create a token named `telemetry` for that user. Leave **Privilege Separation** enabled. Choose an expiration appropriate for your deployment and arrange to replace the credential before it expires.
4. Record the token ID, `command-deck@pve!telemetry`, and save the displayed secret directly into your local environment file as described below. Do not put the secret in YAML, source control, a URL, or a chat.
5. Under **Datacenter → Permissions**, add a **User Permission** for `command-deck@pve` on `/nodes/pve1` with role `CommandDeckNodeAudit`. Add an **API Token Permission** on the same path for `command-deck@pve!telemetry` with the same role. Propagation can remain disabled for this node status endpoint. Repeat both grants for each selected node.

Privilege-separated tokens receive the intersection of the user's and token's permissions, so granting only one of these ACLs is insufficient. Proxmox explains token permissions and one-time secret display in its [official user management documentation](https://github.com/proxmox/pve-docs/blob/master/pveum.adoc#api-tokens). This setup needs no administrator, guest management, datastore, or power management privileges.

## 2. Configure the nodes

Save this minimal Proxmox-only example as the ignored `config.yml`:

```yaml
proxmox:
  url: https://pve1.example.invalid:8006
  token_id: command-deck@pve!telemetry
  timeout_seconds: 4
  cache_seconds: 30

hosts:
  - id: pve1
    name: Proxmox 1
    source: proxmox
    proxmox_node: pve1
    show_on_overview: true
  - id: pve2
    name: Proxmox 2
    source: proxmox
    proxmox_node: pve2
    show_on_overview: true
```

Replace the URL, names, and node identifiers. `proxmox_node` is the exact node name shown in Proxmox, not its IP address or the friendly dashboard name. Remove the second host if unused. Set additional hosts' `show_on_overview: false`; at most two hosts across all sources can appear on Overview. Every configured node must have its own user and token ACLs.

One endpoint serves all selected nodes in the same cluster, including requests proxied by Proxmox to another node. Independent clusters need separate Command Deck instances for now. The URL must be an HTTPS origin, including `:8006` when using the normal Proxmox API port. Do not append `/api2/json`, credentials, a query, or a fragment. Command Deck constructs `GET /api2/json/nodes/<node>/status` itself and does not follow redirects.

Add the credential to your ignored `.env` file:

```dotenv
COMMAND_DECK_PROXMOX_TOKEN_SECRET=replace-with-the-secret-in-your-local-file
```

That line is a placeholder: enter the real value locally. Optional `COMMAND_DECK_PROXMOX_URL` and `COMMAND_DECK_PROXMOX_TOKEN_ID` override their YAML values. The token ID and secret are separate fields; do not include `PVEAPIToken=` in either. YAML `token_secret` is rejected.

For a mixed dashboard, retain your existing `prometheus` configuration and hosts, then add the `proxmox` settings and hosts above. Proxmox hosts use their built-in root capacity row; do not set `instance`, `filesystems`, Graphite, or pool options on them.

## 3. Trust the HTTPS certificate

The endpoint certificate must be trusted and valid for the hostname in `proxmox.url`. Using an IP address requires a matching IP certificate identity. Allow outbound access from Command Deck to the configured endpoint's HTTPS port; the browser does not contact Proxmox directly.

A certificate trusted by the container's default trust store needs no extra configuration. For a private CA, obtain its public PEM CA certificate bundle through your normal trusted administrative process. Put it at `./certs/proxmox-ca.pem` beside your Compose file and add this read-only mount to the existing service:

```yaml
services:
  command-deck:
    volumes:
      - ./config.yml:/app/config.yaml:ro
      - ./certs/proxmox-ca.pem:/app/certs/proxmox-ca.pem:ro
```

Set the corresponding container path in `config.yml`:

```yaml
proxmox:
  url: https://pve1.example.invalid:8006
  token_id: command-deck@pve!telemetry
  ca_file: /app/certs/proxmox-ca.pem
```

Merge these entries into the existing configuration rather than adding a second `proxmox` key. Ensure the file is readable by container user `10001`. Mount only public certificates, never a CA or node private key. TLS verification stays enabled; there is no insecure verification switch.

## 4. Start the version containing this feature

From a source checkout containing the integration, build a local image:

```sh
docker build -t command-deck:proxmox .
```

In your Compose service, replace `image` with `command-deck:proxmox` and add `pull_policy: never`. Keep `env_file: .env` and the configuration mount. Then recreate the service:

```sh
docker compose config --quiet
docker compose up -d --force-recreate command-deck
docker compose logs -f command-deck
```

Recreate after changing environment values; `docker compose restart` alone does not load a changed `.env`. Configuration is also loaded at startup. For local development, follow the [development instructions](guide.md#develop-locally) with this configuration and environment file.

Open Overview or Servers and select the configured host. Healthy telemetry shows live CPU, RAM, and a **System** capacity row. Connection state changes appear in the container logs without credentials or source URLs.

## Behavior and limits

Command Deck checks sources at startup, then refreshes telemetry when dashboard requests arrive after the shared cache expires. The default cache lasts 30 seconds; it is not a continuous background recording service. Multiple browser requests share cached results. A displayed update time records when Command Deck fetched the response, not a producer timestamp from Proxmox.

This first version reads physical node status only. Root filesystem capacity is not total datastore capacity, ZFS pool health, or guest disk usage. CPU and RAM are node measurements and can differ from measurements inside guests. Missing or invalid metrics remain unknown. An API failure reports unavailable telemetry; it does not prove that the physical node is powered off. A healthy node card does not establish cluster quorum, HA, backup, or guest health. Proxmox history charts, guest inventory, and datastore monitoring are not included.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Not configured | Set the URL, token ID, environment token secret, and host's `proxmox_node`; recreate the service. |
| Authentication or permission denied | Check user/token enabled state, expiration, exact token ID, and both ACLs on every `/nodes/<node>` path. Do not fix this by granting administrator access. |
| Service fails at startup after setting `ca_file` | Check that the mounted file exists, contains PEM CA certificates, and is readable by container user `10001`. An invalid CA file prevents startup. |
| Proxmox unavailable | Check endpoint DNS and network reachability from the container, port, certificate hostname, and CA chain. |
| Node request failed or invalid response | Check the exact node name, cluster connectivity, and that the configured origin reaches the Proxmox JSON API without a redirect or login proxy. |
| Some metrics unavailable | The API returned incomplete or invalid CPU, memory, or root capacity values. Compare the node's Proxmox status and API response locally; do not paste credentials or headers into diagnostics. |
| Changes not visible | Recreate after editing configuration or `.env`, then allow the cache to expire and refresh the dashboard. |

Failure messages are deliberately bounded and do not include server response bodies. `/healthz` checks the Command Deck process, not Proxmox connectivity.

## Future work

The next useful milestones are datastore capacity with explicit storage selection and `Datastore.Audit` grants, then optional guest inventory with scoped `VM.Audit` grants. Historical charts could use Proxmox RRD data or a separate Prometheus exporter. Each addition should define its metric semantics and permission needs before expanding this read-only integration.
