"""Render deterministic, fictitious dashboard pages for README screenshots.

Run this through ``uv run --no-sync python scripts/mock-screenshot-pages.py``.
The output is only a temporary capture fixture; no monitored service is contacted.
"""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from command_deck.collectors.services import ServiceMonitor, ServicesCollection
from command_deck.collectors.trends import HostTrend, TrendPoint
from command_deck.models import (
    BackupCheckView,
    DiskView,
    Health,
    HostView,
    IssueView,
    OverviewView,
    SummaryView,
    Telemetry,
)
from command_deck.server_view import chart_path, trend_note

ROOT = Path(__file__).resolve().parents[1]
STAGING = Path(tempfile.gettempdir()) / "command-deck-screenshot-fixtures"
TEMPLATES = ROOT / "src/command_deck/templates"
STATIC = ROOT / "src/command_deck/static"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
GiB = 1024**3
TiB = 1024**4


def disk(label: str, used: int, total: int, *, pool: bool = False) -> DiskView:
    return DiskView(
        label=label,
        mountpoint=f"pool:{label}" if pool else f"/{label.lower()}",
        used_bytes=int(used),
        total_bytes=int(total),
        available_bytes=int(total - used),
        used_percent=100 * used / total,
        health=Health.OK,
    )


def host(
    id: str, name: str, cpu: float, memory: float, disks: list[DiskView], *, cpus: int | None = None
) -> HostView:
    total_memory = 32 * GiB if id == "server" else 64 * GiB
    return HostView(
        id=id,
        name=name,
        telemetry=Telemetry.CONNECTED,
        health=Health.OK,
        metrics_current=True,
        cpu_percent=cpu,
        logical_cpu_count=cpus,
        memory_percent=memory,
        memory_used_bytes=int(total_memory * memory / 100),
        memory_total_bytes=total_memory,
        disks=disks,
        updated_at=NOW,
    )


SERVER = host(
    "server",
    "Server",
    23,
    42,
    [disk("System", 82 * GiB, 256 * GiB), disk("Media", 1.4 * TiB, 4 * TiB)],
    cpus=8,
)
NAS = host(
    "nas",
    "NAS",
    12,
    35,
    [
        disk("Main", 6 * TiB, 12 * TiB, pool=True),
        disk("Backup", 2.8 * TiB, 4 * TiB, pool=True),
        disk("Archive", 730 * GiB, 2 * TiB, pool=True),
    ],
)
HEALTHY = OverviewView(
    health=Health.OK,
    headline="All systems healthy",
    attention_health=Health.OK,
    attention_headline="All systems healthy",
    detail="Prometheus: connected · 2 of 2 hosts configured · Services: connected",
    hosts=[SERVER, NAS],
    backups=SummaryView(
        title="Backups",
        telemetry=Telemetry.CONNECTED,
        health=Health.OK,
        value="6 of 6 healthy",
        caption="Checks reporting on time",
        updated_at=NOW,
    ),
    backup_checks=[
        BackupCheckView(
            name="Nightly backup",
            status="up",
            started=False,
            last_ping=NOW,
            last_success_at=NOW,
            display_status="Okay",
            health=Health.OK,
        )
    ],
    services=SummaryView(
        title="Services",
        telemetry=Telemetry.CONNECTED,
        health=Health.OK,
        value="12 / 12",
        caption="healthy / active monitors",
        updated_at=NOW,
    ),
    generated_at=NOW,
)
SERVICES = ServicesCollection(
    summary=SummaryView(
        title="Services",
        telemetry=Telemetry.CONNECTED,
        health=Health.OK,
        value="4 / 4",
        caption="healthy / active monitors",
        updated_at=NOW,
    ),
    issues=[],
    monitors=[
        ServiceMonitor(name=name, status=1, health=Health.OK)
        for name in ("Photo library", "Notes", "Media", "Metrics")
    ],
)


def render(env: Environment, template: str, context: dict, output: str) -> None:
    context = {
        "request": SimpleNamespace(query_params={}, url=SimpleNamespace(path="/")),
        **context,
    }
    html = env.get_template(template).render(**context)
    # A local file URL must load these assets relative to the rendered fixture.
    # Removing live scripts also fixes the clock and prevents refresh requests.
    html = html.replace('href="/static/', 'href="./static/')
    html = html.replace('src="/static/favicon.svg"', 'src="./static/favicon.svg"')
    html = html.replace('<script src="/static/htmx.min.js" defer></script>', "")
    html = html.replace('<script src="/static/app.js" defer></script>', "")
    html = html.replace(
        '<time class="clock-date" id="clock-date"></time>',
        '<time class="clock-date" id="clock-date">MON 12 OCT</time>',
    )
    html = html.replace(
        '<time class="clock-time" id="clock-time"></time>',
        '<time class="clock-time" id="clock-time">12:00</time>',
    )
    (STAGING / output).write_text(html, encoding="utf-8")


def server_context(selected: HostView) -> dict:
    start = NOW.timestamp() - 300
    cpu = [
        TrendPoint(start + i * 15, max(4, min(95, selected.cpu_percent + (i % 7 - 3) * 2.7)))
        for i in range(21)
    ]
    memory = [
        TrendPoint(start + i * 15, selected.memory_percent + (i % 5 - 2) * 0.5) for i in range(21)
    ]
    trend = HostTrend(
        telemetry=Telemetry.CONNECTED,
        cpu=cpu,
        memory=memory,
        start=start,
        end=start + 300,
        updated_at=NOW,
    )
    return {
        "hosts": [SERVER, NAS],
        "selected": selected,
        "trend": trend,
        "cpu_path": chart_path(cpu, trend.start, trend.end),
        "memory_path": chart_path(memory, trend.start, trend.end),
        "cpu_peak_percent": max(point.percent for point in cpu),
        "cpu_average_percent": sum(point.percent for point in cpu) / len(cpu),
        "cpu_sample_count": len(cpu),
        "cpu_partial": False,
        "trend_note": trend_note(trend),
        "active_view": "servers",
    }


def main() -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    shutil.copytree(STATIC, STAGING / "static", dirs_exist_ok=True)
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    render(
        env,
        "overview.html",
        {"overview": HEALTHY, "active_view": "overview"},
        "overview-healthy.html",
    )
    alert_disk = disk("Backup", int(3.9 * TiB), 4 * TiB, pool=True).model_copy(
        update={"health": Health.CRITICAL, "reason": "Less than 5% available"}
    )
    alert_nas = NAS.model_copy(
        update={"health": Health.CRITICAL, "disks": [NAS.disks[0], alert_disk, NAS.disks[2]]}
    )
    alert = HEALTHY.model_copy(
        update={
            "health": Health.CRITICAL,
            "headline": "Attention needed",
            "attention_health": Health.CRITICAL,
            "attention_headline": "Attention needed",
            "hosts": [SERVER, alert_nas],
            "issues": [
                IssueView(
                    severity=Health.CRITICAL,
                    title="NAS · Backup",
                    detail="Less than 5% available",
                    source="TrueNAS",
                    observed_at=NOW,
                )
            ],
        }
    )
    render(
        env, "overview.html", {"overview": alert, "active_view": "overview"}, "overview-alert.html"
    )
    render(env, "services.html", {"services": SERVICES, "active_view": "services"}, "services.html")
    render(env, "servers.html", server_context(SERVER), "server.html")
    render(env, "servers.html", server_context(NAS), "nas.html")
    render(env, "events.html", {"overview": HEALTHY, "active_view": "events"}, "events.html")
    print(STAGING)


if __name__ == "__main__":
    main()
