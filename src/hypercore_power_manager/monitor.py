"""Main monitoring loop and power management state machine."""

import logging
import time
from datetime import datetime, timezone
from enum import Enum, auto

from .config import Config
from .hypercore_client import HyperCoreClient
from .ipmi_client import IPMIClient
from .nut_client import NUTClient
from .state import delete_state, load_state, save_state


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
                    "saved_vms": [],
                }
            )

        # Timestamp tracking for delay windows
        self._host_shutdown_timer_start: float | None = None

    def run(self) -> None:
        """Main loop — poll UPS and dispatch to current state handler."""
        logger.info("Starting power manager in state %s", self._state.name)

        # Log configured topology so the journal is self-contained
        nut = self._config.nut
        logger.info(
            "Connecting to NUT at %s:%d (UPS: %s, poll interval: %ds)",
            nut.host,
            nut.port,
            nut.ups_name,
            nut.poll_interval_seconds,
        )
        self._nut.connect()

        for cluster in self._clusters:
            cfg = cluster["config"]
            ipmi_hosts = ", ".join(node.ipmi_host for node in cfg.nodes)
            logger.info(
                "Cluster %s (IPMI: %s; VM shutdown timeout: %ds; verify SSL: %s)",
                cfg.host,
                ipmi_hosts,
                cfg.vm_shutdown_timeout,
                cfg.verify_ssl,
            )

        t = self._config.thresholds
        logger.info(
            "Thresholds: battery <= %d%%, runtime <= %ds, "
            "host shutdown delay: %ds, host boot timeout: %ds",
            t.battery_percent,
            t.runtime_seconds,
            t.host_shutdown_delay,
            t.host_boot_timeout,
        )

        # Check for unfinished shutdown cycle from a previous run
        state_data = load_state()
        if state_data is not None:
            clusters_info = state_data.get("clusters", {})
            restored_count = 0

            for cluster in self._clusters:
                host = cluster["config"].host
                if host in clusters_info:
                    vms = clusters_info[host]
                    cluster["saved_vms"] = [(vm["uuid"], vm["name"]) for vm in vms]
                    restored_count += len(vms)

            if restored_count > 0:
                logger.warning(
                    "Recovered state file from %s with %d VM(s) across %d cluster(s)",
                    state_data.get("timestamp", "unknown"),
                    restored_count,
                    len(clusters_info),
                )

                # Poll NUT to determine recovery path — retry if NUT
                # is still starting up after a power event reboot
                ups = None
                for attempt in range(6):
                    try:
                        ups = self._nut.poll()
                        break
                    except Exception:
                        if attempt == 0:
                            logger.info("NUT not ready during recovery, will retry")
                        time.sleep(10)

                if ups is not None and ups.on_line:
                    logger.info("Power is on line, entering recovery")
                    self._state = State.POWERING_ON_HOSTS
                else:
                    if ups is None:
                        logger.warning("Could not reach NUT, assuming power still out")
                    else:
                        logger.warning(
                            "Power still on battery, re-entering shutdown cycle"
                        )
                    self._state = State.SHUTTING_DOWN_VMS
            else:
                logger.warning(
                    "State file found from %s but no matching clusters "
                    "in config, ignoring",
                    state_data.get("timestamp", "unknown"),
                )
                delete_state()

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
        battery_crossed = ups.battery_charge <= thresholds.battery_percent
        runtime_crossed = ups.battery_runtime <= thresholds.runtime_seconds

        if battery_crossed or runtime_crossed:
            # Build a list of which thresholds triggered
            triggered = []
            if battery_crossed:
                triggered.append("battery")
            if runtime_crossed:
                triggered.append("runtime")

            logger.warning(
                "Threshold crossed (%s) — charge: %.0f%% (limit: %d%%, %s), "
                "runtime: %.0fs (limit: %ds, %s). Beginning VM shutdown.",
                ", ".join(triggered),
                ups.battery_charge,
                thresholds.battery_percent,
                "CROSSED" if battery_crossed else "ok",
                ups.battery_runtime,
                thresholds.runtime_seconds,
                "CROSSED" if runtime_crossed else "ok",
            )
            self._state = State.SHUTTING_DOWN_VMS

    def _handle_shutting_down_vms(self, ups) -> None:
        """Shut down all running VMs across all clusters."""
        timestamp = datetime.now(timezone.utc).isoformat()
        logged_in_clusters = set()

        # Phase 1: Login and send SHUTDOWN to all running VMs
        for cluster in self._clusters:
            hc = cluster["hypercore"]
            config = cluster["config"]

            # If saved_vms is already populated (loaded from state file
            # after a crash), skip this cluster — VMs were already
            # shut down in the previous run.
            if cluster["saved_vms"]:
                logger.info(
                    "Cluster %s — restored %d VMs from state file, skipping shutdown",
                    config.host,
                    len(cluster["saved_vms"]),
                )
                continue

            try:
                hc.login()
            except Exception as e:
                logger.error("Failed to login to %s: %s", config.host, e)
                continue

            logged_in_clusters.add(config.host)

            vms = hc.get_vms()
            running = [vm for vm in vms if vm.state == "RUNNING"]

            cluster["saved_vms"] = [(vm.uuid, vm.name) for vm in running]
            logger.info(
                "Cluster %s — saving %d running VMs: %s",
                config.host,
                len(running),
                ", ".join(vm.name for vm in running),
            )

            # Persist state incrementally — survives Pi crash between clusters
            self._write_state(timestamp)

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

            # Skip clusters we didn't log into (restored from state file
            # or login failed) — no active session to query
            if config.host not in logged_in_clusters:
                continue

            pending_names = {uuid: name for uuid, name in cluster["saved_vms"]}
            pending = set(pending_names.keys())

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
                            logger.info(
                                "[%s] VM %s reached SHUTOFF", config.host, vm.name
                            )
                            pending.discard(vm.uuid)

                # Force stop anything still running
                for vm_uuid in pending:
                    logger.warning(
                        "[%s] VM %s (%s) did not shut down in time, forcing STOP",
                        config.host,
                        pending_names.get(vm_uuid, vm_uuid),
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
        max_attempts = 20
        retry_delay = 15

        for cluster in self._clusters:
            hc = cluster["hypercore"]
            config = cluster["config"]
            saved = cluster["saved_vms"]

            if not saved:
                continue

            try:
                hc.login()
            except Exception as e:
                logger.error("[%s] Failed to login: %s", config.host, e)
                continue

            # Build UUID->name lookup for logging
            vm_names = {uuid: name for uuid, name in saved}
            remaining = [uuid for uuid, name in saved]
            total = len(remaining)

            logger.info(
                "[%s] Starting %d VMs: %s",
                config.host,
                total,
                ", ".join(vm_names[uuid] for uuid in remaining),
            )

            for attempt in range(1, max_attempts + 1):
                failed = []
                for vm_uuid in remaining:
                    try:
                        hc.start_vm(vm_uuid)
                    except Exception:
                        failed.append(vm_uuid)

                remaining = failed

                if not remaining:
                    logger.info(
                        "[%s] Attempt %d/%d: all %d VMs started",
                        config.host,
                        attempt,
                        max_attempts,
                        total,
                    )
                    break

                logger.warning(
                    "[%s] Attempt %d/%d: %d/%d started — retrying in %ds",
                    config.host,
                    attempt,
                    max_attempts,
                    total - len(remaining),
                    total,
                    retry_delay,
                )
                time.sleep(retry_delay)

            if remaining:
                logger.error(
                    "[%s] Gave up starting %d VM(s) after %d attempts: %s",
                    config.host,
                    len(remaining),
                    max_attempts,
                    ", ".join(vm_names.get(uuid, uuid) for uuid in remaining),
                )

            try:
                hc.logout()
            except Exception:
                pass

            cluster["saved_vms"] = []

        delete_state()
        self._state = State.MONITORING
        logger.info("Recovery complete. Resuming normal monitoring.")

    def _write_state(self, timestamp: str) -> None:
        """Build state dict from all clusters and persist to disk."""
        clusters_data = {}
        for cluster in self._clusters:
            if cluster["saved_vms"]:
                host = cluster["config"].host
                clusters_data[host] = [
                    {"uuid": uuid, "name": name} for uuid, name in cluster["saved_vms"]
                ]

        save_state({"timestamp": timestamp, "clusters": clusters_data})
