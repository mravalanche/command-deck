"""Bounded local history of actual host readings, shared by every browser."""

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from command_deck.config import AppConfig, HostConfig
from command_deck.models import HostView, Telemetry

WINDOW_SECONDS = 600
STEP_SECONDS = 10
logger = logging.getLogger(__name__)


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
    interval_seconds: float = STEP_SECONDS

    @property
    def has_data(self) -> bool:
        return bool(self.cpu or self.memory)


class TrendCollector:
    def __init__(self, config: AppConfig):
        self.config = config.history
        limit = math.ceil(self.config.window_seconds / self.config.interval_seconds) + 1
        self._points = {
            host.id: (deque(maxlen=limit), deque(maxlen=limit)) for host in config.hosts
        }
        self._states: dict[str, Telemetry] = {}
        # Polling cadence and source freshness are different: a healthy Prometheus
        # target may scrape less often than we poll it. Match its configured policy.
        self._max_sample_age = {
            host.id: (
                max(30, self.config.interval_seconds * 2.5)
                if host.source == "proxmox"
                else config.prometheus.max_sample_age_seconds
            )
            for host in config.hosts
        }

    def _prune(self, now: float) -> None:
        for series in self._points.values():
            for points in series:
                while points and points[0].timestamp < now - self.config.window_seconds:
                    points.popleft()

    def record(self, hosts: list[HostView], *, now: float | None = None) -> None:
        """Called only by the sampler, never by reads of the history cache."""
        now = time.time() if now is None else now
        self._prune(now)
        for host in hosts:
            if host.id not in self._points:
                continue
            self._states[host.id] = host.telemetry
            if not host.metrics_current:
                continue
            for points, value, observed in zip(
                self._points[host.id],
                (host.cpu_percent, host.memory_percent),
                (host.cpu_updated_at, host.memory_updated_at),
                strict=True,
            ):
                if value is None or not math.isfinite(value) or not 0 <= value <= 100:
                    continue
                if observed is None:
                    continue
                stamp = observed.timestamp()
                if (
                    not math.isfinite(stamp)
                    or not 0 <= now - stamp <= self._max_sample_age[host.id]
                ):
                    continue
                # A repeated scrape or cached reading must never invent new detail.
                if points and stamp <= points[-1].timestamp:
                    continue
                points.append(TrendPoint(stamp, value))

    async def collect(self, host: HostConfig) -> HostTrend:
        now = time.time()
        self._prune(now)
        cpu, memory = (list(points) for points in self._points[host.id])
        state = self._states.get(host.id, Telemetry.NOT_CONFIGURED)
        latest = max((points[-1].timestamp for points in (cpu, memory) if points), default=None)
        max_age = self._max_sample_age[host.id]
        reason = None
        if latest is None:
            reason = ("History is warming up" if state == Telemetry.CONNECTED else
                      "No local history available yet")
        elif state in {Telemetry.DISCONNECTED, Telemetry.NOT_CONFIGURED}:
            reason = "Source unavailable · showing retained history"
        elif any(not points or now - points[-1].timestamp > max_age for points in (cpu, memory)):
            state = Telemetry.UNHEALTHY
            reason = "Some history is unavailable or stale"
        return HostTrend(
            telemetry=state, cpu=cpu, memory=memory,
            start=now - self.config.window_seconds, end=now,
            updated_at=datetime.fromtimestamp(latest, UTC) if latest is not None else None,
            reason=reason, interval_seconds=self.config.interval_seconds,
        )


async def sample_forever(sample: Callable[[], Awaitable[None]], interval: float) -> None:
    """Sample on monotonic deadlines; slow sources skip ticks instead of overlapping."""
    deadline = time.monotonic()
    while True:
        try:
            await sample()
        except Exception as exc:
            # Do not log exception contents: upstream messages can contain private URLs.
            logger.error("History sampling failed (%s)", type(exc).__name__)
        deadline += interval
        now = time.monotonic()
        if deadline <= now:
            deadline += (math.floor((now - deadline) / interval) + 1) * interval
        await asyncio.sleep(max(0, deadline - now))
