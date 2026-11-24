from types import SimpleNamespace

from pp_collector.usbwatch import CONNECTED, DISCONNECTED, HEARTBEAT, SerialPortDetector, UsbMonitor


def port(device="/dev/ttyUSB0", vid=0x0403, pid=0x6001):
    return SimpleNamespace(device=device, vid=vid, pid=pid, description="USB Serial", serial_number="A1")


class Collect:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    @property
    def kinds(self):
        return [e.kind for e in self.events]


class Env:
    """Fake OS port list + fake monotonic clock."""

    def __init__(self):
        self.ports = []
        self.now = 1000.0
        self.sink = Collect()
        self.detector = SerialPortDetector(vid=0x0403, pid=0x6001, lister=lambda: self.ports)
        self.monitor = UsbMonitor(
            self.detector, [self.sink], heartbeat_interval_s=3600,
            clock=lambda: 1_700_000_000.0, monotonic=lambda: self.now, sleep=lambda s: None,
        )

    def advance(self, seconds):
        self.now += seconds
        self.monitor.step()


def test_connect_is_reported_once():
    env = Env()
    env.monitor.step()
    assert env.sink.events == []
    env.ports = [port()]
    env.monitor.step()
    env.advance(2)
    assert env.sink.kinds == [CONNECTED]
    assert "Conexion exitosa" in env.sink.events[0].message


def test_heartbeat_every_hour_not_before():
    env = Env()
    env.ports = [port()]
    env.monitor.step()
    env.advance(3599)
    assert env.sink.kinds == [CONNECTED]
    env.advance(1)
    assert env.sink.kinds == [CONNECTED, HEARTBEAT]
    env.advance(3600)
    assert env.sink.kinds == [CONNECTED, HEARTBEAT, HEARTBEAT]
    assert env.sink.events[-1].uptime_s == 7200


def test_disconnect_reported_and_reconnect_restarts_cycle():
    env = Env()
    env.ports = [port()]
    env.monitor.step()
    env.advance(120)
    env.ports = []
    env.advance(2)
    assert env.sink.kinds == [CONNECTED, DISCONNECTED]
    assert env.sink.events[-1].uptime_s == 122
    env.advance(5000)  # no heartbeat while disconnected
    assert env.sink.kinds == [CONNECTED, DISCONNECTED]
    env.ports = [port()]
    env.advance(2)
    assert env.sink.kinds == [CONNECTED, DISCONNECTED, CONNECTED]


def test_filter_ignores_other_adapters():
    env = Env()
    env.ports = [port("/dev/ttyUSB1", vid=0x1A86, pid=0x7523)]
    env.monitor.step()
    assert env.sink.events == []


def test_failing_sink_does_not_stop_others():
    env = Env()

    class Broken:
        def emit(self, event):
            raise RuntimeError("boom")

    env.monitor.sinks.insert(0, Broken())
    env.ports = [port()]
    env.monitor.step()
    assert env.sink.kinds == [CONNECTED]


# --- power outage detection -------------------------------------------------

from pp_collector.dse import Snapshot
from pp_collector.usbwatch import GENSET_STARTED_BY_OUTAGE, PowerOutageWatcher


def snap(rpm=0, mains_v=230.0, mode="auto"):
    values = {"engine_rpm": rpm, "gen_freq_hz": 50.0 if rpm else 0.0}
    for n in (1, 2, 3):
        values[f"mains_l{n}_n_v"] = mains_v
    return Snapshot(ts=0.0, values=values, control_mode=mode, flags={})


class PowerEnv:
    def __init__(self):
        self.next = None
        self.now = 0.0
        self.sink = Collect()
        self.watcher = PowerOutageWatcher(
            lambda: self.next, [self.sink], mains_present_v=90, interval_s=5,
            clock=lambda: 1_700_000_000.0, monotonic=lambda: self.now,
        )

    def read(self, snapshot):
        self.next = snapshot
        self.now += 5
        self.watcher.step()


def test_start_during_outage_is_reported():
    env = PowerEnv()
    env.read(snap(rpm=0, mains_v=230))
    env.read(snap(rpm=0, mains_v=0))      # mains lost, engine still stopped
    assert env.sink.events == []
    env.read(snap(rpm=1500, mains_v=0))   # engine starts
    assert env.sink.kinds == [GENSET_STARTED_BY_OUTAGE]
    assert "corte electrico" in env.sink.events[0].message
    assert env.sink.events[0].data["mains_min_v"] == 0
    env.read(snap(rpm=1500, mains_v=0))   # still running: no repeat
    assert len(env.sink.events) == 1


def test_start_with_mains_present_is_not_an_outage():
    env = PowerEnv()
    env.read(snap(rpm=0))
    env.read(snap(rpm=1500, mains_v=230, mode="test_off_load"))
    assert env.sink.events == []


def test_engine_already_running_at_startup_is_not_reported():
    env = PowerEnv()
    env.read(snap(rpm=1500, mains_v=0))
    assert env.sink.events == []


def test_failed_read_keeps_state_and_next_outage_is_reported_again():
    env = PowerEnv()
    env.read(snap(rpm=0))
    env.read(None)                         # controller did not answer
    env.read(snap(rpm=1500, mains_v=0))
    assert env.sink.kinds == [GENSET_STARTED_BY_OUTAGE]
    env.read(snap(rpm=0, mains_v=230))     # outage over, engine stopped
    env.read(snap(rpm=1500, mains_v=0))    # second outage
    assert len(env.sink.events) == 2


def test_controller_without_mains_sensing_is_not_attributed_to_outage():
    env = PowerEnv()
    no_mains = lambda rpm: Snapshot(0.0, {"engine_rpm": rpm, "mains_l1_n_v": None}, "auto", {})
    env.read(no_mains(0))
    env.read(no_mains(1500))
    assert env.sink.events == []


def test_respects_interval_between_reads():
    env = PowerEnv()
    calls = []
    env.watcher.source = lambda: calls.append(1) or snap()
    env.watcher.step()                     # now=0 -> reads
    env.watcher.step()                     # same instant -> skipped
    assert len(calls) == 1
