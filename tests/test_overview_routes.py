"""The real HTTP routes render source data and remain usable through outages."""

import struct

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
            layout_check = await browser.get("/?layout-check=1")
            layout_check_script = await browser.get("/static/layout-check.js")
            layout_check_style = await browser.get("/static/layout-check.css")
            fragment = await browser.get("/partials/overview")
            events = await browser.get("/events")
            health = await browser.get("/healthz")
            raw_api = await browser.get("/api/v1/query")
            manifest_response = await browser.get("/static/manifest.webmanifest")
            favicon_response = await browser.get("/favicon.ico")
            icon_responses = {
                size: await browser.get(f"/static/icon-{size}.png") for size in (192, 512)
            }
            apple_icon_response = await browser.get("/static/apple-touch-icon.png")

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
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in page.text
    assert 'rel="apple-touch-icon" href="/static/apple-touch-icon.png"' in page.text
    assert 'href="/static/favicon.svg"' in page.text
    assert 'href="/static/favicon-32.png"' in page.text
    assert 'href="?layout-check=1"' in page.text
    assert "/static/layout-check.js?v=ios-layout-probe-3" not in page.text
    assert "/static/layout-check.js?v=ios-layout-probe-3" in layout_check.text
    assert 'data-layout-revision="ios-layout-probe-3"' in layout_check.text
    assert layout_check_script.status_code == layout_check_style.status_code == 200
    assert manifest_response.headers["content-type"].startswith("application/manifest+json")
    manifest = manifest_response.json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
    assert favicon_response.status_code == 200
    assert favicon_response.content[:4] == b"\x00\x00\x01\x00"
    for size, response in (*icon_responses.items(), (180, apple_icon_response)):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", response.content[16:24]) == (size, size)


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


def test_example_config_has_order_and_illustrative_targets():
    config = load_config()
    assert [host.name for host in config.hosts] == ["Server", "NAS"]
    assert all(host.instance and host.instance.split(":", 1)[0].endswith(".example.invalid")
               for host in config.hosts)


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
    gib = 1024 ** 3
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "hosts": [
                {
                    "id": "server",
                    "name": "󰒋 Server",
                    "instance": "192.0.2.10:9100",
                    "filesystems": [
                        {"mountpoint": "/", "label": "System", "icon": "󰋊"},
                        {"mountpoint": "/data", "label": "Data"},
                    ],
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
            "filesystem_size": 100 * gib,
            "filesystem_free": 10 * gib,
            "filesystem_available": 3 * gib,
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
            details = await browser.get("/servers/server")
    assert page.status_code == 200
    assert details.status_code == 200
    assert "23%" in page.text
    assert "65%" in page.text
    for html in (page.text, details.text):
        assert "󰒋 Server" in html
        assert '<span class="drive-icon" aria-hidden="true">󰋊</span>System' in html
        assert '<span class="drive-icon" aria-hidden="true">\uf1c0</span>Data' in html
        assert html.count('class="drive-icon"') == 2
        assert "90 / 100 GiB" in html
    assert "3.0 GiB available" not in page.text
    assert "3.0 GiB available" in details.text
    assert "Used / total" not in details.text
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
    assert "Attention needed" in page.text
    assert "0 / 1" in page.text
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


@pytest.mark.asyncio
async def test_services_tab_and_overview_card_show_named_monitors_without_target_urls():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "uptime_kuma": {"url": "http://kuma:3001", "enabled": True},
            "hosts": [{"id": "server", "name": "Server"}],
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            return httpx.Response(
                200,
                text=(
                    'monitor_status{monitor_id="1",monitor_name="Web",'
                    'monitor_url="https://private.example/token"} 1\n'
                    'monitor_status{monitor_id="2",monitor_name="Database",'
                    'monitor_url="https://private.example/database"} 0\n'
                ),
            )
        raise httpx.ConnectTimeout("source unavailable", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(config, upstream)
    async with upstream, app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as browser:
            overview = await browser.get("/")
            page = await browser.get("/services")
            fragment = await browser.get("/partials/services")

    assert overview.status_code == page.status_code == fragment.status_code == 200
    assert 'class="summary summary-link' in overview.text
    assert 'href="/services"' in overview.text
    assert 'href="/services" class="active" aria-current="page"' in page.text
    assert "Database" in page.text and "Down" in page.text
    assert "Web" in page.text and "Up" in page.text
    assert page.text.index("Database") < page.text.index("Web")
    assert "private.example" not in page.text
    assert '<html' not in fragment.text and 'id="services-live"' in fragment.text


@pytest.mark.asyncio
async def test_services_tab_explains_unavailable_metrics():
    config = AppConfig.model_validate(
        {
            "prometheus": {"url": "http://prometheus:9090"},
            "uptime_kuma": {"url": "http://kuma:3001", "enabled": True},
            "hosts": [{"id": "server", "name": "Server"}],
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
            page = await browser.get("/services")

    assert page.status_code == 200
    assert "Uptime Kuma is unreachable" in page.text
    assert "Service details will appear when Uptime Kuma metrics are available." in page.text
    assert 'class="service-row' not in page.text
