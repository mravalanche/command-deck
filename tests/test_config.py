"""Configuration boundaries: ordered public settings and environment-only secrets."""

import pytest

from command_deck.config import load_config


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
