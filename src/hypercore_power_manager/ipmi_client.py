"""IPMI client for host power management via pyghmi."""

from pyghmi.ipmi import command as ipmi_command

from .config import NodeConfig


class IPMIClient:
    """Controls a single physical server's power state via IPMI.

    pyghmi caches IPMI sessions internally via Session.__new__. If a
    session is not explicitly closed after use, the next Command() for
    the same BMC silently returns the stale cached object instead of
    creating a new connection. This causes indefinite hangs when the
    BMC has been power-cycled between calls.

    Every operation uses _run_command(), which creates a connection,
    executes one operation, and calls logout() in a finally block to
    evict the session from pyghmi's cache.
    """

    def __init__(self, config: NodeConfig) -> None:
        self._config = config
        self.hostname = config.ipmi_host

    def _run_command(self, operation):
        """Execute an IPMI operation with proper session cleanup.

        Creates a fresh pyghmi connection, runs the provided callable,
        and guarantees session logout in a finally block so the cached
        session is evicted from pyghmi's internal registry.
        """
        conn = ipmi_command.Command(
            bmc=self._config.ipmi_host,
            userid=self._config.ipmi_username,
            password=self._config.ipmi_password,
        )
        try:
            return operation(conn)
        finally:
            conn.ipmi_session.logout()  # type: ignore[attr-defined]

    def power_status(self) -> str:
        """Query the current power state of the host.

        Returns:
            Power state string, e.g. 'on' or 'off'.
        """
        result = self._run_command(lambda conn: conn.get_power())
        return result["powerstate"]

    def power_off(self) -> None:
        """Power off the host via IPMI."""
        self._run_command(lambda conn: conn.set_power("off"))

    def power_on(self) -> None:
        """Power on the host via IPMI."""
        self._run_command(lambda conn: conn.set_power("on"))
