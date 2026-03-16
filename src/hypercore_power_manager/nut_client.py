"""NUT client module for polling UPS status."""

from dataclasses import dataclass

from PyNUTClient.PyNUT import PyNUTClient as PyNUTConnection

from .config import NutConfig


@dataclass
class UPSStatus:
    """Snapshot of current UPS state from a single poll."""

    status: str
    battery_charge: float
    battery_runtime: float
    input_voltage: float
    output_voltage: float
    battery_voltage: float
    ups_load: float
    on_battery: bool
    on_line: bool


class NUTClient:
    """Connects to a NUT server and polls UPS status."""

    def __init__(self, config: NutConfig) -> None:
        self._config = config
        self._client: PyNUTConnection | None = None

    def connect(self) -> None:
        """Establish connection to the NUT server."""
        self._client = PyNUTConnection(
            host=self._config.host,
            port=self._config.port,
            login=self._config.username,
            password=self._config.password,
            timeout=5,
        )

    def poll(self) -> UPSStatus:
        """Poll the UPS and return its current status."""
        if self._client is None:
            raise RuntimeError("Not connected. Call connect() first.")

        ups_vars = self._client.GetUPSVars(self._config.ups_name)
        ups_vars = {
            k.decode() if isinstance(k, bytes) else k: v.decode()
            if isinstance(v, bytes)
            else v
            for k, v in ups_vars.items()
        }

        status = ups_vars.get("ups.status", "")

        return UPSStatus(
            status=status,
            battery_charge=float(ups_vars.get("battery.charge", 0)),
            battery_runtime=float(ups_vars.get("battery.runtime", 0)),
            input_voltage=float(ups_vars.get("input.voltage", 0)),
            output_voltage=float(ups_vars.get("output.voltage", 0)),
            battery_voltage=float(ups_vars.get("battery.voltage", 0)),
            ups_load=float(ups_vars.get("ups.load", 0)),
            on_battery="OB" in status,
            on_line="OL" in status,
        )
