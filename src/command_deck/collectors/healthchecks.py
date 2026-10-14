"""Read-only Healthchecks Management API v3 backup collector."""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from command_deck.config import AppConfig
from command_deck.models import Health, IssueView, SummaryView, Telemetry
from command_deck.status_logging import StatusLog


class HealthchecksProtocolError(Exception):
    """The Healthchecks response cannot be trusted as a check list."""


@dataclass(frozen=True)
class CheckStatus:
    name: str
    status: str
    started: bool
    last_ping: datetime | None
    unique_key: str | None


@dataclass(frozen=True)
class HealthchecksCollection:
    summary: SummaryView
    issues: list[IssueView]
    checks: list[CheckStatus]


def _parse_checks(payload: object) -> list[CheckStatus]:
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), list):
        raise HealthchecksProtocolError("Expected a Healthchecks check list")
    checks: list[CheckStatus] = []
    for item in payload["checks"]:
        if not isinstance(item, dict):
            raise HealthchecksProtocolError("Malformed Healthchecks check")
        name = item.get("name")
        status = item.get("status")
        if not isinstance(name, str) or not name:
            raise HealthchecksProtocolError("Missing Healthchecks check name")
        if status not in {"new", "up", "grace", "down", "paused"}:
            raise HealthchecksProtocolError("Unknown Healthchecks status")
        started = item.get("started", False)
        if not isinstance(started, bool):
            raise HealthchecksProtocolError("Invalid Healthchecks started state")
        raw_ping = item.get("last_ping")
        try:
            last_ping = datetime.fromisoformat(raw_ping) if raw_ping is not None else None
        except (TypeError, ValueError) as exc:
            raise HealthchecksProtocolError("Invalid Healthchecks ping timestamp") from exc
        if last_ping is not None and last_ping.tzinfo is None:
            raise HealthchecksProtocolError("Healthchecks ping timestamp has no timezone")
        key = item.get("unique_key")
        if key is not None and not isinstance(key, str):
            raise HealthchecksProtocolError("Invalid Healthchecks check key")
        checks.append(
            CheckStatus(
                name=name,
                status=status,
                started=started,
                last_ping=last_ping,
                unique_key=key,
            )
        )
    return checks


def _summarize(checks: list[CheckStatus], fetched_at: datetime) -> HealthchecksCollection:
    if not checks:
        return HealthchecksCollection(
            summary=SummaryView(
                title="Backups",
                telemetry=Telemetry.UNHEALTHY,
                health=Health.UNKNOWN,
                value="No backup checks matched the configured tag",
                updated_at=fetched_at,
            ),
            issues=[],
            checks=[],
        )
    issues: list[IssueView] = []
    for check in checks:
        if check.status == "down":
            severity = Health.CRITICAL
            reason = "Missed or failed schedule"
        elif check.status == "grace":
            severity = Health.WARNING
            reason = "Late; within grace period"
        elif check.status == "paused":
            severity = Health.UNKNOWN
            reason = "Monitoring paused"
        elif check.status == "new":
            severity = Health.UNKNOWN
            reason = "No result yet"
        else:
            continue
        issues.append(
            IssueView(
                severity=severity,
                title=check.name,
                detail=reason,
                source="Healthchecks",
                observed_at=fetched_at,
            )
        )

    statuses = {check.status for check in checks}
    if "down" in statuses:
        health = Health.CRITICAL
        value = f"{sum(check.status == 'down' for check in checks)} backup job(s) missed or failed"
    elif "grace" in statuses:
        health = Health.WARNING
        value = f"{sum(check.status == 'grace' for check in checks)} backup job(s) running late"
    elif "new" in statuses or "paused" in statuses:
        health = Health.UNKNOWN
        value = "Some backup checks have no current result"
    else:
        health = Health.OK
        value = f"All {len(checks)} scheduled jobs on time"
    return HealthchecksCollection(
        summary=SummaryView(
            title="Backups",
            telemetry=Telemetry.CONNECTED,
            health=health,
            value=value,
            updated_at=fetched_at,
        ),
        issues=issues,
        checks=checks,
    )


class HealthchecksCollector:
    def __init__(self, config: AppConfig, client: httpx.AsyncClient):
        self.config = config.healthchecks
        self.client = client
        self._lock = asyncio.Lock()
        self._cached: HealthchecksCollection | None = None
        self._cached_at = 0.0
        self._status = StatusLog("Healthchecks")

    async def collect(self) -> HealthchecksCollection:
        """Read only tagged checks, sharing one short cache across dashboard requests."""
        if self.config.api_key is None:
            self._status.update(Telemetry.NOT_CONFIGURED)
            return HealthchecksCollection(
                summary=SummaryView(title="Backups", value="Read-only API key not configured"),
                issues=[],
                checks=[],
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

    async def _refresh(self) -> HealthchecksCollection:
        try:
            response = await self.client.get(
                f"{self.config.url}/api/v3/checks/",
                params={"tag": self.config.backup_tag},
                headers={"X-Api-Key": self.config.api_key.get_secret_value()},
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            checks = _parse_checks(response.json())
        except httpx.RequestError:
            state = Telemetry.DISCONNECTED
            reason = "Telemetry unavailable · Healthchecks is unreachable"
        except (httpx.HTTPStatusError, HealthchecksProtocolError, ValueError):
            state = Telemetry.UNHEALTHY
            reason = "Healthchecks API response needs attention"
        else:
            return _summarize(checks, datetime.now(UTC))
        return HealthchecksCollection(
            summary=SummaryView(
                title="Backups",
                telemetry=state,
                health=Health.UNKNOWN,
                value=reason,
            ),
            issues=[],
            checks=[],
        )
