"""TrueNAS storage uses root dataset capacity and keeps failures explicit."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from command_deck.collectors.truenas import TrueNASPoolCollector, _pool_disk, apply_pool_data
from command_deck.config import AppConfig, TrueNASConfig
from command_deck.main import templates
from command_deck.models import Health, HostView, OverviewView, SummaryView, Telemetry


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "truenas": {"host_id": "nas"},
            "hosts": [{"id": "nas", "name": "NAS", "source": "truenas_graphite",
                       "pools": ["tank"]}],
        }
    )


@pytest.mark.asyncio
async def test_pool_query_authenticates_and_reads_only_selected_metadata(monkeypatch):
    config = _config()
    config.truenas = TrueNASConfig(
        host_id="nas", url="https://nas.example.invalid", username="reader",
        api_key=SecretStr("test-key")
    )
    sent = []

    class Socket:
        async def send(self, payload):
            sent.append(json.loads(payload))

        async def recv(self):
            if len(sent) == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "result": {"response_type": "SUCCESS"}})
            if len(sent) == 2:
                return json.dumps({"jsonrpc": "2.0", "id": 2, "result": [
                {"name": "tank",
                 "healthy": True, "warning": False, "status": "ONLINE"},
                {"name": "other",
                 "healthy": True, "warning": False, "status": "ONLINE"},
                ]})
            return json.dumps({"jsonrpc": "2.0", "id": 3, "result": [
                {"id": "tank", "used": {"parsed": 700}, "available": {"parsed": 200}},
                {"id": "other", "used": {"parsed": 300}, "available": {"parsed": 600}},
            ]})

    class Connection:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *_):
            return False

    def connect(url, **kwargs):
        assert url == "wss://nas.example.invalid/api/current"
        assert kwargs["max_size"] <= 1024 * 1024
        return Connection()

    monkeypatch.setattr("command_deck.collectors.truenas.connect", connect)
    collection = await TrueNASPoolCollector(config).collect()
    assert collection is not None
    assert [call["method"] for call in sent] == [
        "auth.login_ex", "pool.query", "pool.dataset.query"
    ]
    assert sent[1]["params"][1]["select"] == [
        "name", "healthy", "warning", "status"
    ]
    assert sent[2]["params"][1]["extra"]["properties"] == ["used", "available"]
    assert sent[2]["params"][1]["extra"]["retrieve_children"] is False
    host = HostView(id="nas", name="NAS", telemetry=Telemetry.CONNECTED, health=Health.OK)
    host = apply_pool_data(host, config.hosts[0], collection)
    assert len(host.disks) == 1
    assert host.disks[0].label == "tank"
    assert host.disks[0].used_percent == 77.8
    assert host.disks[0].total_bytes == 900
    assert host.disks[0].available_bytes == 200
    assert host.health == Health.OK


def test_truenas_requires_https_and_marks_faulted_pool_critical():
    with pytest.raises(ValidationError):
        TrueNASConfig(url="http://nas.example.invalid")
    from command_deck.collectors.truenas import PoolCollection

    config = _config()
    disk = _pool_disk({"name": "tank", "healthy": False, "warning": True,
                       "status": "FAULTED"},
                      {"used": {"parsed": 700}, "available": {"parsed": 300}}, config)
    assert disk is not None and disk.health == Health.CRITICAL
    host = HostView(id="nas", name="NAS", telemetry=Telemetry.CONNECTED, health=Health.OK)
    merged = apply_pool_data(host, config.hosts[0], PoolCollection([disk]))
    assert merged.health == Health.CRITICAL
    missing_status = _pool_disk(
        {"name": "tank"},
        {"used": {"parsed": 700}, "available": {"parsed": 300}}, config
    )
    assert missing_status is not None and missing_status.health == Health.UNKNOWN


def test_missing_dataset_capacity_does_not_fall_back_to_pool_physical_size():
    config = _config()
    disk = _pool_disk({"name": "tank", "size": 1000, "allocated": 700, "free": 300,
                       "healthy": True, "warning": False, "status": "ONLINE"},
                      {"used": {"parsed": 700}, "available": {"parsed": None}}, config)
    assert disk is not None
    assert disk.available_bytes is None
    assert disk.total_bytes is None
    assert disk.health == Health.UNKNOWN
    faulted = _pool_disk({"name": "tank", "healthy": False, "status": "FAULTED"},
                         None, config)
    assert faulted is not None and faulted.health == Health.CRITICAL


def test_overview_keeps_compact_dataset_capacity():
    gib = 1024 ** 3
    disk = _pool_disk({"name": "tank", "healthy": True, "warning": False,
                       "status": "ONLINE"},
                      {"used": {"parsed": 70 * gib},
                       "available": {"parsed": 20 * gib}}, _config())
    assert disk is not None
    host = HostView(id="nas", name="NAS", telemetry=Telemetry.CONNECTED,
                    metrics_current=True, disks=[disk])
    overview = OverviewView(health=Health.OK, headline="Healthy", attention_health=Health.OK,
                            attention_headline="Healthy", detail="Healthy", hosts=[host],
                            backups=SummaryView(title="Backups"),
                            services=SummaryView(title="Services"),
                            generated_at=datetime.now(UTC))
    html = templates.env.get_template("partials/overview.html").render(overview=overview)
    assert "20.0 GiB available" not in html
    assert "70 / 90 GiB" in html


def test_pool_display_labels_do_not_change_pool_selection():
    from command_deck.collectors.truenas import PoolCollection

    config = AppConfig.model_validate({
        "prometheus": {"url": "http://prometheus:9090"},
        "hosts": [{"id": "nas", "name": "󰋊 NAS", "source": "truenas_graphite",
                   "pools": ["tank", "missing"],
                   "pool_labels": {"tank": "󰋊 Archive", "missing": "󰋊 Offline"}}],
    })
    host = HostView(id="nas", name=config.hosts[0].name,
                    telemetry=Telemetry.CONNECTED, health=Health.OK)
    disk = _pool_disk({"name": "tank", "healthy": True, "warning": False,
                       "status": "ONLINE"},
                      {"used": {"parsed": 70}, "available": {"parsed": 30}}, config)
    assert disk is not None
    result = apply_pool_data(host, config.hosts[0], PoolCollection([disk]))
    assert [(item.mountpoint, item.label) for item in result.disks] == [
        ("pool:tank", "󰋊 Archive"), ("pool:missing", "󰋊 Offline")
    ]
