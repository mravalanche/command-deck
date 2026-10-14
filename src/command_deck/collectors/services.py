"""Read-only Uptime Kuma metrics summary for monitored services."""

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from command_deck.config import AppConfig
from command_deck.models import Health, IssueView, SummaryView, Telemetry
from command_deck.status_logging import StatusLog

STATUS_LINE = re.compile(r"^monitor_status(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)(?:\s+\S+)?$")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')
URL_NAME = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
STATUS_HEALTH = {0: Health.CRITICAL, 1: Health.OK, 2: Health.WARNING, 3: Health.UNKNOWN}


class ServicesProtocolError(Exception):
    """The Uptime Kuma metrics body lacks usable monitor states."""


@dataclass(frozen=True)
class ServicesCollection:
    summary: SummaryView
    issues: list[IssueView]
    monitors: list["ServiceMonitor"] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceMonitor:
    name: str
    status: int
    health: Health


def _parse_labels(label_set: str) -> dict[str, str]:
    if not label_set:
        return {}
    labels: dict[str, str] = {}
    position = 0
    while position < len(label_set):
        match = LABEL.match(label_set, position)
        if match is None or match.group(1) in labels:
            raise ServicesProtocolError("Malformed monitor labels")
        value = match.group(2)
        try:
            # Prometheus quoted labels support only these three escapes.
            value = re.sub(
                r"\\(.)",
                lambda escaped: {"n": "\n", "\\": "\\", '"': '"'}[escaped.group(1)],
                value,
            )
        except KeyError as exc:
            raise ServicesProtocolError("Malformed monitor label escape") from exc
        labels[match.group(1)] = value
        position = match.end()
        if position < len(label_set):
            if label_set[position] != ",":
                raise ServicesProtocolError("Malformed monitor labels")
            position += 1
            if position == len(label_set):
                raise ServicesProtocolError("Malformed monitor labels")
    return labels


def _display_name(labels: dict[str, str], ordinal: int) -> str:
    name = " ".join(labels.get("monitor_name", "").split())
    if not name or URL_NAME.match(name):
        monitor_id = labels.get("monitor_id", "")
        # IDs are useful as fallbacks, but should not display a URL or arbitrary label data.
        name = f"Monitor {monitor_id}" if monitor_id.isdecimal() else f"Monitor {ordinal}"
    return name


def _parse_monitors(body: str) -> list[ServiceMonitor]:
    statuses: dict[tuple[tuple[str, str], ...], ServiceMonitor] = {}
    for line in body.splitlines():
        if not line.startswith("monitor_status"):
            continue
        match = STATUS_LINE.fullmatch(line.strip())
        if match is None:
            raise ServicesProtocolError("Malformed monitor status line")
        labels = _parse_labels(match.group("labels") or "")
        try:
            status = int(match.group("value"))
        except ValueError as exc:
            raise ServicesProtocolError("Non-integer monitor status") from exc
        if status not in {0, 1, 2, 3}:
            raise ServicesProtocolError("Unknown monitor status")
        identity = (
            (("monitor_id", labels["monitor_id"]),)
            if labels.get("monitor_id")
            else tuple(sorted(labels.items()))
        )
        if identity in statuses and statuses[identity].status != status:
            raise ServicesProtocolError("Conflicting monitor status")
        if identity not in statuses:
            statuses[identity] = ServiceMonitor(
                name=_display_name(labels, len(statuses) + 1),
                status=status,
                health=STATUS_HEALTH[status],
            )
    if not statuses:
        raise ServicesProtocolError("No active monitor statuses")
    return list(statuses.values())


def _parse_statuses(body: str) -> list[int]:
    return [monitor.status for monitor in _parse_monitors(body)]


def _summarize(monitors: list[ServiceMonitor], observed_at: datetime) -> ServicesCollection:
    statuses = [monitor.status for monitor in monitors]
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
    issues = (
        [
            IssueView(
                severity=health,
                title="Service monitors need attention",
                detail=", ".join(details),
                source="Uptime Kuma",
                observed_at=observed_at,
            )
        ]
        if details
        else []
    )
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
        monitors=sorted(monitors, key=lambda monitor: {0: 0, 2: 1, 3: 2, 1: 3}[monitor.status]),
    )


class ServicesCollector:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient):
        self.config = config.uptime_kuma
        self.client = client
        self._lock = asyncio.Lock()
        self._cached: ServicesCollection | None = None
        self._cached_at = 0.0
        self._status = StatusLog("Uptime Kuma")

    async def collect(self) -> ServicesCollection:
        if not self.config.enabled:
            self._status.update(Telemetry.NOT_CONFIGURED)
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
            self._status.update(result.summary.telemetry)
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
            monitors = _parse_monitors(response.text)
        except httpx.RequestError:
            state = Telemetry.DISCONNECTED
            value = "Uptime Kuma is unreachable"
        except (httpx.HTTPStatusError, ServicesProtocolError):
            state = Telemetry.UNHEALTHY
            value = "Uptime Kuma metrics need attention"
        else:
            return _summarize(monitors, datetime.now(UTC))
        return ServicesCollection(
            summary=SummaryView(title="Services", telemetry=state, value=value), issues=[]
        )
