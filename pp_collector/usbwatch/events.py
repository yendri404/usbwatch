"""Event model shared by the detector, the monitor and every sink."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

CONNECTED = "connected"
HEARTBEAT = "heartbeat"
DISCONNECTED = "disconnected"
GENSET_STARTED_BY_OUTAGE = "genset_started_by_outage"


@dataclass(frozen=True)
class DeviceInfo:
    """What the OS tells us about the USB device (all fields are best effort)."""

    port: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    description: str = ""
    serial_number: Optional[str] = None


@dataclass(frozen=True)
class Event:
    kind: str
    ts: str
    message: str
    device: Optional[DeviceInfo] = None
    uptime_s: Optional[int] = None
    data: Optional[dict] = None  # extra readings (e.g. mains voltage, rpm)

    def to_dict(self) -> dict:
        """JSON-friendly form. A future WebSocket sink can send this as-is."""
        return asdict(self)


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
