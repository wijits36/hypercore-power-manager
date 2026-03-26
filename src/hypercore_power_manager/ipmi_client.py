"""IPMI client for host power management via pyghmi."""

from pyghmi.ipmi import command as ipmi_command

from .config import NodeConfig


class IPMIClient:
    """Controls a single physical server's power state via IPMI.

    Uses pyghmi's persistent session model — one connection per BMC,
    created lazily on first use and reused for all subsequent calls.
    pyghmi's built-in keepalive prevents BMC-side session timeout.

    If a session breaks (e.g. network disruption), the next call
    detects it and transparently creates a fresh connection.
    """

    def __init__(self, config: NodeConfig) -> None:
        self._config = config
        self.hostname = config.ipmi_host
        self._conn: ipmi_command.Command | None = None

    def _get_conn(self) -> ipmi_command.Command:
        """Return the persistent connection, creating it if needed.

        Checks for a broken session and recreates the connection
        transparently. pyghmi marks sessions as broken when it
        detects unrecoverable communication failures.
        """
        if self._conn is None or self._conn.ipmi_session.broken:  # type: ignore[union-attr]
            self._conn = ipmi_command.Command(
                bmc=self._config.ipmi_host,
                userid=self._config.ipmi_username,
                password=self._config.ipmi_password,
            )
        return self._conn

    def power_status(self) -> str:
        """Query the current power state of the host.

        Returns:
            Power state string, e.g. 'on' or 'off'.
        """
        conn = self._get_conn()
        result = conn.get_power()
        return result["powerstate"]

    def power_off(self) -> None:
        """Power off the host via IPMI."""
        conn = self._get_conn()
        conn.set_power("off")

    def power_on(self) -> None:
        """Power on the host via IPMI."""
        conn = self._get_conn()
        conn.set_power("on")
