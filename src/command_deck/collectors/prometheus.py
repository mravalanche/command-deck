"""Read-only Prometheus collector for node exporter and TrueNAS Graphite metrics."""

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from command_deck.config import AppConfig, HostConfig
from command_deck.models import DiskView, Health, HostView, Telemetry
from command_deck.status_logging import StatusLog

# Queries are fixed in the application. Browser input and host labels never become PromQL.
QUERIES = {
    "up": 'up{job="node_exporter"}',
    "up_timestamp": 'timestamp(up{job="node_exporter"})',
    "cpu": (
        "100 * (1 - avg by (instance) "
        '(rate(node_cpu_seconds_total{job="node_exporter",mode="idle"}[5m])))'
    ),
    "cpu_count": 'count by (instance) (node_cpu_seconds_total{job="node_exporter",mode="idle"})',
    "memory_total": 'node_memory_MemTotal_bytes{job="node_exporter"}',
    "memory_available": 'node_memory_MemAvailable_bytes{job="node_exporter"}',
    "filesystem_size": 'node_filesystem_size_bytes{job="node_exporter"}',
    "filesystem_free": 'node_filesystem_free_bytes{job="node_exporter"}',
    "filesystem_available": 'node_filesystem_avail_bytes{job="node_exporter"}',
}

def graphite_queries(host: HostConfig) -> dict[str, str]:
    """Build fixed TrueNAS chart names from a validated Graphite namespace."""
    assert host.graphite_prefix is not None
    prefix = host.graphite_prefix
    selector = '{job="graphite_exporter"}'
    return {
        "up": 'up{job="graphite_exporter"}',
        "last_processed": 'graphite_last_processed_timestamp_seconds{job="graphite_exporter"}',
        "cpu": f"{prefix}_truenas_cpu_usage_cpu_cpu{selector}",
        "memory_total": f"{prefix}_truenas_meminfo_total_total{selector}",
        "memory_available": f"{prefix}_truenas_meminfo_available_available{selector}",
    }


class PrometheusProtocolError(Exception):
    """A successful HTTP response did not contain a usable Prometheus vector."""


@dataclass(frozen=True)
class Sample:
    labels: dict[str, str]
    value: float


@dataclass(frozen=True)
class QueryData:
    samples: list[Sample]
    warned: bool = False


@dataclass(frozen=True)
class PrometheusCollection:
    source_state: Telemetry
    hosts: list[HostView]
    collected_at: datetime


def _parse_vector(payload: object) -> QueryData:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise PrometheusProtocolError("Unsuccessful Prometheus response")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        raise PrometheusProtocolError("Expected a Prometheus instant vector")
    rows = data.get("result")
    if not isinstance(rows, list):
        raise PrometheusProtocolError("Missing Prometheus result list")
    samples: list[Sample] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("metric"), dict):
            raise PrometheusProtocolError("Malformed Prometheus series")
        value = row.get("value")
        if not isinstance(value, list) or len(value) != 2:
            raise PrometheusProtocolError("Malformed Prometheus sample")
        try:
            number = float(value[1])
        except (TypeError, ValueError) as exc:
            raise PrometheusProtocolError("Non-numeric Prometheus sample") from exc
        if not math.isfinite(number):
            continue
        labels = row["metric"]
        if not all(isinstance(key, str) and isinstance(item, str) for key, item in labels.items()):
            raise PrometheusProtocolError("Malformed Prometheus labels")
        samples.append(Sample(labels=labels, value=number))
    return QueryData(samples=samples, warned=bool(payload.get("warnings")))


def _by_instance(data: QueryData | None, instance: str) -> float | None:
    if data is None:
        return None
    matches = [sample.value for sample in data.samples if sample.labels.get("instance") == instance]
    return matches[0] if len(matches) == 1 else None


def _by_mount(data: QueryData | None, instance: str, mountpoint: str) -> float | None:
    if data is None:
        return None
    matches = [
        sample.value
        for sample in data.samples
        if sample.labels.get("instance") == instance
        and sample.labels.get("mountpoint") == mountpoint
    ]
    return matches[0] if len(matches) == 1 else None


def _percent(value: float | None) -> float | None:
    if value is None or not 0 <= value <= 100:
        return None
    return round(value, 1)


class PrometheusCollector:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client
        self._lock = asyncio.Lock()
        self._cached: PrometheusCollection | None = None
        self._cached_at = 0.0
        self._status = StatusLog("Prometheus")
        self._host_status = {
            host.id: StatusLog(f"Prometheus host {host.id}") for host in config.hosts
        }

    async def collect(self) -> PrometheusCollection:
        """Collect once per short cache window, sharing a refresh across callers."""
        if (
            self._cached
            and time.monotonic() - self._cached_at < self.config.prometheus.cache_seconds
        ):
            return self._cached
        async with self._lock:
            if (
                self._cached
                and time.monotonic() - self._cached_at < self.config.prometheus.cache_seconds
            ):
                return self._cached
            result = await self._refresh()
            self._cached = result
            self._cached_at = time.monotonic()
            return result

    async def _query(self, expression: str) -> QueryData:
        settings = self.config.prometheus
        headers: dict[str, str] = {}
        if settings.bearer_token:
            headers["Authorization"] = f"Bearer {settings.bearer_token.get_secret_value()}"
        response = await self.client.get(
            f"{settings.url}/api/v1/query",
            params={
                "query": expression,
                "timeout": f"{settings.timeout_seconds:g}s",
                "lookback_delta": f"{settings.max_sample_age_seconds:g}s",
            },
            headers=headers,
            timeout=settings.timeout_seconds,
        )
        response.raise_for_status()
        try:
            return _parse_vector(response.json())
        except ValueError as exc:
            raise PrometheusProtocolError("Invalid Prometheus JSON") from exc

    async def _refresh(self) -> PrometheusCollection:
        queries = dict(QUERIES)
        graphite_hosts = [
            host for host in self.config.hosts
            if host.source == "truenas_graphite" and host.instance
        ]
        for host in graphite_hosts:
            queries.update(
                {f"{host.id}_{key}": value for key, value in graphite_queries(host).items()}
            )
        results = await asyncio.gather(
            *(self._query(expression) for expression in queries.values()),
            return_exceptions=True,
        )
        values: dict[str, QueryData | None] = {}
        failures: dict[str, BaseException] = {}
        for key, result in zip(queries, results, strict=True):
            if isinstance(result, BaseException):
                values[key] = None
                failures[key] = result
            else:
                values[key] = result
        def state_for(keys: list[str]) -> Telemetry:
            errors = [failures[key] for key in keys if key in failures]
            if len(errors) == len(keys) and all(
                isinstance(error, httpx.RequestError) for error in errors
            ):
                return Telemetry.DISCONNECTED
            if errors or any(values[key].warned for key in keys if values[key] is not None):
                return Telemetry.UNHEALTHY
            return Telemetry.CONNECTED

        # CPU count is optional metadata, not a signal of telemetry health.
        node_state = state_for([key for key in QUERIES if key != "cpu_count"])
        graphite_states = {
            host.id: state_for(list(f"{host.id}_{key}" for key in graphite_queries(host)))
            for host in graphite_hosts
        }
        source_states = [node_state, *graphite_states.values()]
        source_state = (
            Telemetry.DISCONNECTED
            if all(state == Telemetry.DISCONNECTED for state in source_states)
            else Telemetry.UNHEALTHY
            if any(state != Telemetry.CONNECTED for state in source_states)
            else Telemetry.CONNECTED
        )
        now = datetime.now(UTC)
        hosts = [
            self._host(
                host,
                values,
                graphite_states.get(host.id, Telemetry.CONNECTED)
                if host.source == "truenas_graphite" else node_state,
                now,
            ).model_copy(update={"show_on_overview": host.show_on_overview})
            for host in self.config.hosts
        ]
        self._status.update(source_state)
        for host in hosts:
            self._host_status[host.id].update(host.telemetry)
        return PrometheusCollection(source_state=source_state, hosts=hosts, collected_at=now)

    def _host(
        self,
        config: HostConfig,
        values: dict[str, QueryData | None],
        source_state: Telemetry,
        now: datetime,
    ) -> HostView:
        if config.instance is None:
            return HostView(
                id=config.id,
                name=config.name,
                telemetry=Telemetry.NOT_CONFIGURED,
                reason="No Prometheus target is configured",
            )
        if source_state == Telemetry.DISCONNECTED:
            return HostView(
                id=config.id,
                name=config.name,
                telemetry=Telemetry.DISCONNECTED,
                reason="Prometheus is unavailable",
            )
        instance = config.instance
        graphite = config.source == "truenas_graphite"
        prefix = f"{config.id}_" if graphite else ""
        up = _by_instance(values[prefix + "up"], instance)
        stamp = _by_instance(
            values[prefix + "last_processed" if graphite else "up_timestamp"], instance
        )
        try:
            observed_at = datetime.fromtimestamp(stamp, tz=UTC) if stamp is not None else None
        except (OverflowError, ValueError):
            observed_at = None
        if up is None:
            return HostView(
                id=config.id,
                name=config.name,
                telemetry=Telemetry.UNHEALTHY,
                reason="No current scrape result for this configured target",
            )
        if up == 0:
            return HostView(
                id=config.id,
                name=config.name,
                telemetry=Telemetry.DISCONNECTED,
                updated_at=observed_at,
                reason="Prometheus scrape is failing",
            )
        if up != 1 or observed_at is None:
            return HostView(
                id=config.id,
                name=config.name,
                telemetry=Telemetry.UNHEALTHY,
                reason="Source freshness cannot be verified",
            )
        age = (now - observed_at).total_seconds()
        if age < -5 or age > self.config.prometheus.max_sample_age_seconds:
            return HostView(
                id=config.id,
                name=config.name,
                telemetry=Telemetry.UNHEALTHY,
                updated_at=observed_at,
                reason="Source data is stale",
            )

        cpu = _percent(_by_instance(values[prefix + "cpu"], instance))
        raw_cpu_count = None if graphite else _by_instance(values["cpu_count"], instance)
        cpu_count = (
            int(raw_cpu_count)
            if raw_cpu_count is not None and raw_cpu_count >= 1 and raw_cpu_count.is_integer()
            else None
        )
        total = _by_instance(values[prefix + "memory_total"], instance)
        available = _by_instance(values[prefix + "memory_available"], instance)
        memory = (
            _percent(100 * (1 - available / total))
            if total is not None and total > 0 and available is not None and 0 <= available <= total
            else None
        )
        disks = (
            []
            if graphite
            else [
                self._disk(instance, filesystem.mountpoint, filesystem.label, values)
                for filesystem in config.filesystems
            ]
        )
        incomplete = (
            cpu is None or memory is None or any(disk.health == Health.UNKNOWN for disk in disks)
        )
        if any(disk.health == Health.CRITICAL for disk in disks):
            health = Health.CRITICAL
        elif any(disk.health == Health.WARNING for disk in disks):
            health = Health.WARNING
        elif incomplete:
            health = Health.UNKNOWN
        else:
            health = Health.OK
        return HostView(
            id=config.id,
            name=config.name,
            metrics_current=True,
            telemetry=Telemetry.UNHEALTHY
            if incomplete or source_state == Telemetry.UNHEALTHY
            else Telemetry.CONNECTED,
            health=health,
            cpu_percent=cpu,
            logical_cpu_count=cpu_count,
            memory_percent=memory,
            memory_used_bytes=round(total - available) if memory is not None else None,
            memory_total_bytes=round(total) if memory is not None else None,
            disks=disks,
            updated_at=observed_at,
            reason="Some host metrics are unavailable" if incomplete else None,
        )

    def _disk(
        self,
        instance: str,
        mountpoint: str,
        label: str,
        values: dict[str, QueryData | None],
    ) -> DiskView:
        size = _by_mount(values["filesystem_size"], instance, mountpoint)
        free = _by_mount(values["filesystem_free"], instance, mountpoint)
        available = _by_mount(values["filesystem_available"], instance, mountpoint)
        if (
            size is None
            or size <= 0
            or free is None
            or not 0 <= free <= size
            or available is None
            or not 0 <= available <= free
        ):
            return DiskView(
                label=label, mountpoint=mountpoint, reason="Filesystem metrics unavailable"
            )
        available_percent = 100 * available / size
        if available_percent <= self.config.capacity.critical_available_percent:
            health = Health.CRITICAL
        elif available_percent <= self.config.capacity.warning_available_percent:
            health = Health.WARNING
        else:
            health = Health.OK
        reason = (
            f"Only {available_percent:.1f}% available to applications"
            if health != Health.OK
            else None
        )
        return DiskView(
            label=label,
            mountpoint=mountpoint,
            used_bytes=round(size - free),
            total_bytes=round(size),
            available_bytes=round(available),
            used_percent=round(100 * (size - free) / size, 1),
            health=health,
            reason=reason,
        )
