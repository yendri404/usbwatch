"""State machine: detects connect / disconnect and emits an hourly heartbeat."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Sequence

from .detector import Detector
from .events import CONNECTED, DISCONNECTED, HEARTBEAT, DeviceInfo, Event, utc_iso
from .sinks import Sink

log = logging.getLogger("usbwatch")


def _label(device: DeviceInfo) -> str:
    ids = f" [{device.vid:04x}:{device.pid:04x}]" if device.vid is not None and device.pid is not None else ""
    return f"{device.port}{ids} {device.description}".strip()


def _hms(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m {s:02d}s"


class UsbMonitor:
    def __init__(
        self,
        detector: Detector,
        sinks: Sequence[Sink],
        poll_interval_s: float = 2.0,
        heartbeat_interval_s: float = 3600.0,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        watchers: Sequence = (),
    ):
        self.detector = detector
        self.sinks = list(sinks)
        self.watchers = list(watchers)  # extra checks with a step() method, e.g. PowerOutageWatcher
        self.poll_interval_s = poll_interval_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self.running = True
        self._device: Optional[DeviceInfo] = None
        self._since = 0.0
        self._next_heartbeat = 0.0

    def stop(self, *_args) -> None:
        self.running = False

    def _emit(self, kind: str, message: str, device: DeviceInfo, uptime_s: Optional[int] = None) -> None:
        event = Event(kind, utc_iso(self._clock()), message, device, uptime_s)
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception:  # one broken sink must never stop the monitor
                log.exception("sink %s failed", type(sink).__name__)

    def step(self) -> None:
        """One polling iteration. Public so tests can drive it with a fake clock."""
        found = self.detector.find()
        now = self._monotonic()

        if found and self._device is None:
            self._device, self._since = found, now
            self._next_heartbeat = now + self.heartbeat_interval_s
            self._emit(CONNECTED, f"Conexion exitosa: {_label(found)}", found)
        elif found is None and self._device is not None:
            lost, uptime = self._device, int(now - self._since)
            self._device = None
            self._emit(
                DISCONNECTED,
                f"Dispositivo desconectado: {_label(lost)} (estuvo conectado {_hms(uptime)})",
                lost,
                uptime,
            )
        elif found and self._device is not None:
            self._device = found
            if now >= self._next_heartbeat:
                uptime = int(now - self._since)
                self._next_heartbeat = now + self.heartbeat_interval_s
                self._emit(
                    HEARTBEAT,
                    f"Dispositivo conectado y funcionando: {_label(found)} (conectado hace {_hms(uptime)})",
                    found,
                    uptime,
                )

    def run(self) -> None:
        log.info("Monitor USB iniciado; esperando el dispositivo...")
        while self.running:
            try:
                self.step()
            except Exception:  # keep the service alive on transient OS errors
                log.exception("error al consultar los puertos USB")
            for watcher in self.watchers:
                try:
                    watcher.step()
                except Exception:
                    log.exception("watcher %s failed", type(watcher).__name__)
            self._sleep(self.poll_interval_s)
        log.info("Monitor USB detenido")
