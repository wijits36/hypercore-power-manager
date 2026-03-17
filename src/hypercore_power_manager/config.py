"""Configuration loading and validation for hypercore-power-manager."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class NutConfig:
    """NUT server connection settings."""

    host: str
    port: int = 3493
    ups_name: str = "ups"
    username: str | None = None
    password: str | None = None
    poll_interval_seconds: int = 5


@dataclass
class NodeConfig:
    """IPMI settings for a single physical node in a cluster."""

    ipmi_host: str
    ipmi_username: str
    ipmi_password: str


@dataclass
class ClusterConfig:
    """Connection settings for a single HyperCore cluster."""

    host: str
    username: str
    password: str
    nodes: list[NodeConfig]
    vm_shutdown_timeout: int = 300
    verify_ssl: bool = False


@dataclass
class ThresholdsConfig:
    """Thresholds that control when shutdown stages trigger."""

    battery_percent: int = 50
    runtime_seconds: int = 600
    host_shutdown_delay: int = 300
    host_boot_timeout: int = 600


@dataclass
class LoggingConfig:
    """Logging settings."""

    file: str = "/var/log/hypercore-power-manager.log"
    level: str = "INFO"


@dataclass
class Config:
    """Top-level configuration container."""

    nut: NutConfig
    clusters: list[ClusterConfig]
    thresholds: ThresholdsConfig
    logging: LoggingConfig


def load_config(path: str) -> Config:
    """Load and validate configuration from a YAML file."""

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError(f"Config file is empty: {config_path}")

    # NUT configuration
    nut_raw = raw.get("nut", {})
    try:
        nut_config = NutConfig(**nut_raw)
    except TypeError as e:
        raise ValueError(f"Invalid nut config: {e}") from e

    # Cluster configuration
    clusters_config = []
    for cluster_raw in raw.get("clusters", []):
        try:
            # pop() removes "nodes" from the dict so it doesn't collide
            # with the nodes= keyword argument when we unpack **cluster_raw
            nodes = [NodeConfig(**n) for n in cluster_raw.pop("nodes", [])]
            cluster_config = ClusterConfig(**cluster_raw, nodes=nodes)
            clusters_config.append(cluster_config)
        except TypeError as e:
            raise ValueError(f"Invalid cluster config: {e}") from e

    # Thresholds configuration
    thresholds_raw = raw.get("thresholds", {})
    try:
        thresholds_config = ThresholdsConfig(**thresholds_raw)
    except TypeError as e:
        raise ValueError(f"Invalid thresholds config: {e}") from e

    # Logging configuration
    logging_raw = raw.get("logging", {})
    try:
        logging_config = LoggingConfig(**logging_raw)
    except TypeError as e:
        raise ValueError(f"Invalid logging config: {e}") from e

    return Config(
        nut=nut_config,
        clusters=clusters_config,
        thresholds=thresholds_config,
        logging=logging_config,
    )
