"""The real HTTP routes render source data and remain usable through outages."""

import httpx
import pytest
from pydantic import SecretStr

from command_deck.collectors.prometheus import QUERIES
from command_deck.config import AppConfig, HealthchecksConfig, load_config
from command_deck.main import create_app


@pytest.mark.asyncio
async def test_overview_and_partial_show_unknown_without_monitoring_api_exposure():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [
                {"id": "server", "name": "Server", "instance": "192.0.2.10:9100"},
                {"id": "nas", "name": "NAS", "instance": None},
            ],
        }
    )

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("source unavailable", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/")
            fragment = await browser.get("/partials/overview")
            events = await browser.get("/events")
            health = await browser.get("/healthz")
            raw_api = await browser.get("/api/v1/query")

    assert page.status_code == 200
    assert "Attention needed" in page.text
    assert "attention--critical" in page.text
    assert "Server" in page.text and "NAS" in page.text
    assert "DISCONNECTED" in page.text and "NOT CONFIGURED" in page.text
    assert "Backups" in page.text and "Services" in page.text
    assert "<html" not in fragment.text
    assert 'id="overview-live"' in fragment.text
    assert health.json() == {"status": "ok"}
    assert raw_api.status_code == 404
    assert events.status_code == 200
    assert "Current signals" in events.text
    assert "Prometheus" in events.text
    assert "Failure and recovery history is not connected yet" in events.text


@pytest.mark.asyncio
async def test_partial_prometheus_response_keeps_current_metrics_visible():
    from datetime import UTC, datetime

    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [
                {
                    "id": "server",
                    "name": "Server",
                    "instance": "192.0.2.10:9100",
                    "filesystems": [{"mountpoint": "/", "label": "System"}],
                }
            ],
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        if request.url.path == "/api/v1/query_range":
            return httpx.Response(
                200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}
            )
        key = next(name for name, expression in QUERIES.items() if expression == query)
        values = {
            "up": 1,
            "up_timestamp": datetime.now(UTC).timestamp(),
            "cpu": 23,
            "memory_total": 1000,
            "memory_available": 350,
        }
        labels = {"instance": "192.0.2.10:9100"}
        rows = [{"metric": labels, "value": [0, str(values[key])]}] if key in values else []
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": rows}}
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            overview = await browser.get("/")
            servers = await browser.get("/servers")

    for page in (overview, servers):
        assert page.status_code == 200
        assert "23%" in page.text
        assert "65%" in page.text
        assert "UNKNOWN" in page.text


def test_example_config_has_order_and_no_live_target():
    config = load_config()
    assert [host.name for host in config.hosts] == ["Server", "NAS"]
    assert all(host.instance is None for host in config.hosts)


@pytest.mark.asyncio
async def test_overview_selection_links_to_requested_server_even_during_source_failure():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [
                {"id": "server", "name": "Server", "show_on_overview": False},
                {"id": "nas", "name": "NAS", "show_on_overview": True},
            ],
        }
    )

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("source unavailable", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            overview = await browser.get("/")
            nas = await browser.get("/servers/nas")
            unknown = await browser.get("/servers/unknown")

    assert overview.status_code == 200
    assert 'href="/servers/nas"' in overview.text
    assert 'href="/servers/server"' not in overview.text
    assert nas.status_code == 200
    assert 'aria-label="NAS details"' in nas.text
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_overview_renders_live_prometheus_values_and_capacity_issue():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [
                {
                    "id": "server",
                    "name": "Server",
                    "instance": "192.0.2.10:9100",
                    "filesystems": [{"mountpoint": "/", "label": "System"}],
                }
            ],
        }
    )

    def prometheus(request: httpx.Request) -> httpx.Response:
        from datetime import UTC, datetime

        query = request.url.params["query"]
        key = next(name for name, expression in QUERIES.items() if expression == query)
        values = {
            "up": 1,
            "up_timestamp": datetime.now(UTC).timestamp(),
            "cpu": 23,
            "memory_total": 1000,
            "memory_available": 350,
            "filesystem_size": 1000,
            "filesystem_free": 100,
            "filesystem_available": 30,
        }
        labels = {"instance": "192.0.2.10:9100"}
        if key.startswith("filesystem_"):
            labels["mountpoint"] = "/"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": labels, "value": [0, str(values[key])]}],
                },
            },
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(prometheus))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/")
    assert page.status_code == 200
    assert "23%" in page.text
    assert "65%" in page.text
    assert "System" in page.text
    assert "Only 3.0% available" in page.text
    assert "NOT CONFIGURED" in page.text  # Backups and services remain unknown.


@pytest.mark.asyncio
async def test_backup_failure_surfaces_when_prometheus_is_unreachable():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [{"id": "server", "name": "Server", "instance": "192.0.2.10:9100"}],
        }
    )
    config.healthchecks = HealthchecksConfig(api_key=SecretStr("test-only-key"))

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/checks/":
            return httpx.Response(
                200,
                json={"checks": [{"name": "Backup job", "status": "down", "last_ping": None}]},
            )
        raise httpx.ConnectTimeout("Prometheus unavailable", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/")
            events = await browser.get("/events")
    assert page.status_code == 200
    assert "Backup job" in page.text
    assert "missed or failed" in page.text
    assert "DISCONNECTED" in page.text
    assert events.status_code == 200
    assert "Backup job" in events.text
    assert "Healthchecks" in events.text


@pytest.mark.asyncio
async def test_empty_backup_tag_is_explained_on_overview():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [{"id": "server", "name": "Server", "instance": None}],
        }
    )
    config.healthchecks = HealthchecksConfig(api_key=SecretStr("test-only-key"))

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/checks/":
            return httpx.Response(200, json={"checks": []})
        raise httpx.ConnectTimeout("Prometheus unavailable", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            page = await browser.get("/")
    assert page.status_code == 200
    assert "No backup checks matched the configured tag" in page.text
    assert "Telemetry issue" not in page.text
