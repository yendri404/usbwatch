"""Ciclo principal: leer el DSE, resumir, encolar y enviar a Odoo."""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone

from .aggregator import Aggregator
from .config import Config
from .dse import DseController, ModbusError, Snapshot
from .outbox import Outbox
from .uplink import OdooUplink, RejectedError

log = logging.getLogger(__name__)

# Lecturas fallidas seguidas antes de reportar el controlador como caído.
CONTROLLER_FAIL_THRESHOLD = 3
MAX_BACKOFF_S = 300


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def status_message(snap: Snapshot | None, ts: float, mains_present_v: float) -> dict:
    if snap is None:
        return {"ts": iso(ts), "status": {"controller_online": False}}
    return {
        "ts": iso(snap.ts),
        "status": {
            "controller_online": True,
            "control_mode": snap.control_mode,
            "engine_running": snap.is_running(),
            "mains_available": snap.mains_available(mains_present_v),
            "flags": snap.flags,
            "values": snap.values,
            "alarms": snap.alarms,
        },
    }


def state_key(message: dict) -> tuple:
    """Lo que, si cambia, merece un envío inmediato."""
    st = message["status"]
    alarms = frozenset((a["code"], a["severity"]) for a in st.get("alarms", ()))
    return (st["controller_online"], st.get("control_mode"), st.get("engine_running"),
            st.get("mains_available"), alarms)


class Service:
    def __init__(self, cfg: Config, controller: DseController, outbox: Outbox, uplink: OdooUplink,
                 clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.controller = controller
        self.outbox = outbox
        self.uplink = uplink
        self.clock = clock
        self.sleep = sleep
        self.aggregator = Aggregator()
        self.running = True
        self._fails = 0
        self._last_snap: Snapshot | None = None
        self._last_key: tuple | None = None
        self._next_report = clock() + cfg.poll.report_interval_s
        self._next_send = 0.0
        self._send_failures = 0

    def stop(self, *_):
        self.running = False

    def tick(self) -> None:
        now = self.clock()
        try:
            snap = self.controller.read_snapshot()
            self._fails = 0
            self._last_snap = snap
            self.aggregator.add(snap)
        except ModbusError as exc:
            self._fails += 1
            log.warning("lectura DSE fallida (%s seguidas): %s", self._fails, exc)
            if self._fails >= CONTROLLER_FAIL_THRESHOLD:
                self._last_snap = None

        controller_down = self._fails >= CONTROLLER_FAIL_THRESHOLD
        if self._last_snap is not None or controller_down:
            message = status_message(self._last_snap, now, self.cfg.poll.mains_present_v)
            key = state_key(message)
            if key != self._last_key:
                log.info("cambio de estado: %s", key)
                self.outbox.push(message)
                self._last_key = key

        if now >= self._next_report:
            self._next_report = now + self.cfg.poll.report_interval_s
            reading = self.aggregator.flush(now)
            # El reporte periódico lleva también el estado: sirve de señal de vida.
            message = status_message(None if controller_down else self._last_snap, now,
                                     self.cfg.poll.mains_present_v)
            if reading:
                reading["ts"] = iso(reading["ts"])
                message["reading"] = reading
            self.outbox.push(message)

        self.flush_outbox(now)

    def flush_outbox(self, now: float) -> None:
        if now < self._next_send:
            return
        while True:
            batch = self.outbox.peek(self.cfg.odoo.batch_size)
            if not batch:
                break
            ids = [row_id for row_id, _ in batch]
            try:
                ok = self.uplink.send([msg for _, msg in batch])
            except RejectedError as exc:
                log.error("Odoo rechazó el lote, se descarta: %s", exc)
                self.outbox.delete(ids)
                continue
            if not ok:
                self._send_failures += 1
                delay = min(MAX_BACKOFF_S, 5 * 2 ** min(self._send_failures, 6))
                self._next_send = now + delay
                log.info("%s mensajes en cola; reintento en %ss", len(self.outbox), delay)
                return
            self._send_failures = 0
            self.outbox.delete(ids)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("colector iniciado para %s", self.cfg.generator_code)
        interval = self.cfg.poll.interval_s
        while self.running:
            started = self.clock()
            try:
                self.tick()
            except Exception:
                log.exception("error inesperado en el ciclo")
            self.sleep(max(0.0, interval - (self.clock() - started)))
        log.info("colector detenido")
