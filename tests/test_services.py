"""Uptime Kuma service counts only active monitor states."""

import httpx
import pytest

from command_deck.collectors.services import ServicesCollector
from command_deck.config import AppConfig
from command_deck.models import Health, Telemetry


def config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "uptime_kuma": {"url": "http://kuma:3001", "enabled": True},
            "hosts": [{"id": "server", "name": "Server"}],
        }
    )


@pytest.mark.asyncio
async def test_open_metrics_count_healthy_active_monitors_without_auth():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/metrics"
        assert "Authorization" not in request.headers
        return httpx.Response(
            200,
            text="""
# HELP monitor_status Monitor Status (1 = UP, 0 = DOWN, 2 = PENDING, 3 = MAINTENANCE)
monitor_status{monitor_name="site"} 1
monitor_status{monitor_name="nas"} 1
monitor_status{monitor_name="paused-monitor"} 3
""",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.telemetry == Telemetry.CONNECTED
    assert result.summary.health == Health.UNKNOWN
    assert result.summary.value == "2 / 3"
    assert result.summary.caption == "healthy / active monitors"
    assert [(monitor.name, monitor.status) for monitor in result.monitors] == [
        ("paused-monitor", 3),
        ("site", 1),
        ("nas", 1),
    ]


@pytest.mark.asyncio
async def test_down_monitor_is_not_reported_healthy():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
monitor_status{monitor_name="site"} 1
monitor_status{monitor_name="database"} 0
""",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.value == "1 / 2"
    assert result.summary.health == Health.CRITICAL
    assert result.issues[0].detail == "1 down"
    assert result.monitors[0].health == Health.CRITICAL


@pytest.mark.asyncio
async def test_missing_monitor_status_does_not_show_zero_of_zero_as_healthy():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="other_metric 1\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.telemetry == Telemetry.UNHEALTHY
    assert result.summary.health == Health.UNKNOWN
    assert result.monitors == []


@pytest.mark.asyncio
async def test_names_are_parsed_without_exposing_monitor_urls_or_collapsing_same_name():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="\n".join(
                [
                    (
                        r'monitor_status{monitor_id="5",monitor_name="App, \"primary\"",'
                        r'monitor_url="https://private.example/token"} 1'
                    ),
                    (
                        r'monitor_status{monitor_id="6",monitor_name="App, \"primary\"",'
                        r'monitor_url="https://other.example"} 0'
                    ),
                    (
                        r'monitor_status{monitor_id="7",monitor_name="https://private.example/secret",'
                        r'monitor_url="https://private.example/secret"} 2'
                    ),
                ]
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.value == "1 / 3"
    assert [(item.name, item.status) for item in result.monitors] == [
        ('App, "primary"', 0),
        ("Monitor 7", 2),
        ('App, "primary"', 1),
    ]
    assert "private.example" not in str(result)


@pytest.mark.asyncio
async def test_duplicate_identity_with_conflicting_status_marks_metrics_unhealthy():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
monitor_status{monitor_id="5",monitor_name="App"} 1
monitor_status{monitor_name="App",monitor_id="5"} 0
""",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ServicesCollector(config(), client).collect()
    assert result.summary.telemetry == Telemetry.UNHEALTHY
    assert result.monitors == []
