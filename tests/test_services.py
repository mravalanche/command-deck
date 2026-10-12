"""Uptime Kuma service counts only active monitor states."""

import httpx
import pytest

from command_deck.collectors.services import ServicesCollector
from command_deck.config import AppConfig
from command_deck.models import Health, Telemetry


def config() -> AppConfig:
    return AppConfig.model_validate({
        "prometheus": {"url": "http://prometheus:9090"},
        "uptime_kuma": {"url": "http://kuma:3001", "enabled": True},
        "hosts": [{"id": "server", "name": "Server"}],
    })


@pytest.mark.asyncio
async def test_open_metrics_count_healthy_active_monitors_without_auth():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/metrics"
        assert "Authorization" not in request.headers
        return httpx.Response(200, text="""
# HELP monitor_status Monitor Status (1 = UP, 0 = DOWN, 2 = PENDING, 3 = MAINTENANCE)
monitor_status{monitor_name="site"} 1
monitor_status{monitor_name="nas"} 1
monitor_status{monitor_name="paused-monitor"} 3
""")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.telemetry == Telemetry.CONNECTED
    assert result.summary.health == Health.UNKNOWN
    assert result.summary.value == "2 / 3"
    assert result.summary.caption == "healthy / active monitors"


@pytest.mark.asyncio
async def test_down_monitor_is_not_reported_healthy():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="""
monitor_status{monitor_name="site"} 1
monitor_status{monitor_name="database"} 0
""")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.value == "1 / 2"
    assert result.summary.health == Health.CRITICAL
    assert result.issues[0].detail == "1 down"


@pytest.mark.asyncio
async def test_missing_monitor_status_does_not_show_zero_of_zero_as_healthy():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="other_metric 1\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.telemetry == Telemetry.UNHEALTHY
    assert result.summary.health == Health.UNKNOWN
