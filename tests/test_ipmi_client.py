"""Tests for IPMI client host power management."""

from unittest.mock import MagicMock, patch

from hypercore_power_manager.config import NodeConfig
from hypercore_power_manager.ipmi_client import IPMIClient


def test_power_status():
    """power_status() queries pyghmi and returns the power state string."""
    with patch(
        "hypercore_power_manager.ipmi_client.ipmi_command.Command"
    ) as mock_cmd_class:
        mock_conn = MagicMock()
        mock_cmd_class.return_value = mock_conn
        mock_conn.get_power.return_value = {"powerstate": "on"}

        config = NodeConfig(
            ipmi_host="ipmi1.example",
            ipmi_username="root",
            ipmi_password="testpass",
        )
        client = IPMIClient(config)
        result = client.power_status()

        assert result == "on"
        # Verify correct credentials were passed to pyghmi
        mock_cmd_class.assert_called_once_with(
            bmc="ipmi1.example",
            userid="root",
            password="testpass",
        )


def test_power_off():
    """power_off() sends the 'off' command via pyghmi."""
    with patch(
        "hypercore_power_manager.ipmi_client.ipmi_command.Command"
    ) as mock_cmd_class:
        mock_conn = MagicMock()
        mock_cmd_class.return_value = mock_conn

        config = NodeConfig(
            ipmi_host="ipmi1.example",
            ipmi_username="root",
            ipmi_password="testpass",
        )
        client = IPMIClient(config)
        client.power_off()

        mock_conn.set_power.assert_called_once_with("off")


def test_power_on():
    """power_on() sends the 'on' command via pyghmi."""
    with patch(
        "hypercore_power_manager.ipmi_client.ipmi_command.Command"
    ) as mock_cmd_class:
        mock_conn = MagicMock()
        mock_cmd_class.return_value = mock_conn

        config = NodeConfig(
            ipmi_host="ipmi1.example",
            ipmi_username="root",
            ipmi_password="testpass",
        )
        client = IPMIClient(config)
        client.power_on()

        mock_conn.set_power.assert_called_once_with("on")


def test_hostname_attribute():
    """IPMIClient exposes hostname for logging purposes."""
    config = NodeConfig(
        ipmi_host="ipmi1.example",
        ipmi_username="root",
        ipmi_password="testpass",
    )
    client = IPMIClient(config)

    assert client.hostname == "ipmi1.example"


def test_each_call_creates_new_connection():
    """Each method call creates a fresh pyghmi connection."""
    with patch(
        "hypercore_power_manager.ipmi_client.ipmi_command.Command"
    ) as mock_cmd_class:
        mock_conn = MagicMock()
        mock_cmd_class.return_value = mock_conn
        mock_conn.get_power.return_value = {"powerstate": "on"}

        config = NodeConfig(
            ipmi_host="ipmi1.example",
            ipmi_username="root",
            ipmi_password="testpass",
        )
        client = IPMIClient(config)

        client.power_status()
        client.power_on()
        client.power_off()

        # Three method calls should create three separate connections
        assert mock_cmd_class.call_count == 3
