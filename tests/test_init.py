"""Tests for the CLI entry point."""

from unittest.mock import MagicMock, patch

from hypercore_power_manager import main


def test_missing_config_exits(capsys):
    """main() exits with code 1 when config file is missing."""
    with patch("sys.argv", ["hypercore-power-manager", "--config", "nonexistent.yaml"]):
        try:
            main()
            assert False, "main() should have raised SystemExit"
        except SystemExit as e:
            assert e.code == 1


def test_help_flag(capsys):
    """main() prints help and exits with code 0 for --help."""
    with patch("sys.argv", ["hypercore-power-manager", "--help"]):
        try:
            main()
            assert False, "main() should have raised SystemExit"
        except SystemExit as e:
            assert e.code == 0

    captured = capsys.readouterr()
    assert "Monitor UPS via NUT" in captured.out
    assert "--config" in captured.out


def test_main_launches_manager(tmp_path):
    """main() loads config and launches PowerManager.run()."""
    # Create a minimal valid config file
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
nut:
  host: "10.0.0.1"

clusters: []
"""
    )

    with patch("sys.argv", ["hypercore-power-manager", "--config", str(config_file)]):
        with patch("hypercore_power_manager.monitor.PowerManager") as mock_pm_class:
            mock_manager = MagicMock()
            mock_pm_class.return_value = mock_manager

            main()

            # PowerManager should have been created and run() called
            mock_pm_class.assert_called_once()
            mock_manager.run.assert_called_once()
