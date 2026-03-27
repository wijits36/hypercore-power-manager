"""Tests for the power management state machine."""

from unittest.mock import MagicMock, patch

import pytest

from hypercore_power_manager.config import (
    ClusterConfig,
    Config,
    NodeConfig,
    NutConfig,
    ThresholdsConfig,
)
from hypercore_power_manager.hypercore_client import VMInfo
from hypercore_power_manager.monitor import PowerManager, State
from hypercore_power_manager.nut_client import UPSStatus


@pytest.fixture
def config():
    """Build a minimal Config for testing."""
    return Config(
        nut=NutConfig(host="10.0.0.1", ups_name="testups"),
        clusters=[
            ClusterConfig(
                host="https://cluster1.local",
                username="admin",
                password="testpass",
                nodes=[
                    NodeConfig(
                        ipmi_host="ipmi1.local",
                        ipmi_username="root",
                        ipmi_password="testpass",
                    ),
                ],
            ),
        ],
        thresholds=ThresholdsConfig(),
    )


@pytest.fixture
def manager(config):
    """Build a PowerManager with mocked clients."""
    mgr = PowerManager(config)

    # Replace the real NUT client with a mock
    mgr._nut = MagicMock()

    # Replace the real HyperCore and IPMI clients with mocks
    for cluster in mgr._clusters:
        cluster["hypercore"] = MagicMock()
        cluster["ipmi_clients"] = [MagicMock()]

    return mgr


def test_monitoring_to_on_battery(manager):
    """MONITORING transitions to ON_BATTERY when UPS reports battery power."""
    assert manager._state == State.MONITORING

    ups = UPSStatus(
        status="OB",
        battery_charge=100.0,
        battery_runtime=1500.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=27.0,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    assert manager._state == State.ON_BATTERY


def test_on_battery_power_restored(manager):
    """ON_BATTERY returns to MONITORING when line power is restored."""
    manager._state = State.ON_BATTERY

    ups = UPSStatus(
        status="OL",
        battery_charge=95.0,
        battery_runtime=1400.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=27.0,
        ups_load=27.0,
        on_battery=False,
        on_line=True,
    )

    manager._handle_state(ups)

    assert manager._state == State.MONITORING


def test_on_battery_threshold_battery(manager):
    """ON_BATTERY transitions to SHUTTING_DOWN_VMS when charge drops below threshold."""
    manager._state = State.ON_BATTERY

    ups = UPSStatus(
        status="OB",
        battery_charge=50.0,
        battery_runtime=1200.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=26.0,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    assert manager._state == State.SHUTTING_DOWN_VMS


def test_on_battery_threshold_runtime(manager):
    """ON_BATTERY transitions to SHUTTING_DOWN_VMS when runtime drops below threshold."""
    manager._state = State.ON_BATTERY

    ups = UPSStatus(
        status="OB",
        battery_charge=75.0,
        battery_runtime=600.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=26.0,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    assert manager._state == State.SHUTTING_DOWN_VMS


def test_on_battery_stays_when_above_thresholds(manager):
    """ON_BATTERY stays in ON_BATTERY when both thresholds are still safe."""
    manager._state = State.ON_BATTERY

    ups = UPSStatus(
        status="OB",
        battery_charge=51.0,
        battery_runtime=601.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=26.5,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    assert manager._state == State.ON_BATTERY


def test_shutting_down_vms_happy_path(manager):
    """SHUTTING_DOWN_VMS shuts down running VMs and transitions to WAITING."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        manager._state = State.SHUTTING_DOWN_VMS

        hc = manager._clusters[0]["hypercore"]

        # Simulate two running VMs that successfully reach SHUTOFF.
        # side_effect returns the next list each time get_vms() is called:
        #   call 1 (shutdown phase): both VMs still RUNNING
        #   call 2 (wait phase): both VMs reached SHUTOFF
        running_vms = [
            VMInfo(
                uuid="vm-1",
                name="web-server",
                state="RUNNING",
                desired_disposition="RUNNING",
            ),
            VMInfo(
                uuid="vm-2",
                name="db-server",
                state="RUNNING",
                desired_disposition="RUNNING",
            ),
        ]
        stopped_vms = [
            VMInfo(
                uuid="vm-1",
                name="web-server",
                state="SHUTOFF",
                desired_disposition="SHUTOFF",
            ),
            VMInfo(
                uuid="vm-2",
                name="db-server",
                state="SHUTOFF",
                desired_disposition="SHUTOFF",
            ),
        ]
        hc.get_vms.side_effect = [running_vms, stopped_vms]

        ups = UPSStatus(
            status="OB",
            battery_charge=40.0,
            battery_runtime=500.0,
            input_voltage=0.0,
            output_voltage=120.0,
            battery_voltage=26.0,
            ups_load=27.0,
            on_battery=True,
            on_line=False,
        )

        manager._handle_state(ups)

        # Verify state transition and that the right API calls were made
        assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN
        assert manager._clusters[0]["saved_vms"] == [
            ("vm-1", "web-server"),
            ("vm-2", "db-server"),
        ]
        hc.login.assert_called_once()
        hc.shutdown_vm.assert_any_call("vm-1")
        hc.shutdown_vm.assert_any_call("vm-2")


def test_shutting_down_vms_none_running(manager):
    """SHUTTING_DOWN_VMS handles empty VM list gracefully."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        manager._state = State.SHUTTING_DOWN_VMS

        hc = manager._clusters[0]["hypercore"]
        hc.get_vms.return_value = []

        ups = UPSStatus(
            status="OB",
            battery_charge=40.0,
            battery_runtime=500.0,
            input_voltage=0.0,
            output_voltage=120.0,
            battery_voltage=26.0,
            ups_load=27.0,
            on_battery=True,
            on_line=False,
        )

        manager._handle_state(ups)

        assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN
        assert manager._clusters[0]["saved_vms"] == []
        hc.shutdown_vm.assert_not_called()


def test_waiting_for_host_shutdown_power_restored(manager):
    """WAITING_FOR_HOST_SHUTDOWN jumps to STARTING_VMS if power returns."""
    manager._state = State.WAITING_FOR_HOST_SHUTDOWN
    # Timer hasn't started yet — first call initializes it
    manager._host_shutdown_timer_start = None

    ups_on_battery = UPSStatus(
        status="OB",
        battery_charge=35.0,
        battery_runtime=400.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=26.0,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    # First tick: starts the timer, stays in same state
    manager._handle_state(ups_on_battery)
    assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN
    assert manager._host_shutdown_timer_start is not None

    ups_restored = UPSStatus(
        status="OL",
        battery_charge=40.0,
        battery_runtime=500.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=27.0,
        ups_load=27.0,
        on_battery=False,
        on_line=True,
    )

    # Second tick: power is back, skip host shutdown and go start VMs
    manager._handle_state(ups_restored)
    assert manager._state == State.STARTING_VMS
    # Timer should be reset so it doesn't carry over to future events
    assert manager._host_shutdown_timer_start is None


def test_waiting_for_host_shutdown_timer_expires(manager):
    """WAITING_FOR_HOST_SHUTDOWN transitions to SHUTTING_DOWN_HOSTS after delay."""
    with patch("hypercore_power_manager.monitor.time.time") as mock_time:
        manager._state = State.WAITING_FOR_HOST_SHUTDOWN

        ups = UPSStatus(
            status="OB",
            battery_charge=30.0,
            battery_runtime=300.0,
            input_voltage=0.0,
            output_voltage=120.0,
            battery_voltage=25.5,
            ups_load=27.0,
            on_battery=True,
            on_line=False,
        )

        # First tick at T=1000: starts the timer
        mock_time.return_value = 1000.0
        manager._handle_state(ups)
        assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN

        # Second tick at T=1301: 301 seconds later, exceeds the default
        # host_shutdown_delay of 300 seconds
        mock_time.return_value = 1301.0
        manager._handle_state(ups)
        assert manager._state == State.SHUTTING_DOWN_HOSTS


def test_shutting_down_hosts(manager):
    """SHUTTING_DOWN_HOSTS powers off all hosts via IPMI."""
    manager._state = State.SHUTTING_DOWN_HOSTS

    ipmi = manager._clusters[0]["ipmi_clients"][0]

    ups = UPSStatus(
        status="OB",
        battery_charge=20.0,
        battery_runtime=200.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=25.0,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    assert manager._state == State.WAITING_FOR_POWER
    ipmi.power_off.assert_called_once()


def test_waiting_for_power_stays_on_battery(manager):
    """WAITING_FOR_POWER stays put while still on battery."""
    manager._state = State.WAITING_FOR_POWER

    ups = UPSStatus(
        status="OB",
        battery_charge=15.0,
        battery_runtime=100.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=24.5,
        ups_load=5.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    assert manager._state == State.WAITING_FOR_POWER


def test_waiting_for_power_restored(manager):
    """WAITING_FOR_POWER transitions to POWERING_ON_HOSTS when power returns."""
    manager._state = State.WAITING_FOR_POWER

    ups = UPSStatus(
        status="OL",
        battery_charge=15.0,
        battery_runtime=100.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=24.5,
        ups_load=5.0,
        on_battery=False,
        on_line=True,
    )

    manager._handle_state(ups)

    assert manager._state == State.POWERING_ON_HOSTS


def test_powering_on_hosts(manager):
    """POWERING_ON_HOSTS powers on hosts and waits for API readiness."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        with patch("hypercore_power_manager.monitor.time.time") as mock_time:
            manager._state = State.POWERING_ON_HOSTS

            ipmi = manager._clusters[0]["ipmi_clients"][0]
            hc = manager._clusters[0]["hypercore"]

            # Host is off, needs to be powered on
            ipmi.power_status.return_value = "off"

            # HyperCore API responds immediately on first login attempt
            hc.login.return_value = None

            # Time mock: just needs to stay within the boot timeout
            mock_time.return_value = 1000.0

            ups = UPSStatus(
                status="OL",
                battery_charge=20.0,
                battery_runtime=200.0,
                input_voltage=122.0,
                output_voltage=120.0,
                battery_voltage=25.0,
                ups_load=5.0,
                on_battery=False,
                on_line=True,
            )

            manager._handle_state(ups)

            assert manager._state == State.STARTING_VMS
            ipmi.power_on.assert_called_once()


def test_powering_on_hosts_already_on(manager):
    """POWERING_ON_HOSTS skips power_on if host is already running."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        with patch("hypercore_power_manager.monitor.time.time") as mock_time:
            manager._state = State.POWERING_ON_HOSTS

            ipmi = manager._clusters[0]["ipmi_clients"][0]
            hc = manager._clusters[0]["hypercore"]

            # Host is already on
            ipmi.power_status.return_value = "on"
            hc.login.return_value = None
            mock_time.return_value = 1000.0

            ups = UPSStatus(
                status="OL",
                battery_charge=20.0,
                battery_runtime=200.0,
                input_voltage=122.0,
                output_voltage=120.0,
                battery_voltage=25.0,
                ups_load=5.0,
                on_battery=False,
                on_line=True,
            )

            manager._handle_state(ups)

            assert manager._state == State.STARTING_VMS
            # Should NOT have called power_on since host was already running
            ipmi.power_on.assert_not_called()


def test_monitoring_stays_on_line(manager):
    """MONITORING stays in MONITORING when UPS is on line power."""
    assert manager._state == State.MONITORING

    ups = UPSStatus(
        status="OL",
        battery_charge=100.0,
        battery_runtime=1500.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=27.0,
        ups_load=27.0,
        on_battery=False,
        on_line=True,
    )

    manager._handle_state(ups)

    assert manager._state == State.MONITORING


def test_shutting_down_vms_force_stop_stragglers(manager):
    """SHUTTING_DOWN_VMS force-STOPs VMs that don't reach SHUTOFF in time."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        with patch("hypercore_power_manager.monitor.time.time") as mock_time:
            manager._state = State.SHUTTING_DOWN_VMS

            hc = manager._clusters[0]["hypercore"]

            running_vms = [
                VMInfo(
                    uuid="vm-1",
                    name="web-server",
                    state="RUNNING",
                    desired_disposition="RUNNING",
                ),
                VMInfo(
                    uuid="vm-2",
                    name="stubborn-vm",
                    state="RUNNING",
                    desired_disposition="RUNNING",
                ),
            ]

            # vm-1 shuts down cleanly, vm-2 stays RUNNING through every poll
            still_running = [
                VMInfo(
                    uuid="vm-1",
                    name="web-server",
                    state="SHUTOFF",
                    desired_disposition="SHUTOFF",
                ),
                VMInfo(
                    uuid="vm-2",
                    name="stubborn-vm",
                    state="RUNNING",
                    desired_disposition="RUNNING",
                ),
            ]

            # First call returns running VMs, subsequent calls show vm-2 stuck
            hc.get_vms.side_effect = [running_vms] + [still_running] * 10

            # Simulate time progressing past the vm_shutdown_timeout (300s).
            # The handler calls time.time() to set a deadline and check it
            # each iteration. We start at T=1000, then jump past the deadline.
            call_count = [0]

            def fake_time():
                call_count[0] += 1
                # First call sets the deadline (1000 + 300 = 1300)
                # After a few calls, jump past the deadline
                if call_count[0] <= 2:
                    return 1000.0
                return 1301.0

            mock_time.side_effect = fake_time

            ups = UPSStatus(
                status="OB",
                battery_charge=40.0,
                battery_runtime=500.0,
                input_voltage=0.0,
                output_voltage=120.0,
                battery_voltage=26.0,
                ups_load=27.0,
                on_battery=True,
                on_line=False,
            )

            manager._handle_state(ups)

            assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN
            # vm-2 should have been force-stopped
            hc.stop_vm.assert_called_with("vm-2")
            # vm-1 shut down cleanly, should NOT have been force-stopped
            assert all(call.args[0] != "vm-1" for call in hc.stop_vm.call_args_list)


def test_starting_vms(manager):
    """STARTING_VMS restarts previously-running VMs and returns to MONITORING."""
    manager._state = State.STARTING_VMS

    hc = manager._clusters[0]["hypercore"]
    # These were saved during the shutdown phase
    manager._clusters[0]["saved_vms"] = [("vm-1", "web-server"), ("vm-2", "db-server")]

    ups = UPSStatus(
        status="OL",
        battery_charge=30.0,
        battery_runtime=400.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=26.0,
        ups_load=27.0,
        on_battery=False,
        on_line=True,
    )

    manager._handle_state(ups)

    assert manager._state == State.MONITORING
    hc.login.assert_called_once()
    hc.start_vm.assert_any_call("vm-1")
    hc.start_vm.assert_any_call("vm-2")
    # saved list should be cleared after restart
    assert manager._clusters[0]["saved_vms"] == []


def test_starting_vms_none_saved(manager):
    """STARTING_VMS handles empty saved list gracefully."""
    manager._state = State.STARTING_VMS

    hc = manager._clusters[0]["hypercore"]
    manager._clusters[0]["saved_vms"] = []

    ups = UPSStatus(
        status="OL",
        battery_charge=30.0,
        battery_runtime=400.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=26.0,
        ups_load=27.0,
        on_battery=False,
        on_line=True,
    )

    manager._handle_state(ups)

    assert manager._state == State.MONITORING
    # No VMs to start, so login should not have been called
    hc.login.assert_not_called()
    hc.start_vm.assert_not_called()


def test_shutting_down_vms_login_fails(manager):
    """SHUTTING_DOWN_VMS continues to next cluster if login fails."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        manager._state = State.SHUTTING_DOWN_VMS

        hc = manager._clusters[0]["hypercore"]
        # Simulate login failure
        hc.login.side_effect = Exception("Connection refused")

        ups = UPSStatus(
            status="OB",
            battery_charge=40.0,
            battery_runtime=500.0,
            input_voltage=0.0,
            output_voltage=120.0,
            battery_voltage=26.0,
            ups_load=27.0,
            on_battery=True,
            on_line=False,
        )

        manager._handle_state(ups)

        # Should still transition — can't just get stuck because one cluster is down
        assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN
        # get_vms should never have been called since login failed
        hc.get_vms.assert_not_called()


def test_shutting_down_hosts_ipmi_failure(manager):
    """SHUTTING_DOWN_HOSTS continues even if IPMI power_off fails."""
    manager._state = State.SHUTTING_DOWN_HOSTS

    ipmi = manager._clusters[0]["ipmi_clients"][0]
    ipmi.power_off.side_effect = Exception("IPMI timeout")

    ups = UPSStatus(
        status="OB",
        battery_charge=20.0,
        battery_runtime=200.0,
        input_voltage=0.0,
        output_voltage=120.0,
        battery_voltage=25.0,
        ups_load=27.0,
        on_battery=True,
        on_line=False,
    )

    manager._handle_state(ups)

    # Should still transition — don't get stuck retrying forever
    assert manager._state == State.WAITING_FOR_POWER
    ipmi.power_off.assert_called_once()


def test_starting_vms_login_fails(manager):
    """STARTING_VMS continues to next cluster if login fails."""
    manager._state = State.STARTING_VMS

    hc = manager._clusters[0]["hypercore"]
    manager._clusters[0]["saved_vms"] = [("vm-1", "web-server")]
    hc.login.side_effect = Exception("Connection refused")

    ups = UPSStatus(
        status="OL",
        battery_charge=30.0,
        battery_runtime=400.0,
        input_voltage=122.0,
        output_voltage=120.0,
        battery_voltage=26.0,
        ups_load=27.0,
        on_battery=False,
        on_line=True,
    )

    manager._handle_state(ups)

    # Should still return to MONITORING — don't get stuck in STARTING_VMS
    assert manager._state == State.MONITORING
    hc.start_vm.assert_not_called()


def test_full_power_event_cycle(manager):
    """Full cycle: MONITORING through shutdown, recovery, back to MONITORING."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        with patch("hypercore_power_manager.monitor.time.time") as mock_time:
            hc = manager._clusters[0]["hypercore"]
            ipmi = manager._clusters[0]["ipmi_clients"][0]
            mock_time.return_value = 1000.0

            # 1. MONITORING — power goes out
            ups_battery = UPSStatus(
                status="OB",
                battery_charge=100.0,
                battery_runtime=1500.0,
                input_voltage=0.0,
                output_voltage=120.0,
                battery_voltage=27.0,
                ups_load=27.0,
                on_battery=True,
                on_line=False,
            )
            manager._handle_state(ups_battery)
            assert manager._state == State.ON_BATTERY

            # 2. ON_BATTERY — thresholds crossed
            ups_low = UPSStatus(
                status="OB",
                battery_charge=45.0,
                battery_runtime=500.0,
                input_voltage=0.0,
                output_voltage=120.0,
                battery_voltage=26.0,
                ups_load=27.0,
                on_battery=True,
                on_line=False,
            )
            manager._handle_state(ups_low)
            assert manager._state == State.SHUTTING_DOWN_VMS

            # 3. SHUTTING_DOWN_VMS — one VM shuts down cleanly
            running = [
                VMInfo(
                    uuid="vm-1",
                    name="web",
                    state="RUNNING",
                    desired_disposition="RUNNING",
                )
            ]
            stopped = [
                VMInfo(
                    uuid="vm-1",
                    name="web",
                    state="SHUTOFF",
                    desired_disposition="SHUTOFF",
                )
            ]
            hc.get_vms.side_effect = [running, stopped]
            manager._handle_state(ups_low)
            assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN

            # 4. WAITING_FOR_HOST_SHUTDOWN — timer starts, then expires
            manager._handle_state(ups_low)
            assert manager._state == State.WAITING_FOR_HOST_SHUTDOWN
            mock_time.return_value = 1301.0
            manager._handle_state(ups_low)
            assert manager._state == State.SHUTTING_DOWN_HOSTS

            # 5. SHUTTING_DOWN_HOSTS — powers off hosts
            manager._handle_state(ups_low)
            assert manager._state == State.WAITING_FOR_POWER

            # 6. WAITING_FOR_POWER — power returns
            ups_restored = UPSStatus(
                status="OL",
                battery_charge=15.0,
                battery_runtime=100.0,
                input_voltage=122.0,
                output_voltage=120.0,
                battery_voltage=25.0,
                ups_load=5.0,
                on_battery=False,
                on_line=True,
            )
            manager._handle_state(ups_restored)
            assert manager._state == State.POWERING_ON_HOSTS

            # 7. POWERING_ON_HOSTS — hosts come back, API ready
            ipmi.power_status.return_value = "off"
            hc.login.side_effect = None
            hc.login.return_value = None
            mock_time.return_value = 2000.0
            manager._handle_state(ups_restored)
            assert manager._state == State.STARTING_VMS

            # 8. STARTING_VMS — VMs restarted, back to normal
            manager._handle_state(ups_restored)
            assert manager._state == State.MONITORING


def test_run_poll_failure_and_recovery(manager):
    """run() handles poll failures gracefully and recovers."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        # Configure the NUT mock to fail twice, then succeed
        normal_ups = UPSStatus(
            status="OL",
            battery_charge=100.0,
            battery_runtime=1500.0,
            input_voltage=122.0,
            output_voltage=120.0,
            battery_voltage=27.0,
            ups_load=27.0,
            on_battery=False,
            on_line=True,
        )

        # poll() fails twice, then returns normal status, then we
        # raise KeyboardInterrupt to exit the infinite loop
        manager._nut.poll.side_effect = [
            Exception("Connection lost"),
            Exception("Still down"),
            normal_ups,
            KeyboardInterrupt,
        ]
        # connect() should succeed on retry
        manager._nut.connect.return_value = None

        try:
            manager.run()
        except KeyboardInterrupt:
            pass

        # connect() is only called once at startup — poll() handles
        # its own connections, so no reconnect attempts are needed
        assert manager._nut.connect.call_count == 1
        # Should still be in MONITORING — failures don't change state
        assert manager._state == State.MONITORING


def test_starting_vms_retry_then_succeed(manager):
    """STARTING_VMS retries failed VM starts and eventually succeeds."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        manager._state = State.STARTING_VMS

        hc = manager._clusters[0]["hypercore"]
        manager._clusters[0]["saved_vms"] = [
            ("vm-1", "web-server"),
            ("vm-2", "db-server"),
        ]

        # Attempt 1: both fail (API subsystem not ready — the S11 bug).
        # Attempt 2: both succeed.
        # side_effect processes calls in order, so:
        #   call 1 (vm-1, attempt 1): raise
        #   call 2 (vm-2, attempt 1): raise
        #   call 3 (vm-1, attempt 2): succeed
        #   call 4 (vm-2, attempt 2): succeed
        hc.start_vm.side_effect = [
            Exception("400 Bad Request"),
            Exception("400 Bad Request"),
            None,
            None,
        ]

        ups = UPSStatus(
            status="OL",
            battery_charge=30.0,
            battery_runtime=400.0,
            input_voltage=122.0,
            output_voltage=120.0,
            battery_voltage=26.0,
            ups_load=27.0,
            on_battery=False,
            on_line=True,
        )

        manager._handle_state(ups)

        assert manager._state == State.MONITORING
        assert hc.start_vm.call_count == 4
        assert manager._clusters[0]["saved_vms"] == []


def test_starting_vms_partial_failure(manager):
    """STARTING_VMS only retries VMs that failed, not ones that succeeded."""
    with patch("hypercore_power_manager.monitor.time.sleep"):
        manager._state = State.STARTING_VMS

        hc = manager._clusters[0]["hypercore"]
        manager._clusters[0]["saved_vms"] = [
            ("vm-1", "web-server"),
            ("vm-2", "db-server"),
        ]

        # Attempt 1: vm-1 succeeds, vm-2 fails.
        # Attempt 2: vm-2 succeeds.
        hc.start_vm.side_effect = [
            None,
            Exception("400 Bad Request"),
            None,
        ]

        ups = UPSStatus(
            status="OL",
            battery_charge=30.0,
            battery_runtime=400.0,
            input_voltage=122.0,
            output_voltage=120.0,
            battery_voltage=26.0,
            ups_load=27.0,
            on_battery=False,
            on_line=True,
        )

        manager._handle_state(ups)

        assert manager._state == State.MONITORING
        # 3 total calls: vm-1 once (success), vm-2 twice (fail then success)
        assert hc.start_vm.call_count == 3
        # Verify vm-1 was NOT retried — it should appear exactly once
        vm1_calls = [c for c in hc.start_vm.call_args_list if c.args[0] == "vm-1"]
        assert len(vm1_calls) == 1


def test_starting_vms_exhausted_retries(manager):
    """STARTING_VMS gives up after max_attempts and transitions to MONITORING."""
    with patch("hypercore_power_manager.monitor.time.sleep") as mock_sleep:
        manager._state = State.STARTING_VMS

        hc = manager._clusters[0]["hypercore"]
        manager._clusters[0]["saved_vms"] = [("vm-1", "web-server")]

        # Every start_vm call fails — API never becomes ready
        hc.start_vm.side_effect = Exception("400 Bad Request")

        ups = UPSStatus(
            status="OL",
            battery_charge=30.0,
            battery_runtime=400.0,
            input_voltage=122.0,
            output_voltage=120.0,
            battery_voltage=26.0,
            ups_load=27.0,
            on_battery=False,
            on_line=True,
        )

        manager._handle_state(ups)

        # Should still transition to MONITORING — don't get stuck
        assert manager._state == State.MONITORING
        # 20 attempts, 1 VM each = 20 calls
        assert hc.start_vm.call_count == 20
        # saved list still cleared — don't carry stale UUIDs into next event
        assert manager._clusters[0]["saved_vms"] == []
        # 20 sleeps (after each failed attempt except the last)
        assert mock_sleep.call_count == 20
