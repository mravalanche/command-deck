"""Non-secret application configuration and environment-only credentials."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class PrometheusConfig(BaseModel):
    url: str
    timeout_seconds: float = Field(default=3, gt=0, le=10)
    cache_seconds: float = Field(default=20, ge=5, le=120)
    max_sample_age_seconds: float = Field(default=90, ge=30, le=300)
    bearer_token: SecretStr | None = Field(default=None, exclude=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Prometheus URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("Prometheus URL must not contain credentials, a query, or a fragment")
        return value.rstrip("/")


class CapacityConfig(BaseModel):
    warning_available_percent: float = Field(default=15, gt=0, lt=100)
    critical_available_percent: float = Field(default=5, gt=0, lt=100)

    @model_validator(mode="after")
    def check_order(self) -> "CapacityConfig":
        if self.critical_available_percent >= self.warning_available_percent:
            raise ValueError("Critical available percent must be below warning percent")
        return self


class FilesystemConfig(BaseModel):
    mountpoint: str
    label: str

    @field_validator("mountpoint")
    @classmethod
    def validate_mountpoint(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("Filesystem mountpoints must be absolute paths")
        return value


class HostConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    show_on_overview: bool = True
    instance: str | None = None
    source: Literal["node_exporter", "truenas_graphite"] = "node_exporter"
    graphite_prefix: str | None = Field(default=None, pattern=r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
    pools: list[str] = Field(default_factory=list)
    pool_labels: dict[str, str] = Field(default_factory=dict)
    filesystems: list[FilesystemConfig] = Field(default_factory=list)

    @field_validator("pool_labels")
    @classmethod
    def validate_pool_labels(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not name or not label.strip() for name, label in value.items()):
            raise ValueError("Pool names and display labels must not be empty")
        return value

    @model_validator(mode="after")
    def unique_filesystems(self) -> "HostConfig":
        if self.source == "truenas_graphite":
            if self.instance and not self.graphite_prefix:
                raise ValueError("A configured TrueNAS Graphite host needs graphite_prefix")
            if self.filesystems:
                raise ValueError("TrueNAS Graphite has no verified filesystem capacity metrics")
        elif self.graphite_prefix:
            raise ValueError("graphite_prefix only applies to TrueNAS Graphite hosts")
        elif self.pools:
            raise ValueError("pools only applies to TrueNAS Graphite hosts")
        elif self.pool_labels:
            raise ValueError("pool_labels only applies to TrueNAS Graphite hosts")
        if len(self.pools) != len(set(self.pools)):
            raise ValueError(f"Host {self.id} has duplicate pool names")
        mounts = [filesystem.mountpoint for filesystem in self.filesystems]
        if len(mounts) != len(set(mounts)):
            raise ValueError(f"Host {self.id} has duplicate filesystem mountpoints")
        return self


class HealthchecksConfig(BaseModel):
    url: str = "http://healthchecks:8000"
    backup_tag: str = Field(default="backup", min_length=1)
    timeout_seconds: float = Field(default=3, gt=0, le=10)
    cache_seconds: float = Field(default=30, ge=10, le=120)
    api_key: SecretStr | None = Field(default=None, exclude=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Healthchecks URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError(
                "Healthchecks URL must not contain credentials, a query, or a fragment"
            )
        return value.rstrip("/")


class UptimeKumaConfig(BaseModel):
    url: str = "http://uptime-kuma:3001"
    enabled: bool = False
    timeout_seconds: float = Field(default=3, gt=0, le=10)
    cache_seconds: float = Field(default=30, ge=10, le=120)
    api_key: SecretStr | None = Field(default=None, exclude=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Uptime Kuma URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("Uptime Kuma URL must not contain credentials, a query, or a fragment")
        return value.rstrip("/")


class TrueNASConfig(BaseModel):
    host_id: str | None = None
    url: str | None = None
    username: str | None = None
    api_key: SecretStr | None = Field(default=None, exclude=True)
    timeout_seconds: float = Field(default=4, gt=0, le=10)
    cache_seconds: float = Field(default=30, ge=10, le=120)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("TrueNAS URL must be an HTTPS origin without credentials or path")
        return value.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.host_id and self.url and self.username and self.api_key)


class AppConfig(BaseModel):
    prometheus: PrometheusConfig
    healthchecks: HealthchecksConfig = Field(default_factory=HealthchecksConfig)
    uptime_kuma: UptimeKumaConfig = Field(default_factory=UptimeKumaConfig)
    truenas: TrueNASConfig = Field(default_factory=TrueNASConfig)
    capacity: CapacityConfig = Field(default_factory=CapacityConfig)
    hosts: list[HostConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_hosts(self) -> "AppConfig":
        ids = [host.id for host in self.hosts]
        instances = [host.instance for host in self.hosts if host.instance]
        if len(ids) != len(set(ids)) or len(instances) != len(set(instances)):
            raise ValueError("Host IDs and configured Prometheus instances must be unique")
        if sum(host.show_on_overview for host in self.hosts) > 2:
            raise ValueError("At most two hosts can appear on Overview")
        if self.truenas.host_id and not any(
            host.id == self.truenas.host_id and host.source == "truenas_graphite"
            for host in self.hosts
        ):
            raise ValueError("truenas.host_id must identify a TrueNAS Graphite host")
        return self


def _env(name: str) -> str | None:
    """Prefer Command Deck settings while accepting existing local HCC settings."""
    return os.getenv(f"COMMAND_DECK_{name}") or os.getenv(f"HCC_{name}")


def load_config(path: Path | None = None) -> AppConfig:
    """Load public settings from YAML; credentials come only from the environment."""
    config_path = path or Path(_env("CONFIG_FILE") or "config.yaml")
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if isinstance(raw, dict) and isinstance(raw.get("prometheus"), dict):
        if "bearer_token" in raw["prometheus"]:
            raise ValueError("Prometheus credentials must be supplied through the environment")
    if isinstance(raw, dict) and isinstance(raw.get("healthchecks"), dict):
        if "api_key" in raw["healthchecks"]:
            raise ValueError("Healthchecks credentials must be supplied through the environment")
    if isinstance(raw, dict) and isinstance(raw.get("uptime_kuma"), dict):
        if "api_key" in raw["uptime_kuma"]:
            raise ValueError("Uptime Kuma credentials must be supplied through the environment")
    if isinstance(raw, dict) and isinstance(raw.get("truenas"), dict):
        if "api_key" in raw["truenas"]:
            raise ValueError("TrueNAS credentials must be supplied through the environment")
    config = AppConfig.model_validate(raw)
    url = _env("PROMETHEUS_URL")
    token = _env("PROMETHEUS_BEARER_TOKEN")
    if url or token:
        prometheus = PrometheusConfig(
            url=url or config.prometheus.url,
            timeout_seconds=config.prometheus.timeout_seconds,
            cache_seconds=config.prometheus.cache_seconds,
            max_sample_age_seconds=config.prometheus.max_sample_age_seconds,
            bearer_token=SecretStr(token) if token else None,
        )
        config.prometheus = prometheus
    healthchecks_url = _env("HEALTHCHECKS_URL")
    healthchecks_key = _env("HEALTHCHECKS_API_KEY")
    if healthchecks_url or healthchecks_key:
        config.healthchecks = HealthchecksConfig(
            url=healthchecks_url or config.healthchecks.url,
            backup_tag=config.healthchecks.backup_tag,
            timeout_seconds=config.healthchecks.timeout_seconds,
            cache_seconds=config.healthchecks.cache_seconds,
            api_key=SecretStr(healthchecks_key) if healthchecks_key else None,
        )
    kuma_url = _env("UPTIME_KUMA_URL")
    kuma_key = _env("UPTIME_KUMA_API_KEY")
    if kuma_url or kuma_key:
        config.uptime_kuma = UptimeKumaConfig(
            url=kuma_url or config.uptime_kuma.url,
            enabled=True,
            timeout_seconds=config.uptime_kuma.timeout_seconds,
            cache_seconds=config.uptime_kuma.cache_seconds,
            api_key=SecretStr(kuma_key) if kuma_key else None,
        )
    truenas_url = _env("TRUENAS_URL")
    truenas_username = _env("TRUENAS_USERNAME")
    truenas_key = _env("TRUENAS_API_KEY")
    if truenas_url or truenas_username or truenas_key:
        config.truenas = TrueNASConfig(
            host_id=config.truenas.host_id,
            url=truenas_url or config.truenas.url,
            username=truenas_username or config.truenas.username,
            api_key=SecretStr(truenas_key) if truenas_key else None,
            timeout_seconds=config.truenas.timeout_seconds,
            cache_seconds=config.truenas.cache_seconds,
        )
    return config
