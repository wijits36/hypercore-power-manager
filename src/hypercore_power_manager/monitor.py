"""Main monitoring loop and power management state machine."""

import logging
import time
from enum import Enum, auto

from .config import Config
from .hypercore_client import HyperCoreClient
from .ipmi_client import IPMIClient
from .nut_client import NUTClient


class State(Enum):
    """Power management states."""

    MONITORING = auto()
    ON_BATTERY = auto()
    SHUTTING_DOWN_VMS = auto()
    WAITING_FOR_HOST_SHUTDOWN = auto()
    SHUTTING_DOWN_HOSTS = auto()
    WAITING_FOR_POWER = auto()
    POWERING_ON_HOSTS = auto()
    STARTING_VMS = auto()


logger = logging.getLogger(__name__)


class PowerManager:
    """Monitors UPS status and orchestrates graceful shutdown/recovery."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._state = State.MONITORING

        # UPS client — one UPS for everything
        self._nut = NUTClient(config.nut)

        # Per-cluster clients
        self._clusters: list[dict] = []
        for cluster_cfg in config.clusters:
            hc_client = HyperCoreClient(cluster_cfg)
            ipmi_clients = [IPMIClient(node) for node in cluster_cfg.nodes]
            self._clusters.append(
                {
                    "config": cluster_cfg,
                    "hypercore": hc_client,
                    "ipmi_clients": ipmi_clients,
                    "saved_vm_uuids": [],
                }
            )

        # Timestamp tracking for delay windows
        self._host_shutdown_timer_start: float | None = None

    def run(self) -> None:
        """Main loop — poll UPS and dispatch to current state handler."""
        logger.info("Starting power manager in state %s", self._state.name)
        self._nut.connect()
        # Tracks whether we're in a poll failure streak.
        # Suppresses repeated error logs until polling recovers.
        poll_failed = False

        while True:
            try:
                ups = self._nut.poll()
                if poll_failed:
                    logger.info("UPS polling recovered")
                    poll_failed = False
            except Exception as e:
                if not poll_failed:
                    logger.error("Failed to poll UPS: %s", e)
                    poll_failed = True
                try:
                    self._nut.connect()
                except Exception:
                    pass
                time.sleep(self._config.nut.poll_interval_seconds)
                continue

            self._handle_state(ups)
            time.sleep(self._config.nut.poll_interval_seconds)

    def _handle_state(self, ups) -> None:
        """Dispatch to the handler for the current state."""
        match self._state:
            case State.MONITORING:
                self._handle_monitoring(ups)
            case State.ON_BATTERY:
                self._handle_on_battery(ups)
            case State.SHUTTING_DOWN_VMS:
                self._handle_shutting_down_vms(ups)
            case State.WAITING_FOR_HOST_SHUTDOWN:
                self._handle_waiting_for_host_shutdown(ups)
            case State.SHUTTING_DOWN_HOSTS:
                self._handle_shutting_down_hosts(ups)
            case State.WAITING_FOR_POWER:
                self._handle_waiting_for_power(ups)
            case State.POWERING_ON_HOSTS:
                self._handle_powering_on_hosts(ups)
            case State.STARTING_VMS:
                self._handle_starting_vms(ups)

    def _handle_monitoring(self, ups) -> None:
        """Normal operation — watch for battery switchover."""
        if ups.on_battery:
            logger.warning(
                "UPS on battery — charge: %.0f%%, runtime: %.0fs",
                ups.battery_charge,
                ups.battery_runtime,
            )
            self._state = State.ON_BATTERY

    def _handle_on_battery(self, ups) -> None:
        """On battery — check if power returned or thresholds crossed."""
        if ups.on_line:
            logger.info("Power restored while on battery, resuming monitoring")
            self._state = State.MONITORING
            return

        thresholds = self._config.thresholds
        if (
            ups.battery_charge <= thresholds.battery_percent
            or ups.battery_runtime <= thresholds.runtime_seconds
        ):
            logger.warning(
                "Thresholds crossed — charge: %.0f%% (<= %d%%), "
                "runtime: %.0fs (<= %ds). Beginning VM shutdown.",
                ups.battery_charge,
                thresholds.battery_percent,
                ups.battery_runtime,
                thresholds.runtime_seconds,
            )
            self._state = State.SHUTTING_DOWN_VMS

    def _handle_shutting_down_vms(self, ups) -> None:
        """Shut down all running VMs across all clusters."""
        # Phase 1: Login and send SHUTDOWN to all running VMs
        for cluster in self._clusters:
            hc = cluster["hypercore"]
            config = cluster["config"]

            try:
                hc.login()
            except Exception as e:
                logger.error("Failed to login to %s: %s", config.host, e)
                continue

            vms = hc.get_vms()
            running = [vm for vm in vms if vm.state == "RUNNING"]

            # TODO: Persist saved_vm_uuids to disk so recovery can
            # resume if the Pi reboots during a power event.
            cluster["saved_vm_uuids"] = [vm.uuid for vm in running]
            logger.info(
                "Cluster %s — saving %d running VMs: %s",
                config.host,
                len(running),
                ", ".join(vm.name for vm in running),
            )

            for vm in running:
                logger.info("Sending SHUTDOWN to %s (%s)", vm.name, vm.uuid)
                try:
                    hc.shutdown_vm(vm.uuid)
                except Exception as e:
                    logger.error("Failed to shutdown %s: %s", vm.name, e)

        # Phase 2: Wait for VMs to reach SHUTOFF, STOP stragglers
        for cluster in self._clusters:
            hc = cluster["hypercore"]
            config = cluster["config"]
            pending = set(cluster["saved_vm_uuids"])

            if pending:
                deadline = time.time() + config.vm_shutdown_timeout
                while pending and time.time() < deadline:
                    time.sleep(10)
                    try:
                        vms = hc.get_vms()
                    except Exception:
                        continue
                    for vm in vms:
                        if vm.uuid in pending and vm.state == "SHUTOFF":
                            logger.info("VM %s reached SHUTOFF", vm.name)
                            # discard() won't raise if UUID was already removed
                            pending.discard(vm.uuid)

                # Force stop anything still running
                for vm_uuid in pending:
                    logger.warning(
                        "VM %s did not shut down in time, forcing STOP",
                        vm_uuid,
                    )
                    try:
                        hc.stop_vm(vm_uuid)
                    except Exception as e:
                        logger.error("Failed to STOP VM %s: %s", vm_uuid, e)

            try:
                hc.logout()
            except Exception:
                pass

        self._state = State.WAITING_FOR_HOST_SHUTDOWN

    def _handle_waiting_for_host_shutdown(self, ups) -> None:
        """Delay before host shutdown — power restore aborts this step."""
        if self._host_shutdown_timer_start is None:
            self._host_shutdown_timer_start = time.time()
            logger.info(
                "All VMs shut down. Waiting %ds before host shutdown.",
                self._config.thresholds.host_shutdown_delay,
            )

        if ups.on_line:
            logger.info("Power restored during host shutdown delay")
            self._host_shutdown_timer_start = None
            self._state = State.STARTING_VMS
            return

        elapsed = time.time() - self._host_shutdown_timer_start
        if elapsed >= self._config.thresholds.host_shutdown_delay:
            logger.warning("Host shutdown delay expired, powering off hosts")
            self._host_shutdown_timer_start = None
            self._state = State.SHUTTING_DOWN_HOSTS

    def _handle_shutting_down_hosts(self, ups) -> None:
        """Power off all hosts via IPMI."""
        for cluster in self._clusters:
            for ipmi in cluster["ipmi_clients"]:
                logger.warning("Powering off host %s", ipmi.hostname)
                try:
                    ipmi.power_off()
                except Exception as e:
                    logger.error(
                        "Failed to power off %s: %s",
                        ipmi.hostname,
                        e,
                    )

        self._state = State.WAITING_FOR_POWER

    def _handle_waiting_for_power(self, ups) -> None:
        """Everything is off. Wait for line power to return."""
        if ups.on_line:
            logger.info("Line power restored")
            self._state = State.POWERING_ON_HOSTS

    def _handle_powering_on_hosts(self, ups) -> None:
        """Power on hosts via IPMI and wait for HyperCore API."""
        for cluster in self._clusters:
            for ipmi in cluster["ipmi_clients"]:
                status = ipmi.power_status()
                if status == "on":
                    logger.info("Host %s already on, skipping", ipmi.hostname)
                else:
                    logger.info("Powering on host %s", ipmi.hostname)
                    try:
                        ipmi.power_on()
                    except Exception as e:
                        logger.error(
                            "Failed to power on %s: %s",
                            ipmi.hostname,
                            e,
                        )

        # Wait for HyperCore API on each cluster
        for cluster in self._clusters:
            hc = cluster["hypercore"]
            config = cluster["config"]
            logger.info("Waiting for HyperCore API on %s", config.host)

            # while/else: the else block runs only if the loop exits
            # WITHOUT a break, meaning we timed out waiting for the API.
            deadline = time.time() + self._config.thresholds.host_boot_timeout
            api_failed = False
            while time.time() < deadline:
                try:
                    hc.login()
                    hc.logout()
                    logger.info("HyperCore API on %s is ready", config.host)
                    break
                except Exception:
                    if not api_failed:
                        logger.info(
                            "HyperCore API on %s not ready yet, will retry",
                            config.host,
                        )
                        api_failed = True
                    time.sleep(15)
            else:
                logger.error(
                    "HyperCore API on %s did not become ready within %ds",
                    config.host,
                    self._config.thresholds.host_boot_timeout,
                )

        self._state = State.STARTING_VMS

    def _handle_starting_vms(self, ups) -> None:
        """Start VMs that were running before the shutdown."""
        for cluster in self._clusters:
            hc = cluster["hypercore"]
            config = cluster["config"]
            saved = cluster["saved_vm_uuids"]

            if not saved:
                continue

            try:
                hc.login()
            except Exception as e:
                logger.error("Failed to login to %s: %s", config.host, e)
                continue

            for vm_uuid in saved:
                logger.info("Starting VM %s on %s", vm_uuid, config.host)
                try:
                    hc.start_vm(vm_uuid)
                except Exception as e:
                    logger.error("Failed to start VM %s: %s", vm_uuid, e)

            try:
                hc.logout()
            except Exception:
                pass

            cluster["saved_vm_uuids"] = []

        self._state = State.MONITORING
        logger.info("Recovery complete. Resuming normal monitoring.")
