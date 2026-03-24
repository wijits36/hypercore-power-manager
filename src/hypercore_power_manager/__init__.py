"""hypercore-power-manager: UPS-triggered graceful shutdown for HyperCore."""

import argparse
import logging

from .config import load_config


def main() -> None:
    """Entry point for the hypercore-power-manager CLI."""
    parser = argparse.ArgumentParser(
        description="Monitor UPS via NUT and manage HyperCore power lifecycle.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="path to the YAML config file (default: config.yaml)",
    )
    args = parser.parse_args()

    # Log to stderr — systemd captures this into the journal automatically
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("hypercore_power_manager")

    # Load configuration
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Configuration error: %s", e)
        raise SystemExit(1)

    logger.info("Configuration loaded from %s", args.config)

    # Launch the power manager
    from .monitor import PowerManager

    manager = PowerManager(config)
    try:
        manager.run()
    except KeyboardInterrupt:
        logger.info("Shutting down (received interrupt)")
