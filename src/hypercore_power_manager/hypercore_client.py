"""HyperCore REST API client for VM power management."""

import logging
from dataclasses import dataclass

import requests
import urllib3

from .config import ClusterConfig

logger = logging.getLogger(__name__)


@dataclass
class VMInfo:
    """Represents a virtual machine's identity and power state."""

    uuid: str
    name: str
    state: str
    desired_disposition: str


class HyperCoreClient:
    """Manages a session with a single HyperCore cluster."""

    def __init__(self, config: ClusterConfig) -> None:
        self._config = config
        self._session: requests.Session | None = None
        self._base_url = f"https://{config.host}/rest/v1"

    def login(self) -> None:
        """Authenticate and establish a session with HyperCore."""
        self._session = requests.Session()
        self._session.verify = self._config.verify_ssl

        if not self._config.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        response = self._session.post(
            f"{self._base_url}/login",
            json={
                "username": self._config.username,
                "password": self._config.password,
                "useOIDC": False,
            },
        )
        response.raise_for_status()

    def logout(self) -> None:
        """End the HyperCore session."""
        if self._session is None:
            return

        try:
            self._session.post(f"{self._base_url}/logout")
        finally:
            self._session.close()
            self._session = None

    def get_vms(self) -> list[VMInfo]:
        """Fetch all VMs and their power states from the cluster."""
        if self._session is None:
            raise RuntimeError("Not logged in. Call login() first.")

        response = self._session.get(f"{self._base_url}/VirDomain")
        response.raise_for_status()

        vms = []
        for vm_data in response.json():
            vms.append(
                VMInfo(
                    uuid=vm_data["uuid"],
                    name=vm_data["name"],
                    state=vm_data["state"],
                    desired_disposition=vm_data["desiredDisposition"],
                )
            )
        return vms

    def _vm_action(self, vm_uuid: str, action: str, cause: str = "") -> None:
        """Send a power action to a specific VM."""
        if self._session is None:
            raise RuntimeError("Not logged in. Call login() first.")

        # HyperCore REST API expects an array, not a single object.
        # Sending a bare object returns 400.
        response = self._session.post(
            f"{self._base_url}/VirDomain/action",
            json=[
                {
                    "virDomainUUID": vm_uuid,
                    "actionType": action,
                    "cause": cause,
                }
            ],
        )
        if not response.ok:
            logger.error(
                "VM action %s failed on %s — HTTP %d: %s",
                action,
                vm_uuid,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

    def shutdown_vm(self, vm_uuid: str) -> None:
        """Gracefully shut down a VM via ACPI signal."""
        self._vm_action(
            vm_uuid, "SHUTDOWN", "hypercore-power-manager: graceful shutdown"
        )

    def stop_vm(self, vm_uuid: str) -> None:
        """Force stop a VM immediately."""
        self._vm_action(vm_uuid, "STOP", "hypercore-power-manager: forced stop")

    def start_vm(self, vm_uuid: str) -> None:
        """Start a stopped VM."""
        self._vm_action(vm_uuid, "START", "hypercore-power-manager: power restore")
