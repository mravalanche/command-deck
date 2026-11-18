"""Local history preserves source timing, bounded retention, and sampling lifecycle."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from command_deck.collectors.prometheus import QUERIES, PrometheusCollector
from command_deck.collectors.proxmox import ProxmoxCollector
from command_deck.collectors.trends import TrendCollector, sample_forever
from command_deck.config import AppConfig
from command_deck.main import create_app
from command_deck.models import HostView, Telemetry


def settings(**extra) -> AppConfig:
    return AppConfig.model_validate({
        "hosts": [{"id": "server", "name": "Server", "instance": "host:9100"}], **extra,
    })


def reading(stamp, *, cpu=25, memory=60, current=True, state=Telemetry.CONNECTED):
    return HostView(
        id="server", name="Server", telemetry=state, metrics_current=current,
        cpu_percent=cpu, memory_percent=memory,
        updated_at=datetime.fromtimestamp(stamp, UTC),
        cpu_updated_at=datetime.fromtimestamp(stamp, UTC),
        memory_updated_at=datetime.fromtimestamp(stamp, UTC),
    )


@pytest.mark.asyncio
async def test_full_default_window_rollover_and_independent_reads(monkeypatch):
    config = settings()
    collector = TrendCollector(config)
    for stamp in range(1000, 1601, 10):
        collector.record([reading(stamp)], now=stamp)
    monkeypatch.setattr("command_deck.collectors.trends.time.time", lambda: 1600)
    trend = await collector.collect(config.hosts[0])
    assert len(trend.cpu) == len(trend.memory) == 61
    assert trend.start == 1000 and trend.end == 1600 and trend.interval_seconds == 10
    assert [point.timestamp for point in trend.cpu] == list(range(1000, 1601, 10))
    assert (await collector.collect(config.hosts[0])).cpu == trend.cpu
    collector.record([reading(1610)], now=1610)
    monkeypatch.setattr("command_deck.collectors.trends.time.time", lambda: 1610)
    assert (await collector.collect(config.hosts[0])).cpu[0].timestamp == 1010


@pytest.mark.asyncio
async def test_custom_window_and_interval_and_bounded_memory(monkeypatch):
    config = settings(history={"interval_seconds": 30, "window_seconds": 300})
    collector = TrendCollector(config)
    for stamp in range(900, 1531, 30):
        collector.record([reading(stamp)], now=stamp)
    monkeypatch.setattr("command_deck.collectors.trends.time.time", lambda: 1530)
    trend = await collector.collect(config.hosts[0])
    assert len(trend.cpu) <= 11
    assert trend.start == 1230 and trend.interval_seconds == 30
    assert all(point.timestamp >= 1230 for point in trend.cpu)


@pytest.mark.asyncio
async def test_duplicate_out_of_order_stale_and_missing_metric_timestamps(monkeypatch):
    config = settings()
    collector = TrendCollector(config)
    collector.record([reading(1000)], now=1000)
    collector.record([reading(1000, cpu=99)], now=1010)
    collector.record([reading(990)], now=1010)
    collector.record([reading(900)], now=1010)
    missing = reading(1010)
    missing.cpu_updated_at = None
    collector.record([missing], now=1010)
    partial = reading(1020)
    partial.cpu_updated_at = datetime.fromtimestamp(900, UTC)
    collector.record([partial], now=1020)
    monkeypatch.setattr("command_deck.collectors.trends.time.time", lambda: 1020)
    trend = await collector.collect(config.hosts[0])
    assert [p.timestamp for p in trend.cpu] == [1000]
    assert [p.timestamp for p in trend.memory] == [1000, 1010, 1020]
    assert trend.cpu[0].percent == 25
    collector.record([reading(1020, current=False, state=Telemetry.DISCONNECTED)], now=1030)
    assert "retained history" in (await collector.collect(config.hosts[0])).reason
    monkeypatch.setattr("command_deck.collectors.trends.time.time", lambda: 1700)
    assert not (await collector.collect(config.hosts[0])).has_data


@pytest.mark.asyncio
async def test_sampler_keeps_cadence_recovers_and_cancels(monkeypatch):
    clock = SimpleNamespace(now=0)
    samples = []
    sleeps = []

    async def sample():
        samples.append(clock.now)
        if len(samples) == 1:
            raise ValueError("one failed sample must not stop background collection")
        if len(samples) == 2:
            clock.now += 25  # slow sources skip ticks instead of overlapping

    async def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds
        if len(sleeps) == 4:
            raise asyncio.CancelledError

    monkeypatch.setattr("command_deck.collectors.trends.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("command_deck.collectors.trends.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await sample_forever(sample, 10)
    assert samples == [0, 10, 40, 50]
    assert sleeps == [10, 5, 10, 10]


@pytest.mark.parametrize("history", [
    {"interval_seconds": 4}, {"interval_seconds": 31}, {"interval_seconds": float("nan")},
    {"window_seconds": 299}, {"window_seconds": 3601}, {"window_seconds": float("inf")},
])
def test_history_config_rejects_invalid_bounds(history):
    with pytest.raises(ValidationError):
        settings(history=history)


@pytest.mark.asyncio
async def test_node_cpu_uses_last_two_scrapes_and_preserves_metric_source_times():
    assert "irate(" in QUERIES["cpu"] and "[5m]" not in QUERIES["cpu"]
    config = settings()
    values = {"up": 1, "up_timestamp": 1000, "cpu": 25, "cpu_count": 4,
              "cpu_timestamp": 990, "memory_timestamp": 995,
              "memory_total": 1000, "memory_available": 400}

    def respond(request):
        key = next(k for k, v in QUERIES.items() if v == request.url.params["query"])
        rows = [{"metric": {"instance": "host:9100"}, "value": [1050, str(values.get(key, 0))]}]
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": "vector", "result": rows,
        }})

    # Host time check uses datetime; query timestamps must remain the source values.
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = PrometheusCollector(config, client)
        data = {key: await collector._query(expression) for key, expression in QUERIES.items()}
        host = collector._host(config.hosts[0], data, Telemetry.CONNECTED,
                               datetime.fromtimestamp(1000, UTC))
    assert host.cpu_updated_at.timestamp() == 990
    assert host.memory_updated_at.timestamp() == 995
    assert host.updated_at.timestamp() == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("provided_client", [True, False])
async def test_background_samples_all_sources_without_browser_and_isolates_failures(
    monkeypatch, provided_client,
):
    config = settings()
    calls = []
    sampled = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_prom(self, *, force_refresh=False):
        calls.append(("prometheus", force_refresh))
        raise RuntimeError("one collector failed")

    async def fake_proxmox(self, *, force_refresh=False):
        calls.append(("proxmox", force_refresh))
        return {"server": reading(datetime.now(UTC).timestamp())}

    async def controlled_sampler(sample, interval):
        assert interval == 10
        try:
            await sample()
            sampled.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(PrometheusCollector, "collect", fake_prom)
    monkeypatch.setattr(ProxmoxCollector, "collect", fake_proxmox)
    monkeypatch.setattr("command_deck.main.sample_forever", controlled_sampler)
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        app = create_app(config, client if provided_client else None)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(sampled.wait(), 1)
            trend = await app.state.trends.collect(config.hosts[0])
            assert trend.cpu[0].percent == 25
            assert [call for call in calls if call[1]] == [
                ("prometheus", True), ("proxmox", True),
            ]
            assert not cancelled.is_set()
        assert cancelled.is_set()


@pytest.mark.asyncio
async def test_routes_never_fetch_range_history_and_missing_metric_timestamps_leave_gaps():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert request.url.path == "/api/v1/query"
        key = next(k for k, v in QUERIES.items() if v == request.url.params["query"])
        values = {"up": 1, "up_timestamp": datetime.now(UTC).timestamp(),
                  "cpu": 25, "cpu_count": 8, "memory_total": 1000, "memory_available": 400}
        rows = ([{"metric": {"instance": "host:9100"}, "value": [0, str(values[key])]}]
                if key in values else [])
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": "vector", "result": rows,
        }})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(settings(), upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://testserver") as browser:
            page = await browser.get("/servers")
            trend = await app.state.trends.collect(settings().hosts[0])
            assert not trend.has_data
            assert page.status_code == 200 and "25%" in page.text
            assert 'class="chart-cpu"' not in page.text
            assert 'hx-trigger="every 10' in page.text
    assert calls and not any("query_range" in path for path in calls)


@pytest.mark.asyncio
async def test_forced_sampling_bypasses_host_request_caches_and_pins_prometheus_snapshot():
    from pydantic import SecretStr

    from command_deck.config import ProxmoxConfig

    requests = []
    config = settings()
    config.hosts.append(type(config.hosts[0])(
        id="pve", name="PVE", source="proxmox", proxmox_node="pve1",
    ))
    config.proxmox = ProxmoxConfig(url="https://pve:8006", token_id="reader@pve!deck",
                                   token_secret=SecretStr("test-only"))

    def respond(request):
        requests.append(request)
        if request.url.host == "pve":
            return httpx.Response(200, json={"data": {
                "cpu": 0.2, "memory": {"used": 50, "total": 100},
                "rootfs": {"used": 20, "total": 100, "avail": 80},
            }})
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": "vector", "result": [],
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        prom = PrometheusCollector(config, client)
        prox = ProxmoxCollector(config, client)
        await prom.collect()
        await prox.collect()
        count = len(requests)
        await prom.collect()
        await prox.collect()
        assert len(requests) == count
        await prom.collect(force_refresh=True)
        await prox.collect(force_refresh=True)
        assert len(requests) == 2 * count
    prom_requests = [request for request in requests if request.url.host != "pve"]
    assert len({request.url.params["time"] for request in prom_requests[:len(QUERIES)]}) == 1
    assert all("time" in request.url.params for request in prom_requests)


@pytest.mark.asyncio
async def test_graphite_history_times_cannot_be_fresher_than_last_processed():
    from command_deck.collectors.prometheus import QueryData, Sample

    config = settings(hosts=[{
        "id": "server", "name": "NAS", "source": "truenas_graphite",
        "instance": "graphite:9108", "graphite_prefix": "nas_metrics",
    }])
    values = {"up": 1, "last_processed": 990, "cpu": 30,
              "cpu_timestamp": 1000, "memory_timestamp": 995,
              "memory_total": 100, "memory_available": 40}
    data = {f"server_{key}": QueryData([Sample({"instance": "graphite:9108"}, value)])
            for key, value in values.items()}
    async with httpx.AsyncClient() as client:
        host = PrometheusCollector(config, client)._host(
            config.hosts[0], data, Telemetry.CONNECTED, datetime.fromtimestamp(1000, UTC),
        )
    assert host.cpu_updated_at.timestamp() == host.memory_updated_at.timestamp() == 990
