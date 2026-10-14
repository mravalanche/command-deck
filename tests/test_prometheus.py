"""Collector behavior against representative Prometheus API responses."""

from datetime import UTC, datetime

import httpx
import pytest

from command_deck.collectors.prometheus import QUERIES, PrometheusCollector, graphite_queries
from command_deck.config import AppConfig
from command_deck.models import Health, Telemetry
from command_deck.overview import build_overview


def settings() -> AppConfig:
    return AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090", "cache_seconds": 20},
            "hosts": [
                {
                    "id": "server",
                    "name": "Server",
                    "instance": "192.0.2.10:9100",
                    "filesystems": [{"mountpoint": "/", "label": "System"}],
                },
                {"id": "nas", "name": "NAS", "instance": None},
            ],
        }
    )


def vector(
    query: str,
    *,
    up: float = 1,
    age: int = 1,
    missing_cpu: bool = False,
    available_bytes: float = 40,
) -> dict:
    observed = datetime.now(UTC).timestamp() - age
    values = {
        "up": (up, {"instance": "192.0.2.10:9100"}),
        "up_timestamp": (observed, {"instance": "192.0.2.10:9100"}),
        "cpu": (18, {"instance": "192.0.2.10:9100"}),
        "cpu_count": (8, {"instance": "192.0.2.10:9100"}),
        "memory_total": (1000, {"instance": "192.0.2.10:9100"}),
        "memory_available": (400, {"instance": "192.0.2.10:9100"}),
        "filesystem_size": (1000, {"instance": "192.0.2.10:9100", "mountpoint": "/"}),
        "filesystem_free": (200, {"instance": "192.0.2.10:9100", "mountpoint": "/"}),
        "filesystem_available": (
            available_bytes,
            {"instance": "192.0.2.10:9100", "mountpoint": "/"},
        ),
    }
    key = next(key for key, expression in QUERIES.items() if expression == query)
    result = [] if key == "cpu" and missing_cpu else [values[key]]
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": labels, "value": [observed, str(value)]} for value, labels in result
            ],
        },
    }


@pytest.mark.asyncio
async def test_disk_pressure_uses_available_bytes_and_unknown_sources_remain_unknown():
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["timeout"] == "3s"
        assert request.url.params["lookback_delta"] == "90s"
        return httpx.Response(200, json=vector(request.url.params["query"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = PrometheusCollector(settings(), client)
        collection = await collector.collect()
        assert await collector.collect() is collection
    assert calls == len(QUERIES)
    server, nas = collection.hosts
    assert server.telemetry == Telemetry.CONNECTED
    assert server.cpu_percent == 18
    assert server.logical_cpu_count == 8
    assert server.memory_percent == 60
    assert server.disks[0].used_percent == 80  # size - free, not size - available
    assert server.disks[0].available_bytes == 40
    assert server.disks[0].health == Health.CRITICAL  # available to apps is only 4%
    assert nas.telemetry == Telemetry.NOT_CONFIGURED
    assert nas.health == Health.UNKNOWN
    overview = build_overview(collection)
    assert overview.health == Health.CRITICAL
    assert overview.backups.health == Health.UNKNOWN
    assert overview.services.health == Health.UNKNOWN


@pytest.mark.asyncio
async def test_scrape_failure_and_missing_metrics_never_appear_healthy():
    for case in ("scrape", "missing", "stale"):

        def respond(request: httpx.Request, current_case: str = case) -> httpx.Response:
            return httpx.Response(
                200,
                json=vector(
                    request.url.params["query"],
                    up=0 if current_case == "scrape" else 1,
                    age=120 if current_case == "stale" else 1,
                    missing_cpu=current_case == "missing",
                    available_bytes=200,
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            host = (await PrometheusCollector(settings(), client).collect()).hosts[0]
        assert host.health == Health.UNKNOWN
        assert host.telemetry == (
            Telemetry.DISCONNECTED if case == "scrape" else Telemetry.UNHEALTHY
        )


@pytest.mark.asyncio
async def test_prometheus_timeout_keeps_all_hosts_renderable():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("source unreachable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        collection = await PrometheusCollector(settings(), client).collect()
    assert collection.source_state == Telemetry.DISCONNECTED
    assert collection.hosts[0].telemetry == Telemetry.DISCONNECTED
    assert collection.hosts[1].telemetry == Telemetry.NOT_CONFIGURED
    assert build_overview(collection).health == Health.UNKNOWN


@pytest.mark.asyncio
async def test_truenas_graphite_uses_host_metrics_and_ingest_freshness():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [{
                "id": "nas",
                "name": "NAS",
                "source": "truenas_graphite",
                "instance": "graphite-exporter:9108",
                "graphite_prefix": "truenas_nas",
            }],
        }
    )
    queries = graphite_queries(config.hosts[0])
    values = {
        "up": 1,
        "last_processed": datetime.now(UTC).timestamp() - 10,
        "cpu": 12.5,
        "memory_total": 32_000,
        "memory_available": 8_000,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        rows = []
        for key, expression in queries.items():
            if query == expression:
                rows = [{
                    "metric": {"instance": "graphite-exporter:9108"},
                    "value": [datetime.now(UTC).timestamp(), str(values[key])],
                }]
                break
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": rows}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = PrometheusCollector(config, client)
        host = (await collector.collect()).hosts[0]
        assert host.telemetry == Telemetry.CONNECTED
        assert host.cpu_percent == 12.5
        assert host.memory_percent == 75
        assert host.disks == []
        values["last_processed"] = datetime.now(UTC).timestamp() - 120
        stale = (await PrometheusCollector(config, client).collect()).hosts[0]
    assert stale.telemetry == Telemetry.UNHEALTHY
    assert stale.reason == "Source data is stale"
    assert host.logical_cpu_count is None


@pytest.mark.asyncio
async def test_optional_cpu_count_failure_does_not_degrade_healthy_host():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["query"] == QUERIES["cpu_count"]:
            return httpx.Response(503)
        return httpx.Response(200, json=vector(request.url.params["query"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collection = await PrometheusCollector(settings(), client).collect()
    assert collection.source_state == Telemetry.CONNECTED
    assert collection.hosts[0].telemetry == Telemetry.CONNECTED
    assert collection.hosts[0].logical_cpu_count is None
