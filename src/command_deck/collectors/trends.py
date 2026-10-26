"""On-demand five-minute CPU and memory history from Prometheus."""

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from command_deck.collectors.prometheus import QUERIES, graphite_queries
from command_deck.config import AppConfig, HostConfig
from command_deck.models import Telemetry
from command_deck.status_logging import StatusLog

MEMORY_QUERY = (
    '100 * (1 - (node_memory_MemAvailable_bytes{job="node_exporter"} '
    '/ on(instance) node_memory_MemTotal_bytes{job="node_exporter"}))'
)
WINDOW_SECONDS = 300
STEP_SECONDS = 15


@dataclass(frozen=True)
class TrendPoint:
    timestamp: float
    percent: float


@dataclass(frozen=True)
class HostTrend:
    telemetry: Telemetry
    cpu: list[TrendPoint]
    memory: list[TrendPoint]
    start: float
    end: float
    updated_at: datetime | None = None
    reason: str | None = None

    @property
    def has_data(self) -> bool:
        return bool(self.cpu or self.memory)


def _parse_matrix(payload: object, instance: str) -> list[TrendPoint]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Unsuccessful Prometheus range response")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "matrix":
        raise ValueError("Expected Prometheus matrix")
    rows = data.get("result")
    if not isinstance(rows, list):
        raise ValueError("Missing Prometheus series")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("metric"), dict)
        and row["metric"].get("instance") == instance
    ]
    if len(matches) != 1:
        return []
    raw_points = matches[0].get("values")
    if not isinstance(raw_points, list):
        raise ValueError("Missing Prometheus range values")
    points: list[TrendPoint] = []
    for raw in raw_points:
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError("Malformed Prometheus range sample")
        try:
            timestamp = float(raw[0])
            percent = float(raw[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Non-numeric Prometheus range sample") from exc
        if math.isfinite(timestamp) and math.isfinite(percent) and 0 <= percent <= 100:
            points.append(TrendPoint(timestamp=timestamp, percent=percent))
    return sorted(points, key=lambda point: point.timestamp)


class TrendCollector:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient):
        self.config = config.prometheus
        self.client = client
        self._cache: dict[str, tuple[float, HostTrend]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._status: dict[str, StatusLog] = {}

    async def collect(self, host: HostConfig) -> HostTrend:
        now = time.time()
        if host.source == "proxmox":
            return HostTrend(
                telemetry=Telemetry.NOT_CONFIGURED, cpu=[], memory=[],
                start=now - WINDOW_SECONDS, end=now,
                reason="Five-minute history is not available for Proxmox nodes",
            )
        if host.instance is None:
            return HostTrend(
                telemetry=Telemetry.NOT_CONFIGURED,
                cpu=[],
                memory=[],
                start=now - WINDOW_SECONDS,
                end=now,
                reason="No Prometheus target is configured",
            )
        cached = self._cache.get(host.id)
        if cached and time.monotonic() - cached[0] < self.config.cache_seconds:
            return cached[1]
        async with self._locks.setdefault(host.id, asyncio.Lock()):
            cached = self._cache.get(host.id)
            if cached and time.monotonic() - cached[0] < self.config.cache_seconds:
                return cached[1]
            result = await self._refresh(host, now)
            self._cache[host.id] = (time.monotonic(), result)
            self._status.setdefault(host.id, StatusLog(f"Prometheus trend {host.id}")).update(
                result.telemetry
            )
            return result

    async def _query(self, expression: str, start: float, end: float) -> object:
        headers: dict[str, str] = {}
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token.get_secret_value()}"
        response = await self.client.get(
            f"{self.config.url}/api/v1/query_range",
            params={
                "query": expression,
                "start": start,
                "end": end,
                "step": f"{STEP_SECONDS}s",
                "timeout": f"{self.config.timeout_seconds:g}s",
                "lookback_delta": f"{self.config.max_sample_age_seconds:g}s",
            },
            headers=headers,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    async def _refresh(self, host: HostConfig, end: float) -> HostTrend:
        start = end - WINDOW_SECONDS
        if host.source == "truenas_graphite":
            graphite = graphite_queries(host)
            cpu_query = graphite["cpu"]
            memory_query = (
                f'100 * (1 - ({graphite["memory_available"]} '
                f'/ on(instance) {graphite["memory_total"]}))'
            )
        else:
            cpu_query = QUERIES["cpu"]
            memory_query = MEMORY_QUERY
        raw = await asyncio.gather(
            self._query(cpu_query, start, end),
            self._query(memory_query, start, end),
            return_exceptions=True,
        )
        points: list[list[TrendPoint]] = []
        failures: list[BaseException] = []
        for item in raw:
            if isinstance(item, BaseException):
                failures.append(item)
                points.append([])
                continue
            try:
                points.append(_parse_matrix(item, host.instance or ""))
            except ValueError as exc:
                failures.append(exc)
                points.append([])
        latest_by_series = [series[-1].timestamp if series else None for series in points]
        latest = max(
            (timestamp for timestamp in latest_by_series if timestamp is not None), default=None
        )
        if latest is None:
            state = (
                Telemetry.DISCONNECTED
                if failures and all(isinstance(exc, httpx.RequestError) for exc in failures)
                else Telemetry.UNHEALTHY
            )
            return HostTrend(
                telemetry=state,
                cpu=[],
                memory=[],
                start=start,
                end=end,
                reason="Five-minute trend is unavailable",
            )
        if end - latest > self.config.max_sample_age_seconds:
            return HostTrend(
                telemetry=Telemetry.UNHEALTHY,
                cpu=[],
                memory=[],
                start=start,
                end=end,
                updated_at=datetime.fromtimestamp(latest, UTC),
                reason="Trend data is stale",
            )
        stale_series = any(
            timestamp is None
            or end - timestamp < -5
            or end - timestamp > self.config.max_sample_age_seconds
            for timestamp in latest_by_series
        )
        state = Telemetry.UNHEALTHY if failures or stale_series else Telemetry.CONNECTED
        return HostTrend(
            telemetry=state,
            cpu=points[0],
            memory=points[1],
            start=start,
            end=end,
            updated_at=datetime.fromtimestamp(latest, UTC),
            reason="Some trend data is unavailable or stale"
            if state == Telemetry.UNHEALTHY
            else None,
        )
