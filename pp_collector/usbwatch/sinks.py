"""Where events go.

A sink is any object with ``emit(event)``. To add WebSocket support later,
write a ``WebSocketSink`` with that one method and pass it in the sinks list
(see USB_MONITOR.md); nothing else in the package changes.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .events import CONNECTED, DISCONNECTED, GENSET_STARTED_BY_OUTAGE, HEARTBEAT, Event

log = logging.getLogger("usbwatch")


class Sink(Protocol):
    def emit(self, event: Event) -> None: ...


class LogSink:
    """Writes events to the log (stdout, or the journal when run as a service)."""

    _LEVELS = {
        CONNECTED: logging.INFO,
        HEARTBEAT: logging.INFO,
        DISCONNECTED: logging.WARNING,
        GENSET_STARTED_BY_OUTAGE: logging.WARNING,
    }

    def emit(self, event: Event) -> None:
        log.log(self._LEVELS.get(event.kind, logging.INFO), event.message)
