"""Tests for configuration loading."""

import pytest

from hypercore_power_manager.config import NutConfig, load_config


def test_nut_config_defaults():
    """NutConfig fills in defaults when only host is provided."""
    config = NutConfig(host="10.0.0.1")
    assert config.port == 3493
    assert config.ups_name == "ups"
    assert config.poll_interval_seconds == 5
    assert config.username is None
    assert config.password is None


def test_load_config_full(tmp_path):
    """load_config parses a complete YAML file into typed Config object."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
nut:
  host: "10.0.0.1"
  ups_name: "testups"

clusters:
  - host: "cluster1.example"
    username: "admin"
    password: "testpass"
    nodes:
      - ipmi_host: "ipmi1.example"
        ipmi_username: "root"
        ipmi_password: "testpass"

thresholds:
  battery_percent: 40
  runtime_seconds: 500
""")

    config = load_config(str(config_file))

    assert config.nut.host == "10.0.0.1"
    assert config.nut.ups_name == "testups"
    assert config.nut.port == 3493
    assert len(config.clusters) == 1
    assert config.clusters[0].host == "cluster1.example"
    assert len(config.clusters[0].nodes) == 1
    assert config.clusters[0].nodes[0].ipmi_host == "ipmi1.example"
    assert config.thresholds.battery_percent == 40
    assert config.thresholds.runtime_seconds == 500
    assert config.thresholds.host_shutdown_delay == 300


def test_load_config_minimal(tmp_path):
    """load_config works with only required fields, filling in all defaults."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
nut:
  host: "10.0.0.1"

clusters: []
""")

    config = load_config(str(config_file))

    assert config.nut.host == "10.0.0.1"
    assert config.nut.port == 3493
    assert config.clusters == []
    assert config.thresholds.battery_percent == 50


def test_load_config_invalid_cluster(tmp_path):
    """load_config raises ValueError for a cluster missing required fields."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
nut:
  host: "10.0.0.1"

clusters:
  - host: "cluster1.example"
    nodes: []
""")

    with pytest.raises(ValueError, match="Invalid cluster config"):
        load_config(str(config_file))


def test_load_config_multi_cluster(tmp_path):
    """load_config handles multiple clusters with different node counts."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
nut:
  host: "10.0.0.1"

clusters:
  - host: "cluster1.example"
    username: "admin"
    password: "testpass"
    nodes:
      - ipmi_host: "ipmi1.example"
        ipmi_username: "root"
        ipmi_password: "testpass"

  - host: "cluster2.example"
    username: "admin2"
    password: "testpass2"
    vm_shutdown_timeout: 120
    nodes:
      - ipmi_host: "ipmi2a.example"
        ipmi_username: "root"
        ipmi_password: "testpass"
      - ipmi_host: "ipmi2b.example"
        ipmi_username: "root"
        ipmi_password: "testpass"
""")

    config = load_config(str(config_file))

    assert len(config.clusters) == 2
    # First cluster: one node, default timeout
    assert config.clusters[0].host == "cluster1.example"
    assert len(config.clusters[0].nodes) == 1
    assert config.clusters[0].vm_shutdown_timeout == 300
    # Second cluster: two nodes, custom timeout
    assert config.clusters[1].host == "cluster2.example"
    assert len(config.clusters[1].nodes) == 2
    assert config.clusters[1].vm_shutdown_timeout == 120
    assert config.clusters[1].nodes[1].ipmi_host == "ipmi2b.example"


def test_load_config_invalid_yaml(tmp_path):
    """load_config propagates yaml.YAMLError for malformed YAML."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
nut:
  host: "10.0.0.1"
  port: [invalid yaml
    this is broken
""")

    import yaml

    with pytest.raises(yaml.YAMLError):
        load_config(str(config_file))
