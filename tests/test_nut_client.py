"""Tests for NUT client UPS status parsing."""

from unittest.mock import MagicMock, patch

from hypercore_power_manager.config import NutConfig
from hypercore_power_manager.nut_client import NUTClient


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_poll_parses_ups_vars(mock_connection_class):
    """poll() converts raw NUT variables into a typed UPSStatus."""
    # Create a mock instance that the constructor returns
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    # Simulate what pynutclient actually returns: bytes keys and values
    mock_conn.GetUPSVars.return_value = {
        b"ups.status": b"OL",
        b"battery.charge": b"100",
        b"battery.runtime": b"1560",
        b"input.voltage": b"122.0",
        b"output.voltage": b"120.0",
        b"battery.voltage": b"27.4",
        b"ups.load": b"27",
    }

    config = NutConfig(host="10.0.0.1", ups_name="testups")
    client = NUTClient(config)
    client.connect()
    status = client.poll()

    assert status.status == "OL"
    assert status.battery_charge == 100.0
    assert status.battery_runtime == 1560.0
    assert status.input_voltage == 122.0
    assert status.output_voltage == 120.0
    assert status.battery_voltage == 27.4
    assert status.ups_load == 27.0
    assert status.on_line is True
    assert status.on_battery is False


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_poll_on_battery_status(mock_connection_class):
    """poll() correctly parses OB (on battery) status flag."""
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    mock_conn.GetUPSVars.return_value = {
        b"ups.status": b"OB",
        b"battery.charge": b"85",
        b"battery.runtime": b"1200",
        b"input.voltage": b"0",
        b"output.voltage": b"120.0",
        b"battery.voltage": b"26.8",
        b"ups.load": b"27",
    }

    config = NutConfig(host="10.0.0.1", ups_name="testups")
    client = NUTClient(config)
    client.connect()
    status = client.poll()

    assert status.on_battery is True
    assert status.on_line is False
    assert status.status == "OB"


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_poll_combined_status_flags(mock_connection_class):
    """poll() handles combined NUT status flags like 'OL CHRG'."""
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    # NUT can report multiple flags separated by spaces.
    # "OL CHRG" means on line power AND charging battery.
    mock_conn.GetUPSVars.return_value = {
        b"ups.status": b"OL CHRG",
        b"battery.charge": b"73",
        b"battery.runtime": b"900",
        b"input.voltage": b"121.5",
        b"output.voltage": b"120.0",
        b"battery.voltage": b"26.2",
        b"ups.load": b"27",
    }

    config = NutConfig(host="10.0.0.1", ups_name="testups")
    client = NUTClient(config)
    client.connect()
    status = client.poll()

    # "OL" is present in "OL CHRG", so on_line should be True
    assert status.on_line is True
    assert status.on_battery is False
    assert status.status == "OL CHRG"


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_poll_missing_variable_defaults_to_zero(mock_connection_class):
    """poll() defaults to 0 when a UPS variable is missing."""
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    # Only status is present — all numeric fields should default to 0
    mock_conn.GetUPSVars.return_value = {
        b"ups.status": b"OL",
    }

    config = NutConfig(host="10.0.0.1", ups_name="testups")
    client = NUTClient(config)
    client.connect()
    status = client.poll()

    assert status.battery_charge == 0.0
    assert status.battery_runtime == 0.0
    assert status.input_voltage == 0.0
    assert status.ups_load == 0.0


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_connect_passes_config(mock_connection_class):
    """connect() passes config values to PyNUTConnection constructor."""
    config = NutConfig(
        host="10.0.0.1",
        port=3493,
        ups_name="testups",
        username="testuser",
        password="testpass",
    )
    client = NUTClient(config)
    client.connect()

    mock_connection_class.assert_called_once_with(
        host="10.0.0.1",
        port=3493,
        login="testuser",
        password="testpass",
        timeout=5,
    )


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_poll_handles_string_values(mock_connection_class):
    """poll() handles str values in case pynutclient changes from bytes."""
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    # Simulate a future pynutclient version returning str instead of bytes
    mock_conn.GetUPSVars.return_value = {
        "ups.status": "OB LB",
        "battery.charge": "22",
        "battery.runtime": "180",
        "input.voltage": "0",
        "output.voltage": "118.0",
        "battery.voltage": "24.1",
        "ups.load": "27",
    }

    config = NutConfig(host="10.0.0.1", ups_name="testups")
    client = NUTClient(config)
    client.connect()
    status = client.poll()

    assert status.status == "OB LB"
    assert status.battery_charge == 22.0
    assert status.on_battery is True
    assert status.on_line is False


@patch("hypercore_power_manager.nut_client.PyNUTConnection")
def test_poll_creates_fresh_connection(mock_connection_class):
    """poll() creates a new connection each call, preventing stale sockets."""
    mock_conn = MagicMock()
    mock_connection_class.return_value = mock_conn

    mock_conn.GetUPSVars.return_value = {
        b"ups.status": b"OL",
        b"battery.charge": b"100",
        b"battery.runtime": b"1560",
        b"input.voltage": b"122.0",
        b"output.voltage": b"120.0",
        b"battery.voltage": b"27.4",
        b"ups.load": b"27",
    }

    config = NutConfig(host="10.0.0.1", ups_name="testups")
    client = NUTClient(config)

    # Call poll() twice without connect() — both should work
    client.poll()
    client.poll()

    # PyNUTConnection constructor should have been called twice,
    # once per poll() — proving each poll gets a fresh connection
    assert mock_connection_class.call_count == 2
