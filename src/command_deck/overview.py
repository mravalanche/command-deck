"""Turn independent source results into an explainable Overview."""

from datetime import UTC, datetime

from command_deck.collectors.healthchecks import CheckStatus, HealthchecksCollection
from command_deck.collectors.prometheus import PrometheusCollection
from command_deck.collectors.services import ServicesCollection
from command_deck.models import (
    BackupCheckView,
    Health,
    IssueView,
    OverviewView,
    SummaryView,
    Telemetry,
)


def _backup_check_view(check: CheckStatus) -> BackupCheckView:
    health_by_status = {
        "up": Health.OK,
        "grace": Health.WARNING,
        "down": Health.CRITICAL,
        "paused": Health.UNKNOWN,
        "new": Health.UNKNOWN,
    }
    if check.status == "down":
        display_status = "Overdue · running" if check.started else "Missed or failed"
    elif check.status == "grace":
        display_status = "Running late" if check.started else "Late"
    elif check.status == "paused":
        display_status = "Paused"
    elif check.started:
        display_status = "Running"
    elif check.status == "new":
        display_status = "No result"
    else:
        display_status = "Okay"
    return BackupCheckView(
        name=check.name,
        status=check.status,
        started=check.started,
        last_ping=check.last_ping,
        display_status=display_status,
        health=health_by_status[check.status],
        last_success_at=check.last_ping if check.status == "up" and not check.started else None,
    )


def build_overview(
    collection: PrometheusCollection,
    backups_result: HealthchecksCollection | None = None,
    services_result: ServicesCollection | None = None,
) -> OverviewView:
    backups = backups_result.summary if backups_result else SummaryView(title="Backups")
    backup_checks = (
        [_backup_check_view(check) for check in backups_result.checks] if backups_result else []
    )
    services = services_result.summary if services_result else SummaryView(title="Services")
    issues: list[IssueView] = list(backups_result.issues) if backups_result else []
    if services_result:
        issues.extend(services_result.issues)
    for host in collection.hosts:
        if host.telemetry in {Telemetry.DISCONNECTED, Telemetry.UNHEALTHY}:
            issues.append(
                IssueView(
                    severity=Health.CRITICAL
                    if host.telemetry == Telemetry.DISCONNECTED
                    else Health.WARNING,
                    title=f"{host.name} telemetry {host.telemetry.value.lower()}",
                    detail=host.reason or "The latest host reading needs checking",
                    source="Prometheus",
                    observed_at=host.updated_at,
                )
            )
        for disk in host.disks:
            if disk.health in {Health.WARNING, Health.CRITICAL}:
                issues.append(
                    IssueView(
                        severity=disk.health,
                        title=f"{host.name} · {disk.label}",
                        detail=disk.reason or "Filesystem capacity needs attention",
                        source="TrueNAS" if disk.mountpoint.startswith("pool:") else "Prometheus",
                        observed_at=host.updated_at,
                    )
                )

    issue_rank = {Health.CRITICAL: 0, Health.WARNING: 1, Health.UNKNOWN: 2, Health.OK: 3}
    issues.sort(key=lambda issue: issue_rank[issue.severity])

    all_health = [host.health for host in collection.hosts] + [backups.health, services.health]
    if Health.CRITICAL in all_health:
        health = Health.CRITICAL
        headline = "Attention needed"
    elif Health.WARNING in all_health:
        health = Health.WARNING
        headline = "Attention needed"
    elif Health.UNKNOWN in all_health or collection.source_state != Telemetry.CONNECTED:
        health = Health.UNKNOWN
        headline = "Monitoring coverage incomplete"
    else:
        health = Health.OK
        headline = "All systems healthy"

    attention_health = issues[0].severity if issues else health
    attention_headline = (
        "Attention needed" if attention_health in {Health.WARNING, Health.CRITICAL} else headline
    )

    configured = sum(host.telemetry != Telemetry.NOT_CONFIGURED for host in collection.hosts)
    detail = (
        f"Prometheus: {collection.source_state.value.lower()} · "
        f"{configured} of {len(collection.hosts)} hosts configured · "
        f"Services: {services.telemetry.value.lower()}"
    )
    return OverviewView(
        health=health,
        headline=headline,
        attention_health=attention_health,
        attention_headline=attention_headline,
        detail=detail,
        issues=issues,
        hosts=collection.hosts,
        backups=backups,
        backup_checks=backup_checks,
        services=services,
        generated_at=datetime.now(UTC),
    )
