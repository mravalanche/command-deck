"""Proxmox node metrics remain useful through partial data and independent failures."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from command_deck.collectors.proxmox import ProxmoxCollector
from command_deck.config import AppConfig
from command_deck.models import Health, Telemetry


def settings(*nodes: str) -> AppConfig:
    config = AppConfig.model_validate({
        "prometheus": {"url": "http://prometheus:9090"},
        "proxmox": {"url": "https://pve.example.invalid:8006", "token_id": "deck@pve!metrics"},
        "hosts": [
            {"id": node, "name": f"Node {node}", "source": "proxmox", "proxmox_node": node,
             "show_on_overview": False}
            for node in nodes or ("pve1",)
        ],
    })
    config.proxmox.token_secret = SecretStr("test-only-secret")
    return config


def node_data() -> dict:
    return {
        "cpu": 0.125,
        "cpuinfo": {"cpus": 16},
        "memory": {"used": 3072, "total": 4096},
        "rootfs": {"used": 70, "total": 100, "free": 30, "avail": 25},
    }


@pytest.mark.asyncio
async def test_node_status_authenticates_without_redirects_and_normalizes_capacity():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": node_data()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://pve.example.invalid:8006/api2/json/nodes/pve1/status"
    assert request.headers["Authorization"] == "PVEAPIToken=deck@pve!metrics=test-only-secret"
    assert request.extensions["timeout"]["read"] == 4
    assert host.name == "Node pve1" and host.show_on_overview is False
    assert host.telemetry == Telemetry.CONNECTED and host.telemetry_source == "Proxmox"
    assert host.health == Health.OK and host.metrics_current
    assert host.cpu_percent == 12.5 and host.logical_cpu_count == 16
    assert host.memory_percent == 75 and host.memory_used_bytes == 3072
    assert host.memory_total_bytes == 4096
    assert host.updated_at and abs((datetime.now(UTC) - host.updated_at).total_seconds()) < 5
    assert host.disks[0].mountpoint == "/"
    assert host.disks[0].available_bytes == 25  # Available excludes reserved filesystem space.
    assert host.disks[0].used_bytes == 70 and host.disks[0].total_bytes == 100
    assert host.disks[0].used_percent == 70
    assert "test-only-secret" not in host.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(("available", "health"), [
    (30, Health.OK), (15, Health.WARNING), (5, Health.CRITICAL), (0, Health.CRITICAL),
])
async def test_capacity_thresholds_use_available_space_even_with_partial_cpu(available, health):
    data = node_data()
    data["cpu"] = None
    data["rootfs"]["avail"] = available
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data})
    )) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert host.telemetry == Telemetry.UNHEALTHY and host.metrics_current
    assert host.cpu_percent is None and host.memory_percent == 75
    assert host.disks[0].health == health
    assert host.health == (Health.UNKNOWN if health == Health.OK else health)


@pytest.mark.asyncio
@pytest.mark.parametrize("rootfs", [
    None, {}, {"used": 70, "total": 100},
    {"used": True, "total": 100, "free": 30},
    {"used": 101, "total": 100, "free": 0},
    {"used": -1, "total": 100, "free": 30},
    {"used": 70, "total": 0, "free": 30},
    {"used": 70, "total": 100, "avail": None, "free": 30},
    {"used": 70, "total": 100, "free": 31},
])
async def test_invalid_root_capacity_does_not_hide_other_current_metrics(rootfs):
    data = node_data()
    data["rootfs"] = rootfs
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data})
    )) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert host.telemetry == Telemetry.UNHEALTHY and host.health == Health.UNKNOWN
    assert host.metrics_current and host.cpu_percent == 12.5 and host.memory_percent == 75
    assert host.disks[0].health == Health.UNKNOWN
    assert host.disks[0].available_bytes is None


@pytest.mark.asyncio
async def test_root_capacity_accepts_free_when_available_field_is_absent():
    data = node_data()
    del data["rootfs"]["avail"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data})
    )) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert host.telemetry == Telemetry.CONNECTED
    assert host.disks[0].available_bytes == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("cpu", [True, "0.2", -0.1, 1.01, float("nan"), float("inf")])
async def test_invalid_cpu_is_not_presented_as_a_current_percentage(cpu):
    import json

    data = node_data()
    data["cpu"] = cpu
    # Real invalid upstream JSON may include NaN; httpx's json= intentionally forbids it.
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=json.dumps({"data": data}))
    )) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert host.telemetry == Telemetry.UNHEALTHY and host.cpu_percent is None
    assert host.memory_percent == 75


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", [
    None, {"used": 0, "total": 0}, {"used": 5, "total": 4},
    {"used": False, "total": 4}, {"used": -1, "total": 4},
])
async def test_invalid_memory_never_becomes_a_percentage(memory):
    data = node_data()
    data["memory"] = memory
    data["cpuinfo"] = {"cpus": True}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data})
    )) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert host.telemetry == Telemetry.UNHEALTHY
    assert host.memory_percent is None and host.memory_used_bytes is None
    assert host.logical_cpu_count is None and host.cpu_percent == 12.5


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 404, 500])
async def test_http_errors_are_safe_and_redirects_do_not_receive_credentials(status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://other.example.invalid"},
                              text="private response test-only-secret")

    # Collector must override even a caller-supplied client's redirect-following default.
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                 follow_redirects=True) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert len(requests) == 1
    assert host.telemetry == Telemetry.UNHEALTHY
    assert not host.metrics_current and host.cpu_percent is None and host.updated_at is None
    assert host.telemetry_source == "Proxmox"
    assert "private response" not in host.model_dump_json()
    assert "test-only-secret" not in host.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["not json", "[]", '{"data":null}', '{"data":[]}'])
async def test_protocol_errors_are_unhealthy(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=body)
    )) as client:
        host = (await ProxmoxCollector(settings(), client).collect())["pve1"]
    assert host.telemetry == Telemetry.UNHEALTHY and not host.metrics_current


@pytest.mark.asyncio
async def test_node_failure_is_isolated_and_refreshes_replace_current_values():
    failing = False

    def respond(request):
        if request.url.path.endswith("/pve2/status") or failing:
            raise httpx.ConnectTimeout("private URL test-only-secret", request=request)
        return httpx.Response(200, json={"data": node_data()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = ProxmoxCollector(settings("pve1", "pve2"), client)
        hosts = await collector.collect()
        assert hosts["pve1"].telemetry == Telemetry.CONNECTED
        assert hosts["pve2"].telemetry == Telemetry.DISCONNECTED
        assert "test-only-secret" not in hosts["pve2"].model_dump_json()
        failing = True
        collector._cache = None
        failed = await collector.collect()
    assert failed["pve1"].telemetry == Telemetry.DISCONNECTED
    assert not failed["pve1"].metrics_current and failed["pve1"].cpu_percent is None


@pytest.mark.asyncio
async def test_cache_and_concurrent_requests_share_one_refresh():
    calls = 0

    async def respond(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json={"data": node_data()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = ProxmoxCollector(settings(), client)
        results = await asyncio.gather(*(collector.collect() for _ in range(5)))
        assert results[0] is await collector.collect()
    assert calls == 1 and all(item is results[0] for item in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["url", "token_id", "token_secret", "node"])
async def test_missing_setup_does_not_make_network_requests(missing):
    config = settings()
    if missing == "node":
        config.hosts[0].proxmox_node = None
    else:
        setattr(config.proxmox, missing, None)

    def unexpected(request):
        pytest.fail("Unconfigured Proxmox should not make requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        host = (await ProxmoxCollector(config, client).collect())["pve1"]
    assert host.telemetry == Telemetry.NOT_CONFIGURED and host.telemetry_source == "Proxmox"
    assert not host.metrics_current


@pytest.mark.asyncio
async def test_other_host_sources_are_excluded():
    config = AppConfig.model_validate({
        "prometheus": {"url": "http://prometheus:9090"},
        "hosts": [{"id": "server", "name": "Server"}],
    })
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: pytest.fail("No Proxmox hosts should cause no requests")
    )) as client:
        assert await ProxmoxCollector(config, client).collect() == {}
