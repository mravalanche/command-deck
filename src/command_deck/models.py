"""Normalized dashboard values shared by collectors and templates."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Health(StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class Telemetry(StrEnum):
    CONNECTED = "CONNECTED"
    UNHEALTHY = "UNHEALTHY"
    DISCONNECTED = "DISCONNECTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class DiskView(BaseModel):
    label: str
    mountpoint: str
    used_bytes: int | None = None
    total_bytes: int | None = None
    available_bytes: int | None = None
    used_percent: float | None = None
    health: Health = Health.UNKNOWN
    reason: str | None = None


class HostView(BaseModel):
    id: str
    name: str
    show_on_overview: bool = True
    telemetry: Telemetry
    health: Health = Health.UNKNOWN
    metrics_current: bool = False
    cpu_percent: float | None = None
    logical_cpu_count: int | None = None
    memory_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    disks: list[DiskView] = Field(default_factory=list)
    updated_at: datetime | None = None
    reason: str | None = None


class SummaryView(BaseModel):
    title: str
    telemetry: Telemetry = Telemetry.NOT_CONFIGURED
    health: Health = Health.UNKNOWN
    value: str = "Not connected yet"
    caption: str | None = None
    updated_at: datetime | None = None


class IssueView(BaseModel):
    severity: Health
    title: str
    detail: str
    source: str = ""
    observed_at: datetime | None = None


class OverviewView(BaseModel):
    health: Health
    headline: str
    attention_health: Health
    attention_headline: str
    detail: str
    issues: list[IssueView] = Field(default_factory=list)
    hosts: list[HostView]
    backups: SummaryView
    services: SummaryView
    generated_at: datetime
