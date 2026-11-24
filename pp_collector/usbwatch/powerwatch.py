"""Detects that the generator was started because of a power outage.

Rule: the engine goes from stopped to running while the mains (utility) supply
is known and absent. Starts with mains present (manual / test mode) are ignored,
and so is an engine that was already running when the watcher started.

This needs live readings from the controller, which the DSE exposes over
Modbus (RS485), not over its USB-B programming port.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Sequence

from pp_collector.dse import DseController, MinimalModbusReader, ModbusError, Snapshot

from .events import GENSET_STARTED_BY_OUTAGE, Event, utc_iso
from .sinks import Sink

log = logging.getLogger("usbwatch")

StatusSource = Callable[[], Optional[Snapshot]]


def _mains_voltages(snap: Snapshot) -> list:
    volts = [snap.values.get(f"mains_l{n}_n_v") for n in (1, 2, 3)]
    return [v for v in volts if v is not None]


class ModbusStatusSource:
    """Reads a Snapshot from the DSE; returns None when it cannot (unplugged, no answer).

    The port is opened lazily and dropped on any failure, so it recovers by itself
    when the cable is plugged back in.
    """

    def __init__(self, port: str, slave_id: int = 10, baudrate: int = 19200, parity: str = "N",
                 stopbits: int = 1, timeout_s: float = 1.0):
        self._args = (port, slave_id, baudrate, parity, stopbits, timeout_s)
        self._reader: Optional[MinimalModbusReader] = None

    def __call__(self) -> Optional[Snapshot]:
        try:
            if self._reader is None:
                self._reader = MinimalModbusReader(*self._args)
            return DseController(self._reader).read_snapshot()
        except (ModbusError, OSError, ValueError) as exc:
            log.debug("sin lectura del controlador: %s", exc)
            self.close()
            return None

    def close(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
            self._reader = None


class PowerOutageWatcher:
    def __init__(
        self,
        source: StatusSource,
        sinks: Sequence[Sink],
        mains_present_v: float = 90.0,
        interval_s: float = 5.0,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.source = source
        self.sinks = sinks
        self.mains_present_v = mains_present_v
        self.interval_s = interval_s
        self._clock = clock
        self._monotonic = monotonic
        self._next_poll = 0.0
        self._was_running: Optional[bool] = None

    def step(self) -> None:
        now = self._monotonic()
        if now < self._next_poll:
            return
        self._next_poll = now + self.interval_s

        snap = self.source()
        if snap is None:
            return  # keep the previous state; a failed read says nothing about the plant
        running = snap.is_running()
        previous, self._was_running = self._was_running, running
        if previous is None or previous or not running:
            return  # first reading (baseline), still running, or stopped

        volts = _mains_voltages(snap)
        if not volts:
            log.info("Motor arranco pero el controlador no mide la red; no se puede atribuir a un corte")
            return
        if snap.mains_available(self.mains_present_v):
            return  # started with mains present: manual or test start

        data = {
            "control_mode": snap.control_mode,
            "mains_min_v": min(volts),
            "engine_rpm": snap.values.get("engine_rpm"),
            "gen_freq_hz": snap.values.get("gen_freq_hz"),
        }
        message = (
            f"Planta encendida por corte electrico (modo: {snap.control_mode}, "
            f"red: {min(volts):.1f} V, motor: {data['engine_rpm']} rpm)"
        )
        event = Event(GENSET_STARTED_BY_OUTAGE, utc_iso(self._clock()), message, data=data)
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception:
                log.exception("sink %s failed", type(sink).__name__)
