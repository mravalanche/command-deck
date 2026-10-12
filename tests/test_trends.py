"""On-demand range queries preserve source truth and server selector behavior."""

from datetime import UTC, datetime

import httpx
import pytest

from command_deck.collectors.prometheus import QUERIES
from command_deck.collectors.trends import TrendCollector
from command_deck.config import AppConfig
from command_deck.main import create_app
from command_deck.models import Telemetry
from command_deck.server_view import chart_path, trend_note


def settings() -> AppConfig:
    return AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [
                {"id": "server", "name": "Server", "instance": "192.0.2.10:9100"},
                {"id": "nas", "name": "NAS", "instance": None},
            ],
        }
    )


@pytest.mark.asyncio
async def test_trend_queries_prometheus_history_on_selection_then_uses_cache():
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v1/query_range"
        start = float(request.url.params["start"])
        end = float(request.url.params["end"])
        is_cpu = "node_cpu_seconds_total" in request.url.params["query"]
        values = [[start, "15"], [end, "30"]] if is_cpu else [[start, "40"], [end, "55"]]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {"instance": "192.0.2.10:9100"}, "values": values}],
                },
            },
        )

    config = settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = TrendCollector(config, client)
        first = await collector.collect(config.hosts[0])
        assert await collector.collect(config.hosts[0]) is first
        nas = await collector.collect(config.hosts[1])
    assert calls == 2
    assert first.telemetry == Telemetry.CONNECTED
    assert [point.percent for point in first.cpu] == [15, 30]
    assert [point.percent for point in first.memory] == [40, 55]
    assert chart_path(first.cpu, first.start, first.end).startswith("M0.0,")
    assert trend_note(first) == "Past 5 min"
    assert nas.telemetry == Telemetry.NOT_CONFIGURED
    assert not nas.has_data


@pytest.mark.asyncio
async def test_trend_timeout_has_no_false_graph():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("source unavailable", request=request)

    config = settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        trend = await TrendCollector(config, client).collect(config.hosts[0])
    assert trend.telemetry == Telemetry.DISCONNECTED
    assert not trend.has_data


@pytest.mark.asyncio
async def test_truenas_graphite_trend_uses_gauges_and_valid_duration_parameters():
    config = AppConfig.model_validate({
        "prometheus": {"url": "http://prometheus:9090"},
        "hosts": [{
            "id": "nas", "name": "NAS", "source": "truenas_graphite",
            "instance": "graphite-exporter:9108", "graphite_prefix": "truenas_nas",
        }],
    })

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timeout"] == "3s"
        assert request.url.params["lookback_delta"] == "90s"
        query = request.url.params["query"]
        assert "rate(" not in query
        value = "12" if "cpu_usage" in query else "75"
        return httpx.Response(200, json={
            "status": "success",
            "data": {"resultType": "matrix", "result": [{
                "metric": {"instance": "graphite-exporter:9108"},
                "values": [[float(request.url.params["end"]), value]],
            }]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        trend = await TrendCollector(config, client).collect(config.hosts[0])
    assert trend.telemetry == Telemetry.CONNECTED
    assert trend.cpu[-1].percent == 12
    assert trend.memory[-1].percent == 75


@pytest.mark.asyncio
async def test_stale_memory_history_is_marked_partial_even_with_current_cpu():
    def respond(request: httpx.Request) -> httpx.Response:
        end = float(request.url.params["end"])
        is_cpu = "node_cpu_seconds_total" in request.url.params["query"]
        latest = end if is_cpu else end - 240
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"instance": "192.0.2.10:9100"},
                            "values": [[latest, "32"]],
                        }
                    ],
                },
            },
        )

    config = settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        trend = await TrendCollector(config, client).collect(config.hosts[0])
    assert trend.telemetry == Telemetry.UNHEALTHY
    assert trend.has_data
    assert trend_note(trend) == "Some trend data is unavailable or stale"


@pytest.mark.asyncio
async def test_servers_view_orders_hosts_from_config_and_handles_source_outage():
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("source unavailable", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    app = create_app(settings(), upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/servers")
            fragment = await browser.get("/partials/servers/nas")
            missing = await browser.get("/partials/servers/nonexistent")
    assert page.status_code == 200
    assert page.text.index("Server") < page.text.index("NAS")
    assert "Telemetry unavailable" in page.text or "Prometheus is unavailable" in page.text
    assert 'id="servers-live"' in fragment.text
    assert "NOT CONFIGURED" in fragment.text
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_servers_chart_axes_remain_visible_with_partial_history():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query_range":
            points = (
                [
                    [float(request.url.params["start"]), "25"],
                    [float(request.url.params["end"]), "50"],
                ]
                if "node_cpu_seconds_total" in request.url.params["query"]
                else []
            )
            rows = [{"metric": {"instance": "192.0.2.10:9100"}, "values": points}] if points else []
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {"resultType": "matrix", "result": rows},
                },
            )
        key = next(
            name
            for name, expression in QUERIES.items()
            if expression == request.url.params["query"]
        )
        values = {
            "up": 1,
            "up_timestamp": datetime.now(UTC).timestamp(),
            "cpu": 50,
            "cpu_count": 8,
            "memory_total": 1000,
            "memory_available": 400,
        }
        rows = (
            [{"metric": {"instance": "192.0.2.10:9100"}, "value": [0, str(values[key])]}]
            if key in values
            else []
        )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": rows},
            },
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(settings(), upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/servers")
    assert page.status_code == 200
    assert 'class="chart-y-axis"' in page.text
    assert "<span>100%</span><span>50%</span><span>0%</span>" in page.text
    assert "<span>−5</span><span>−4</span><span>−3</span>" in page.text
    assert "<span>−2</span><span>−1</span><span>Now</span>" in page.text
    assert "CPU history shown. Memory history unavailable." in page.text
    assert 'class="chart-cpu"' in page.text
    assert 'class="chart-memory"' not in page.text
    assert "8 logical CPUs" in page.text
    assert "Peak" in page.text and "50%" in page.text
    assert "Avg" in page.text and "38%" in page.text
    assert "Partial · 2 pts" in page.text


@pytest.mark.asyncio
async def test_cpu_summary_is_absent_when_history_and_core_count_are_missing():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query_range":
            rows = [] if "node_cpu_seconds_total" in request.url.params["query"] else [{
                "metric": {"instance": "192.0.2.10:9100"},
                "values": [[float(request.url.params["end"]), "60"]],
            }]
            return httpx.Response(200, json={
                "status": "success", "data": {"resultType": "matrix", "result": rows},
            })
        key = next(name for name, expression in QUERIES.items()
                   if expression == request.url.params["query"])
        values = {
            "up": 1, "up_timestamp": datetime.now(UTC).timestamp(),
            "cpu": 25, "memory_total": 1000, "memory_available": 400,
        }
        rows = ([{"metric": {"instance": "192.0.2.10:9100"},
                  "value": [0, str(values[key])]}] if key in values else [])
        return httpx.Response(200, json={
            "status": "success", "data": {"resultType": "vector", "result": rows},
        })

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(settings(), upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/servers")
    assert page.status_code == 200
    assert "logical CPU" not in page.text
    assert 'class="resource-summary"' not in page.text
    assert "CPU history unavailable. Memory history shown." in page.text
