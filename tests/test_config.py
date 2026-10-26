"""Configuration boundaries: ordered public settings and environment-only secrets."""

import pytest

from command_deck.config import AppConfig, load_config


@pytest.mark.parametrize("suffix", ["yml", "yaml"])
def test_config_file_accepts_either_yaml_suffix(tmp_path, monkeypatch, suffix):
    path = tmp_path / f"config.{suffix}"
    path.write_text(
        "prometheus:\n  url: http://prometheus:9090\nhosts:\n"
        "  - id: server\n    name: Server\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COMMAND_DECK_CONFIG_FILE", str(path))
    assert load_config().prometheus.url == "http://prometheus:9090"


def test_environment_url_is_validated_and_yaml_cannot_hold_token(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "prometheus:\n  url: http://prometheus:9090\nhosts:\n"
        "  - id: server\n    name: Server\n    instance: target:9100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COMMAND_DECK_PROMETHEUS_URL", "http://replacement:9090/")
    assert load_config(path).prometheus.url == "http://replacement:9090"

    monkeypatch.setenv("COMMAND_DECK_PROMETHEUS_URL", "not-a-url")
    with pytest.raises(ValueError, match="absolute HTTP"):
        load_config(path)

    monkeypatch.delenv("COMMAND_DECK_PROMETHEUS_URL")
    path.write_text(
        path.read_text(encoding="utf-8").replace("  url:", "  bearer_token: forbidden\n  url:"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="environment"):
        load_config(path)


def test_legacy_environment_values_remain_supported_with_new_name_precedence(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "prometheus:\n  url: http://prometheus:9090\nhosts:\n"
        "  - id: server\n    name: Server\n    instance: target:9100\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("COMMAND_DECK_PROMETHEUS_URL", raising=False)
    monkeypatch.setenv("HCC_PROMETHEUS_URL", "http://legacy:9090")
    assert load_config(path).prometheus.url == "http://legacy:9090"

    monkeypatch.setenv("COMMAND_DECK_PROMETHEUS_URL", "http://new:9090")
    assert load_config(path).prometheus.url == "http://new:9090"


def test_storage_icons_are_optional_but_cannot_be_blank():
    base = {"prometheus": {"url": "http://prometheus:9090"}}
    filesystem = {"mountpoint": "/", "label": "System"}
    host = {"id": "server", "name": "Server", "filesystems": [filesystem]}
    node = AppConfig.model_validate({**base, "hosts": [host]})
    assert node.hosts[0].filesystems[0].icon is None

    with pytest.raises(ValueError, match="Filesystem icon must not be empty"):
        AppConfig.model_validate({**base, "hosts": [
            {**host, "filesystems": [{**filesystem, "icon": " "}]}
        ]})

    with pytest.raises(ValueError, match="Pool names and icons must not be empty"):
        AppConfig.model_validate({**base, "hosts": [
            {"id": "nas", "name": "NAS", "source": "truenas_graphite",
             "pool_icons": {"tank": " "}}
        ]})


@pytest.mark.parametrize(
    "section, env_name",
    [
        ("prometheus", "PROMETHEUS_URL"),
        ("healthchecks", "HEALTHCHECKS_URL"),
        ("uptime_kuma", "UPTIME_KUMA_URL"),
        ("truenas", "TRUENAS_URL"),
        ("proxmox", "PROXMOX_URL"),
    ],
)
def test_invalid_source_urls_do_not_echo_credentials(tmp_path, monkeypatch, section, env_name):
    marker = "EXAMPLE_SECRET"
    invalid_url = f"https://user:{marker}@example.invalid"
    public = {
        "prometheus": {"url": "http://prometheus:9090"},
        "hosts": [{"id": "server", "name": "Server"}],
    }
    public[section] = {"url": invalid_url}
    with pytest.raises(ValueError) as invalid_file:
        AppConfig.model_validate(public)
    assert marker not in str(invalid_file.value)

    path = tmp_path / "config.yaml"
    path.write_text(
        "prometheus:\n  url: http://prometheus:9090\nhosts:\n"
        "  - id: server\n    name: Server\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(f"COMMAND_DECK_{env_name}", invalid_url)
    with pytest.raises(ValueError) as invalid_env:
        load_config(path)
    assert marker not in str(invalid_env.value)


def test_proxmox_env_credentials_and_public_settings(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("hosts:\n  - id: pve\n    name: PVE\n    source: proxmox\n"
                    "    proxmox_node: pve1\n")
    monkeypatch.setenv("COMMAND_DECK_PROXMOX_URL", "https://pve.example.invalid:8006/")
    monkeypatch.setenv("COMMAND_DECK_PROXMOX_TOKEN_ID", "reader@pve!dashboard")
    monkeypatch.setenv("COMMAND_DECK_PROXMOX_TOKEN_SECRET", "test-secret")
    config = load_config(path)
    assert config.proxmox.enabled
    assert config.proxmox.url == "https://pve.example.invalid:8006"
    assert config.proxmox.token_secret.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(config)
    assert "token_secret" not in config.model_dump()["proxmox"]
    path.write_text(path.read_text() + "proxmox:\n  token_secret: forbidden\n")
    with pytest.raises(ValueError, match="environment"):
        load_config(path)


@pytest.mark.parametrize("url", [
    "http://pve:8006", "https://user:EXAMPLE_SECRET@pve", "https://pve/api2/json",
    "https://pve?token=EXAMPLE_SECRET", "https://pve/#fragment",
])
def test_proxmox_requires_https_origin_without_credentials(url):
    with pytest.raises(ValueError) as error:
        AppConfig.model_validate({"proxmox": {"url": url},
                                  "hosts": [{"id": "pve", "name": "PVE"}]})
    assert "EXAMPLE_SECRET" not in str(error.value)


@pytest.mark.parametrize("extra", [
    {"proxmox_node": "../other"}, {"instance": "pve:9100"},
    {"filesystems": [{"mountpoint": "/", "label": "Root"}]},
    {"pools": ["local"]}, {"graphite_prefix": "pve"},
])
def test_proxmox_rejects_wrong_source_options(extra):
    with pytest.raises(ValueError):
        AppConfig.model_validate({"hosts": [
            {"id": "pve", "name": "PVE", "source": "proxmox", **extra}
        ]})
