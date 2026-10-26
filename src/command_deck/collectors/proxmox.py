"""Read-only Proxmox VE node status, normalized for the existing host views."""

import asyncio
import math
import time
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from command_deck.config import AppConfig, HostConfig
from command_deck.models import DiskView, Health, HostView, Telemetry
from command_deck.status_logging import StatusLog


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _root_disk(data: object, config: AppConfig) -> DiskView:
    missing = DiskView(label="System", mountpoint="/", reason="Root filesystem metrics unavailable")
    if not isinstance(data, dict):
        return missing
    total, used = _number(data.get("total")), _number(data.get("used"))
    # Available excludes filesystem reservations. Older responses may expose only free.
    available = _number(data.get("avail") if "avail" in data else data.get("free"))
    if (total is None or total <= 0 or used is None or used > total
            or available is None or available > total - used):
        return missing
    available_percent = 100 * (available / total)
    health = (
        Health.CRITICAL if available_percent <= config.capacity.critical_available_percent
        else Health.WARNING if available_percent <= config.capacity.warning_available_percent
        else Health.OK
    )
    return DiskView(
        label="System",
        mountpoint="/",
        used_bytes=round(used),
        total_bytes=round(total),
        available_bytes=round(available),
        used_percent=round(100 * (used / total), 1),
        health=health,
        reason=f"Only {available_percent:.1f}% available" if health != Health.OK else None,
    )


class ProxmoxCollector:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        self._cache: tuple[float, dict[str, HostView]] | None = None
        self._lock = asyncio.Lock()
        self._status = StatusLog("Proxmox")
        self._host_status = {
            host.id: StatusLog(f"Proxmox host {host.id}")
            for host in config.hosts if host.source == "proxmox"
        }

    async def collect(self) -> dict[str, HostView]:
        settings = self.config.proxmox
        if self._cache and time.monotonic() - self._cache[0] < settings.cache_seconds:
            return self._cache[1]
        async with self._lock:
            if self._cache and time.monotonic() - self._cache[0] < settings.cache_seconds:
                return self._cache[1]
            hosts = await asyncio.gather(*(
                self._host(host) for host in self.config.hosts if host.source == "proxmox"
            ))
            result = {host.id: host for host in hosts}
            states = [host.telemetry for host in hosts]
            state = (
                Telemetry.NOT_CONFIGURED if not states or all(
                    item == Telemetry.NOT_CONFIGURED for item in states
                ) else Telemetry.CONNECTED if all(item == Telemetry.CONNECTED for item in states)
                else Telemetry.DISCONNECTED if all(
                    item == Telemetry.DISCONNECTED for item in states
                )
                else Telemetry.UNHEALTHY
            )
            self._status.update(state)
            for host in hosts:
                self._host_status[host.id].update(host.telemetry)
            self._cache = (time.monotonic(), result)
            return result

    async def _host(self, host: HostConfig) -> HostView:
        settings = self.config.proxmox

        def unavailable(telemetry: Telemetry, reason: str) -> HostView:
            return HostView(
                id=host.id, name=host.name, show_on_overview=host.show_on_overview,
                telemetry=telemetry, telemetry_source="Proxmox", reason=reason,
            )

        if not settings.enabled or not host.proxmox_node:
            return unavailable(Telemetry.NOT_CONFIGURED, "No Proxmox connection or node configured")
        assert settings.url and settings.token_id and settings.token_secret
        try:
            response = await self.client.get(
                f"{settings.url}/api2/json/nodes/{quote(host.proxmox_node, safe='')}/status",
                headers={"Authorization": (
                    f"PVEAPIToken={settings.token_id}={settings.token_secret.get_secret_value()}"
                )},
                timeout=settings.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                return unavailable(Telemetry.UNHEALTHY, "Proxmox node response is invalid")
        except httpx.RequestError:
            return unavailable(Telemetry.DISCONNECTED, "Proxmox is unavailable")
        except httpx.HTTPStatusError as exc:
            reason = (
                "Proxmox API authentication or permission denied"
                if exc.response.status_code in {401, 403} else "Proxmox node request failed"
            )
            return unavailable(Telemetry.UNHEALTHY, reason)
        except Exception:
            return unavailable(Telemetry.UNHEALTHY, "Proxmox node response is invalid")

        data = payload["data"]
        cpu = _number(data.get("cpu"))
        cpu_percent = round(100 * cpu, 1) if cpu is not None and cpu <= 1 else None
        cpuinfo = data.get("cpuinfo")
        cpus = _number(cpuinfo.get("cpus")) if isinstance(cpuinfo, dict) else None
        logical_cpus = int(cpus) if cpus is not None and cpus >= 1 and cpus.is_integer() else None
        memory = data.get("memory")
        total = _number(memory.get("total")) if isinstance(memory, dict) else None
        used = _number(memory.get("used")) if isinstance(memory, dict) else None
        memory_valid = total is not None and total > 0 and used is not None and used <= total
        disk = _root_disk(data.get("rootfs"), self.config)
        incomplete = cpu_percent is None or not memory_valid or disk.health == Health.UNKNOWN
        health = disk.health if disk.health in {Health.WARNING, Health.CRITICAL} else (
            Health.UNKNOWN if incomplete else Health.OK
        )
        return HostView(
            id=host.id, name=host.name, show_on_overview=host.show_on_overview,
            telemetry=Telemetry.UNHEALTHY if incomplete else Telemetry.CONNECTED,
            telemetry_source="Proxmox",
            health=health, metrics_current=True,
            cpu_percent=cpu_percent, logical_cpu_count=logical_cpus,
            memory_percent=round(100 * (used / total), 1) if memory_valid else None,
            memory_used_bytes=round(used) if memory_valid else None,
            memory_total_bytes=round(total) if memory_valid else None,
            disks=[disk], updated_at=datetime.now(UTC),
            reason="Some Proxmox node metrics are unavailable" if incomplete else None,
        )
