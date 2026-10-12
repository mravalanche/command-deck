"""Read-only TrueNAS pool capacity via the versioned JSON-RPC WebSocket API."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from websockets.asyncio.client import connect

from command_deck.config import AppConfig, HostConfig
from command_deck.models import DiskView, Health, HostView, Telemetry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoolCollection:
    disks: list[DiskView]
    reason: str | None = None


def _dataset_bytes(dataset: object, property_name: str) -> int | None:
    if not isinstance(dataset, dict) or not isinstance(dataset.get(property_name), dict):
        return None
    value = dataset[property_name].get("parsed")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _pool_disk(pool: object, dataset: object, config: AppConfig) -> DiskView | None:
    if not isinstance(pool, dict) or not isinstance(pool.get("name"), str):
        return None
    name = pool["name"]
    status = pool.get("status")
    if pool.get("healthy") is False or status == "FAULTED":
        health, reason = Health.CRITICAL, f"Pool status: {status or 'unhealthy'}"
    elif pool.get("warning") is True or status == "DEGRADED":
        health, reason = Health.WARNING, f"Pool status: {status or 'warning'}"
    elif pool.get("healthy") is not True or pool.get("warning") is not False or status != "ONLINE":
        health, reason = Health.UNKNOWN, "Pool status unavailable"
    else:
        health, reason = Health.OK, None
    used = _dataset_bytes(dataset, "used")
    available = _dataset_bytes(dataset, "available")
    if used is None or available is None or used + available <= 0:
        return DiskView(label=name, mountpoint=f"pool:{name}",
                        health=health if health in {Health.CRITICAL, Health.WARNING}
                        else Health.UNKNOWN,
                        reason="; ".join(filter(None, [reason, "Pool capacity unavailable"])))
    # The root dataset's available property reflects writable space; pool.free
    # includes space that ZFS cannot offer to datasets (parity, slop, etc.).
    total = used + available
    available_percent = 100 * available / total
    if health == Health.OK and available_percent <= config.capacity.critical_available_percent:
        health, reason = Health.CRITICAL, f"Only {available_percent:.1f}% free"
    elif health == Health.OK and available_percent <= config.capacity.warning_available_percent:
        health, reason = Health.WARNING, f"Only {available_percent:.1f}% free"
    return DiskView(
        label=name,
        mountpoint=f"pool:{name}",
        used_bytes=used,
        total_bytes=total,
        available_bytes=available,
        used_percent=round(100 * used / total, 1),
        health=health,
        reason=reason,
    )


async def _rpc(socket, request_id: int, method: str, params: list, timeout: float) -> object:
    await socket.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method,
                                  "params": params}))
    async with asyncio.timeout(timeout):
        while True:
            response = json.loads(await socket.recv())
            if not isinstance(response, dict) or response.get("id") != request_id:
                continue  # Ignore unrelated notifications.
            if response.get("error") is not None or "result" not in response:
                raise ValueError(f"TrueNAS {method} failed")
            return response["result"]


class TrueNASPoolCollector:
    def __init__(self, config: AppConfig):
        self.config = config
        self._cache: tuple[float, PoolCollection] | None = None
        self._lock = asyncio.Lock()

    async def collect(self) -> PoolCollection | None:
        settings = self.config.truenas
        if not settings.enabled:
            return None
        if self._cache and time.monotonic() - self._cache[0] < settings.cache_seconds:
            return self._cache[1]
        async with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < settings.cache_seconds:
                return self._cache[1]
            try:
                result = await self._refresh()
            except Exception as exc:
                logger.warning("TrueNAS pool query failed (%s)", type(exc).__name__)
                result = PoolCollection([], "TrueNAS pool data unavailable")
            self._cache = (time.monotonic(), result)
            return result

    async def _refresh(self) -> PoolCollection:
        settings = self.config.truenas
        assert settings.url and settings.username and settings.api_key
        websocket_url = "wss://" + settings.url.removeprefix("https://") + "/api/current"
        async with connect(websocket_url, open_timeout=settings.timeout_seconds,
                           close_timeout=1, max_size=1024 * 1024) as socket:
            login = await _rpc(socket, 1, "auth.login_ex", [{"mechanism": "API_KEY_PLAIN",
                "username": settings.username, "api_key": settings.api_key.get_secret_value(),
                "login_options": {"user_info": False}}], settings.timeout_seconds)
            if not isinstance(login, dict) or login.get("response_type") != "SUCCESS":
                raise ValueError("TrueNAS API authentication failed")
            rows = await _rpc(socket, 2, "pool.query", [[], {"select": ["name", "healthy",
                "warning", "status"]}], settings.timeout_seconds)
            try:
                datasets = await _rpc(socket, 3, "pool.dataset.query", [[], {"select": ["id",
                    "used", "available"], "extra": {"flat": False, "retrieve_children": False,
                    "properties": ["used", "available"], "retrieve_user_props": False}}],
                    settings.timeout_seconds)
            except Exception as exc:
                logger.warning("TrueNAS dataset query failed (%s)", type(exc).__name__)
                datasets = None
        if not isinstance(rows, list):
            raise ValueError("TrueNAS pool response is not a list")
        dataset_failure = not isinstance(datasets, list)
        if dataset_failure:
            datasets = []
        by_id = {row["id"]: row for row in datasets
                 if isinstance(row, dict) and isinstance(row.get("id"), str)}
        disks = [disk for row in rows if (disk := _pool_disk(
            row, by_id.get(row.get("name")) if isinstance(row, dict) else None,
            self.config)) is not None]
        return PoolCollection(disks, "No pools returned" if not disks else
                              "TrueNAS dataset capacity unavailable" if dataset_failure else None)


def apply_pool_data(host: HostView, host_config: HostConfig,
                    pools: PoolCollection | None) -> HostView:
    if host_config.source != "truenas_graphite" or pools is None:
        return host
    disks = [disk for disk in pools.disks if not host_config.pools
             or disk.label in host_config.pools]
    found = {disk.label for disk in disks}
    disks += [DiskView(label=name, mountpoint=f"pool:{name}", reason="Pool not found")
              for name in host_config.pools if name not in found]
    if pools.reason and not disks:
        disks = [DiskView(label="Pools", mountpoint="pool:", reason=pools.reason)]
    degraded = pools.reason is not None or any(disk.health == Health.UNKNOWN for disk in disks)
    pool_health = (
        Health.CRITICAL if any(disk.health == Health.CRITICAL for disk in disks)
        else Health.WARNING if any(disk.health == Health.WARNING for disk in disks)
        else Health.UNKNOWN if degraded else Health.OK
    )
    ranks = {Health.OK: 0, Health.UNKNOWN: 1, Health.WARNING: 2, Health.CRITICAL: 3}
    health = max((host.health, pool_health), key=ranks.__getitem__)
    return host.model_copy(update={
        "disks": disks,
        "health": health,
        "telemetry": Telemetry.UNHEALTHY if degraded and host.telemetry == Telemetry.CONNECTED
                     else host.telemetry,
        "reason": "; ".join(filter(None, [host.reason, pools.reason or
                           ("Some pool metrics are unavailable" if degraded else None)])) or None,
    })
