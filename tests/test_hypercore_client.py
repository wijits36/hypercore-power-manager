"""Tests for HyperCore REST API client."""

from unittest.mock import MagicMock, patch

import pytest

from hypercore_power_manager.config import ClusterConfig, NodeConfig
from hypercore_power_manager.hypercore_client import HyperCoreClient


@pytest.fixture
def cluster_config():
    """Build a minimal ClusterConfig for testing."""
    return ClusterConfig(
        host="cluster1.example",
        username="admin",
        password="testpass",
        nodes=[
            NodeConfig(
                ipmi_host="ipmi1.example",
                ipmi_username="root",
                ipmi_password="testpass",
            ),
        ],
    )


def test_login(cluster_config):
    """login() creates a session and posts credentials."""
    with patch(
        "hypercore_power_manager.hypercore_client.requests.Session"
    ) as mock_session_class:
        with patch("hypercore_power_manager.hypercore_client.urllib3"):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            client = HyperCoreClient(cluster_config)
            client.login()

            mock_session.post.assert_called_once_with(
                "https://cluster1.example/rest/v1/login",
                json={
                    "username": "admin",
                    "password": "testpass",
                    "useOIDC": False,
                },
            )


def test_get_vms(cluster_config):
    """get_vms() parses API response into VMInfo objects."""
    with patch(
        "hypercore_power_manager.hypercore_client.requests.Session"
    ) as mock_session_class:
        with patch("hypercore_power_manager.hypercore_client.urllib3"):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            # Simulate the JSON response from GET /rest/v1/VirDomain
            mock_session.get.return_value.json.return_value = [
                {
                    "uuid": "abc-123",
                    "name": "web-server",
                    "state": "RUNNING",
                    "desiredDisposition": "RUNNING",
                },
                {
                    "uuid": "def-456",
                    "name": "db-server",
                    "state": "SHUTOFF",
                    "desiredDisposition": "SHUTOFF",
                },
            ]

            client = HyperCoreClient(cluster_config)
            client.login()
            vms = client.get_vms()

            assert len(vms) == 2
            assert vms[0].uuid == "abc-123"
            assert vms[0].name == "web-server"
            assert vms[0].state == "RUNNING"
            assert vms[1].state == "SHUTOFF"
            # Verify the camelCase API field maps to our snake_case attribute
            assert vms[1].desired_disposition == "SHUTOFF"


def test_shutdown_vm_sends_array(cluster_config):
    """shutdown_vm() sends the action as an array, not a single object."""
    with patch(
        "hypercore_power_manager.hypercore_client.requests.Session"
    ) as mock_session_class:
        with patch("hypercore_power_manager.hypercore_client.urllib3"):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            client = HyperCoreClient(cluster_config)
            client.login()
            client.shutdown_vm("abc-123")

            # The critical detail: body must be an array [{}], not a bare {}
            mock_session.post.assert_any_call(
                "https://cluster1.example/rest/v1/VirDomain/action",
                json=[
                    {
                        "virDomainUUID": "abc-123",
                        "actionType": "SHUTDOWN",
                        "cause": "hypercore-power-manager: graceful shutdown",
                    }
                ],
            )


def test_stop_vm(cluster_config):
    """stop_vm() sends STOP action for force-killing a VM."""
    with patch(
        "hypercore_power_manager.hypercore_client.requests.Session"
    ) as mock_session_class:
        with patch("hypercore_power_manager.hypercore_client.urllib3"):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            client = HyperCoreClient(cluster_config)
            client.login()
            client.stop_vm("abc-123")

            mock_session.post.assert_any_call(
                "https://cluster1.example/rest/v1/VirDomain/action",
                json=[
                    {
                        "virDomainUUID": "abc-123",
                        "actionType": "STOP",
                        "cause": "hypercore-power-manager: forced stop",
                    }
                ],
            )


def test_start_vm(cluster_config):
    """start_vm() sends START action for power restore."""
    with patch(
        "hypercore_power_manager.hypercore_client.requests.Session"
    ) as mock_session_class:
        with patch("hypercore_power_manager.hypercore_client.urllib3"):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            client = HyperCoreClient(cluster_config)
            client.login()
            client.start_vm("abc-123")

            mock_session.post.assert_any_call(
                "https://cluster1.example/rest/v1/VirDomain/action",
                json=[
                    {
                        "virDomainUUID": "abc-123",
                        "actionType": "START",
                        "cause": "hypercore-power-manager: power restore",
                    }
                ],
            )


def test_get_vms_not_logged_in(cluster_config):
    """get_vms() raises RuntimeError if called before login()."""
    client = HyperCoreClient(cluster_config)

    with pytest.raises(RuntimeError, match="Not logged in"):
        client.get_vms()


def test_logout_cleans_session(cluster_config):
    """logout() posts to logout endpoint and clears the session."""
    with patch(
        "hypercore_power_manager.hypercore_client.requests.Session"
    ) as mock_session_class:
        with patch("hypercore_power_manager.hypercore_client.urllib3"):
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            client = HyperCoreClient(cluster_config)
            client.login()
            client.logout()

            mock_session.post.assert_any_call(
                "https://cluster1.example/rest/v1/logout",
            )
            mock_session.close.assert_called_once()

            # Session should be None after logout
            with pytest.raises(RuntimeError, match="Not logged in"):
                client.get_vms()
