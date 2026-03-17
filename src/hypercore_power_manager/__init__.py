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

    # Phase 1: Basic console logging so config errors are visible
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

    # Phase 2: Reconfigure logging with settings from config
    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)

    # File handler — persistent log for troubleshooting
    file_handler = logging.FileHandler(config.logging.file)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    # Reconfigure root logger with both console and file output
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)

    logger.info("Configuration loaded from %s", args.config)
    logger.info("Logging to %s at level %s", config.logging.file, config.logging.level)

    # Launch the power manager
    from .monitor import PowerManager

    manager = PowerManager(config)
    try:
        manager.run()
    except KeyboardInterrupt:
        logger.info("Shutting down (received interrupt)")
