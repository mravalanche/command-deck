"""Container logs show connection changes without repeating errors or leaking details."""

from types import SimpleNamespace

import pytest

from command_deck.main import check_startup_connections
from command_deck.models import Telemetry
from command_deck.status_logging import StatusLog, configure_stdout_logging


def test_status_log_reports_first_result_and_transitions_only(capfd):
    configure_stdout_logging()
    configure_stdout_logging()
    status = StatusLog("Prometheus host server")
    for state in (
        Telemetry.CONNECTED,
        Telemetry.CONNECTED,
        Telemetry.DISCONNECTED,
        Telemetry.DISCONNECTED,
        Telemetry.CONNECTED,
    ):
        status.update(state)

    lines = capfd.readouterr().out.splitlines()
    assert lines == [
        "INFO command-deck: Prometheus host server: connected",
        "WARNING command-deck: Prometheus host server: disconnected",
        "INFO command-deck: Prometheus host server: connected",
    ]


@pytest.mark.asyncio
async def test_startup_check_logs_safe_results_and_exception_class(capfd):
    configure_stdout_logging()

    class Collector:
        def __init__(self, source: str, state: Telemetry):
            self.status = StatusLog(source)
            self.state = state

        async def collect(self):
            self.status.update(self.state)

    class FailedCollector:
        async def collect(self):
            raise RuntimeError("private URL and credential must not appear")

    app = SimpleNamespace(state=SimpleNamespace(
        collector=Collector("Prometheus", Telemetry.CONNECTED),
        healthchecks=FailedCollector(),
        services=Collector("Uptime Kuma", Telemetry.NOT_CONFIGURED),
        truenas=Collector("TrueNAS", Telemetry.UNHEALTHY),
        proxmox=Collector("Proxmox", Telemetry.NOT_CONFIGURED),
    ))
    await check_startup_connections(app, 2)

    output = capfd.readouterr().out
    assert "Checking sources for 2 configured hosts" in output
    assert "Prometheus: connected" in output
    assert "Uptime Kuma: not configured" in output
    assert "TrueNAS: unhealthy" in output
    assert "Healthchecks: startup check failed (RuntimeError)" in output
    assert "private URL" not in output
    assert "credential must not appear" not in output
