"""Server-rendered dashboard and partial refresh routes."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from command_deck.collectors.healthchecks import HealthchecksCollection, HealthchecksCollector
from command_deck.collectors.prometheus import PrometheusCollection, PrometheusCollector
from command_deck.collectors.services import ServicesCollection, ServicesCollector
from command_deck.collectors.trends import STEP_SECONDS, HostTrend, TrendCollector
from command_deck.collectors.truenas import TrueNASPoolCollector, apply_pool_data
from command_deck.config import AppConfig, load_config
from command_deck.models import HostView, OverviewView, SummaryView, Telemetry
from command_deck.overview import build_overview
from command_deck.server_view import chart_path, trend_note
from command_deck.status_logging import configure_stdout_logging

PACKAGE = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE / "templates")
logger = logging.getLogger(__name__)


async def check_startup_connections(app: FastAPI, host_count: int) -> None:
    """Prime enabled source caches and report initial connection states in the background."""
    logger.info("Checking sources for %d configured hosts", host_count)
    names = ("Prometheus", "Healthchecks", "Uptime Kuma", "TrueNAS")
    results = await asyncio.gather(
        app.state.collector.collect(),
        app.state.healthchecks.collect(),
        app.state.services.collect(),
        app.state.truenas.collect(),
        return_exceptions=True,
    )
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("%s: startup check failed (%s)", name, type(result).__name__)


def create_app(config: AppConfig | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = config or load_config()

    def init_collectors(app: FastAPI, shared_client: httpx.AsyncClient) -> None:
        app.state.collector = PrometheusCollector(settings, shared_client)
        app.state.healthchecks = HealthchecksCollector(settings, shared_client)
        app.state.services = ServicesCollector(settings, shared_client)
        app.state.trends = TrendCollector(settings, shared_client)
        app.state.truenas = TrueNASPoolCollector(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_stdout_logging()
        if client is None:
            async with httpx.AsyncClient(trust_env=False) as owned_client:
                init_collectors(app, owned_client)
                startup_task = asyncio.create_task(
                    check_startup_connections(app, len(settings.hosts))
                )
                try:
                    yield
                finally:
                    startup_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await startup_task
        else:
            init_collectors(app, client)
            yield

    app = FastAPI(title="Command Deck", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(PACKAGE / "static" / "favicon.ico", media_type="image/x-icon")

    async def collect_overview(request: Request) -> OverviewView:
        (
            prometheus_result,
            healthchecks_result,
            services_result,
            pools_result,
        ) = await asyncio.gather(
            request.app.state.collector.collect(),
            request.app.state.healthchecks.collect(),
            request.app.state.services.collect(),
            request.app.state.truenas.collect(),
            return_exceptions=True,
        )
        if isinstance(prometheus_result, BaseException):
            logger.error(
                "Unexpected Prometheus collector failure (%s)", type(prometheus_result).__name__
            )
            prometheus_result = PrometheusCollection(
                source_state=Telemetry.UNHEALTHY,
                hosts=[
                    HostView(
                        id=host.id,
                        name=host.name,
                        show_on_overview=host.show_on_overview,
                        telemetry=Telemetry.UNHEALTHY,
                        reason="Prometheus collector failed",
                    )
                    for host in settings.hosts
                ],
                collected_at=datetime.now(UTC),
            )
        if isinstance(healthchecks_result, BaseException):
            logger.error(
                "Unexpected Healthchecks collector failure (%s)",
                type(healthchecks_result).__name__,
            )
            healthchecks_result = HealthchecksCollection(
                summary=SummaryView(
                    title="Backups",
                    telemetry=Telemetry.UNHEALTHY,
                    value="Healthchecks collector failed",
                ),
                issues=[],
                checks=[],
            )
        if isinstance(services_result, BaseException):
            logger.error(
                "Unexpected services collector failure (%s)", type(services_result).__name__
            )
            services_result = ServicesCollection(
                summary=SummaryView(
                    title="Services",
                    telemetry=Telemetry.UNHEALTHY,
                    value="Services collector failed",
                ),
                issues=[],
            )
        if isinstance(pools_result, BaseException):
            logger.error("Unexpected TrueNAS collector failure (%s)", type(pools_result).__name__)
            pools_result = None
        prometheus_result = replace(
            prometheus_result,
            hosts=[
                apply_pool_data(
                    host,
                    configured,
                    pools_result if configured.id == settings.truenas.host_id else None,
                )
                for host, configured in zip(prometheus_result.hosts, settings.hosts, strict=True)
            ],
        )
        overview = build_overview(prometheus_result, healthchecks_result, services_result)
        return overview

    async def overview_response(request: Request, template: str) -> HTMLResponse:
        overview = await collect_overview(request)
        return templates.TemplateResponse(
            request, template, {"overview": overview, "active_view": "overview"}
        )

    async def events_response(request: Request, template: str) -> HTMLResponse:
        overview = await collect_overview(request)
        return templates.TemplateResponse(
            request, template, {"overview": overview, "active_view": "events"}
        )

    async def services_response(request: Request, template: str) -> HTMLResponse:
        try:
            services = await request.app.state.services.collect()
        except Exception as exc:
            logger.error("Unexpected services collector failure (%s)", type(exc).__name__)
            services = ServicesCollection(
                summary=SummaryView(
                    title="Services",
                    telemetry=Telemetry.UNHEALTHY,
                    value="Services collector failed",
                ),
                issues=[],
            )
        return templates.TemplateResponse(
            request, template, {"services": services, "active_view": "services"}
        )

    async def servers_response(request: Request, template: str, host_id: str) -> HTMLResponse:
        configured = next((host for host in settings.hosts if host.id == host_id), None)
        if configured is None:
            raise HTTPException(status_code=404, detail="Unknown host")
        collection, trend, pools = await asyncio.gather(
            request.app.state.collector.collect(),
            request.app.state.trends.collect(configured),
            request.app.state.truenas.collect(),
            return_exceptions=True,
        )
        if isinstance(collection, BaseException):
            logger.error("Unexpected Prometheus collector failure (%s)", type(collection).__name__)
            hosts = [
                HostView(
                    id=host.id,
                    name=host.name,
                    show_on_overview=host.show_on_overview,
                    telemetry=Telemetry.UNHEALTHY,
                    reason="Prometheus collector failed",
                )
                for host in settings.hosts
            ]
        else:
            hosts = collection.hosts
        if isinstance(trend, BaseException):
            logger.error("Unexpected trend collector failure (%s)", type(trend).__name__)
            now = datetime.now(UTC).timestamp()
            trend = HostTrend(
                telemetry=Telemetry.UNHEALTHY,
                cpu=[],
                memory=[],
                start=now - 300,
                end=now,
                reason="Trend collector failed",
            )
        if isinstance(pools, BaseException):
            logger.error("Unexpected TrueNAS collector failure (%s)", type(pools).__name__)
            pools = None
        hosts = [
            apply_pool_data(
                host, setting, pools if setting.id == settings.truenas.host_id else None
            )
            for host, setting in zip(hosts, settings.hosts, strict=True)
        ]
        selected = next(host for host in hosts if host.id == host_id)
        cpu_values = [point.percent for point in trend.cpu]
        cpu_partial = bool(trend.cpu) and (
            len(trend.cpu) < 0.8 * ((trend.end - trend.start) / STEP_SECONDS + 1)
            or trend.cpu[0].timestamp > trend.start + 30
            or trend.cpu[-1].timestamp < trend.end - 30
            or any(
                later.timestamp - earlier.timestamp > 30
                for earlier, later in zip(trend.cpu, trend.cpu[1:], strict=False)
            )
        )
        return templates.TemplateResponse(
            request,
            template,
            {
                "hosts": hosts,
                "selected": selected,
                "trend": trend,
                "cpu_path": chart_path(trend.cpu, trend.start, trend.end),
                "memory_path": chart_path(trend.memory, trend.start, trend.end),
                "cpu_peak_percent": max(cpu_values, default=None),
                "cpu_average_percent": sum(cpu_values) / len(cpu_values) if cpu_values else None,
                "cpu_sample_count": len(cpu_values),
                "cpu_partial": cpu_partial,
                "trend_note": trend_note(trend),
                "active_view": "servers",
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        return await overview_response(request, "overview.html")

    @app.get("/partials/overview", response_class=HTMLResponse)
    async def overview_partial(request: Request) -> HTMLResponse:
        return await overview_response(request, "partials/overview.html")

    @app.get("/events", response_class=HTMLResponse)
    async def events(request: Request) -> HTMLResponse:
        return await events_response(request, "events.html")

    @app.get("/partials/events", response_class=HTMLResponse)
    async def events_partial(request: Request) -> HTMLResponse:
        return await events_response(request, "partials/events.html")

    @app.get("/services", response_class=HTMLResponse)
    async def services(request: Request) -> HTMLResponse:
        return await services_response(request, "services.html")

    @app.get("/partials/services", response_class=HTMLResponse)
    async def services_partial(request: Request) -> HTMLResponse:
        return await services_response(request, "partials/services.html")

    @app.get("/servers", response_class=HTMLResponse)
    async def servers(request: Request) -> HTMLResponse:
        return await servers_response(request, "servers.html", settings.hosts[0].id)

    @app.get("/servers/{host_id}", response_class=HTMLResponse)
    async def server_detail(request: Request, host_id: str) -> HTMLResponse:
        return await servers_response(request, "servers.html", host_id)

    @app.get("/partials/servers/{host_id}", response_class=HTMLResponse)
    async def servers_partial(request: Request, host_id: str) -> HTMLResponse:
        return await servers_response(request, "partials/servers.html", host_id)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
