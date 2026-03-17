"""IPMI client for host power management via pyghmi."""

from pyghmi.ipmi import command as ipmi_command

from .config import NodeConfig


class IPMIClient:
    """Controls a single physical server's power state via IPMI.

    Each method creates a fresh pyghmi connection rather than reusing
    a persistent session. IPMI calls are infrequent (only during power
    events), so the reconnection overhead is negligible and avoids
    managing session lifecycle (timeouts, keepalives).
    """

    def __init__(self, config: NodeConfig) -> None:
        self._config = config
        self.hostname = config.ipmi_host

    def power_status(self) -> str:
        """Query the current power state of the host.

        Returns:
            Power state string, e.g. 'on' or 'off'.
        """
        conn = ipmi_command.Command(
            bmc=self._config.ipmi_host,
            userid=self._config.ipmi_username,
            password=self._config.ipmi_password,
        )
        result = conn.get_power()
        return result["powerstate"]

    def power_off(self) -> None:
        """Power off the host via IPMI."""
        conn = ipmi_command.Command(
            bmc=self._config.ipmi_host,
            userid=self._config.ipmi_username,
            password=self._config.ipmi_password,
        )
        conn.set_power("off")

    def power_on(self) -> None:
        """Power on the host via IPMI."""
        conn = ipmi_command.Command(
            bmc=self._config.ipmi_host,
            userid=self._config.ipmi_username,
            password=self._config.ipmi_password,
        )
        conn.set_power("on")
