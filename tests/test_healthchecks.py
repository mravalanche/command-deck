"""Healthchecks read-only API outcomes and independent source degradation."""

import httpx
import pytest
from pydantic import SecretStr

from command_deck.collectors.healthchecks import HealthchecksCollector
from command_deck.config import AppConfig, HealthchecksConfig
from command_deck.models import Health, Telemetry


def settings(*, with_key: bool = True) -> AppConfig:
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [{"id": "server", "name": "Server"}],
            "healthchecks": {
                "url": "http://healthchecks:8000",
                "backup_tag": "backup",
                "cache_seconds": 30,
            },
        }
    )
    if with_key:
        config.healthchecks = HealthchecksConfig(
            url=config.healthchecks.url,
            backup_tag=config.healthchecks.backup_tag,
            cache_seconds=config.healthchecks.cache_seconds,
            api_key=SecretStr("test-read-only-key"),
        )
    return config


def check(name: str, status: str) -> dict:
    return {
        "name": name,
        "status": status,
        "last_ping": "2026-10-12T08:00:00+00:00",
        "unique_key": "test-unique-key",
    }


@pytest.mark.asyncio
async def test_successful_and_failed_checks_are_authoritative_and_cached():
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v3/checks/"
        assert request.url.params["tag"] == "backup"
        assert request.headers["X-Api-Key"] == "test-read-only-key"
        return httpx.Response(
            200,
            json={"checks": [check("Server backup", "up"), check("NAS backup", "down")]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = HealthchecksCollector(settings(), client)
        result = await collector.collect()
        assert await collector.collect() is result
    assert calls == 1
    assert result.summary.telemetry == Telemetry.CONNECTED
    assert result.summary.health == Health.CRITICAL
    assert result.summary.value == "1 backup job(s) missed or failed"
    assert result.issues[0].title == "NAS backup"
    assert result.checks[0].last_ping is not None


@pytest.mark.asyncio
async def test_missing_key_does_not_send_an_unauthenticated_request():
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No request should be made without a key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        result = await HealthchecksCollector(settings(with_key=False), client).collect()
    assert result.summary.telemetry == Telemetry.NOT_CONFIGURED
    assert result.summary.health == Health.UNKNOWN


@pytest.mark.asyncio
async def test_empty_or_invalid_check_set_cannot_be_healthy():
    for payload, expected_state in (
        ({"checks": []}, Telemetry.UNHEALTHY),
        ({"checks": [check("Backup", "surprise")]}, Telemetry.UNHEALTHY),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request, current_payload=payload: httpx.Response(200, json=current_payload)
            )
        ) as client:
            result = await HealthchecksCollector(settings(), client).collect()
        assert result.summary.telemetry == expected_state
        assert result.summary.health == Health.UNKNOWN


@pytest.mark.asyncio
async def test_timeout_and_auth_failure_are_distinct_source_states():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        disconnected = await HealthchecksCollector(settings(), client).collect()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403))
    ) as client:
        unhealthy = await HealthchecksCollector(settings(), client).collect()
    assert disconnected.summary.telemetry == Telemetry.DISCONNECTED
    assert unhealthy.summary.telemetry == Telemetry.UNHEALTHY
    assert disconnected.summary.health == unhealthy.summary.health == Health.UNKNOWN
