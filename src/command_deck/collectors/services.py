"""Read-only Uptime Kuma metrics summary for monitored services."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from command_deck.config import AppConfig
from command_deck.models import Health, IssueView, SummaryView, Telemetry

logger = logging.getLogger(__name__)
STATUS_LINE = re.compile(r"^monitor_status(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)(?:\s+\S+)?$")


class ServicesProtocolError(Exception):
    """The Uptime Kuma metrics body lacks usable monitor states."""


@dataclass(frozen=True)
class ServicesCollection:
    summary: SummaryView
    issues: list[IssueView]


def _parse_statuses(body: str) -> list[int]:
    statuses: dict[str, int] = {}
    for line in body.splitlines():
        if not line.startswith("monitor_status"):
            continue
        match = STATUS_LINE.fullmatch(line.strip())
        if match is None:
            raise ServicesProtocolError("Malformed monitor status line")
        label_set = match.group("labels") or ""
        try:
            status = int(match.group("value"))
        except ValueError as exc:
            raise ServicesProtocolError("Non-integer monitor status") from exc
        if status not in {0, 1, 2, 3}:
            raise ServicesProtocolError("Unknown monitor status")
        if label_set in statuses and statuses[label_set] != status:
            raise ServicesProtocolError("Conflicting monitor status")
        statuses[label_set] = status
    if not statuses:
        raise ServicesProtocolError("No active monitor statuses")
    return list(statuses.values())


def _summarize(statuses: list[int], observed_at: datetime) -> ServicesCollection:
    total = len(statuses)
    up = statuses.count(1)
    down = statuses.count(0)
    pending = statuses.count(2)
    maintenance = statuses.count(3)
    if down:
        health = Health.CRITICAL
    elif pending:
        health = Health.WARNING
    elif maintenance:
        health = Health.UNKNOWN
    else:
        health = Health.OK
    details = []
    for count, name in ((down, "down"), (pending, "pending"), (maintenance, "maintenance")):
        if count:
            details.append(f"{count} {name}")
    issues = [
        IssueView(
            severity=health,
            title="Service monitors need attention",
            detail=", ".join(details),
            source="Uptime Kuma",
            observed_at=observed_at,
        )
    ] if details else []
    return ServicesCollection(
        summary=SummaryView(
            title="Services",
            telemetry=Telemetry.CONNECTED,
            health=health,
            value=f"{up} / {total}",
            caption="healthy / active monitors",
            updated_at=observed_at,
        ),
        issues=issues,
    )


class ServicesCollector:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient):
        self.config = config.uptime_kuma
        self.client = client
        self._lock = asyncio.Lock()
        self._cached: ServicesCollection | None = None
        self._cached_at = 0.0

    async def collect(self) -> ServicesCollection:
        if not self.config.enabled:
            return ServicesCollection(
                summary=SummaryView(title="Services", value="Uptime Kuma not configured"),
                issues=[],
            )
        if self._cached and time.monotonic() - self._cached_at < self.config.cache_seconds:
            return self._cached
        async with self._lock:
            if self._cached and time.monotonic() - self._cached_at < self.config.cache_seconds:
                return self._cached
            result = await self._refresh()
            self._cached = result
            self._cached_at = time.monotonic()
            return result

    async def _refresh(self) -> ServicesCollection:
        try:
            auth = (
                httpx.BasicAuth("", self.config.api_key.get_secret_value())
                if self.config.api_key is not None
                else None
            )
            response = await self.client.get(
                f"{self.config.url}/metrics",
                auth=auth,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            statuses = _parse_statuses(response.text)
        except httpx.RequestError as exc:
            logger.warning("Uptime Kuma request failed (%s)", type(exc).__name__)
            state = Telemetry.DISCONNECTED
            value = "Uptime Kuma is unreachable"
        except (httpx.HTTPStatusError, ServicesProtocolError) as exc:
            logger.warning("Uptime Kuma metrics failed (%s)", type(exc).__name__)
            state = Telemetry.UNHEALTHY
            value = "Uptime Kuma metrics need attention"
        else:
            return _summarize(statuses, datetime.now(UTC))
        return ServicesCollection(
            summary=SummaryView(title="Services", telemetry=state, value=value), issues=[]
        )
