"""USB presence detection.

Detection is done by polling the serial-port list of the OS (pyserial). It is
plain Python, works the same on Linux/Windows and needs no root or extra
system libraries. Polling every ~2 s costs next to nothing, even on a
Raspberry Pi B+.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Protocol

from .events import DeviceInfo


class Detector(Protocol):
    def find(self) -> Optional[DeviceInfo]:
        """Return the device if it is currently plugged in, else None."""


def _system_ports() -> Iterable:
    # Imported lazily so the rest of the package (and its tests) work without pyserial.
    from serial.tools import list_ports

    return list_ports.comports()


class SerialPortDetector:
    """Looks for a serial port, optionally filtered by port name and/or USB VID/PID.

    With no filter at all, ANY serial adapter matches; use ``--list`` to find the
    VID/PID of the controller and then filter on it.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        vid: Optional[int] = None,
        pid: Optional[int] = None,
        lister: Callable[[], Iterable] = _system_ports,
    ):
        self.port = port
        self.vid = vid
        self.pid = pid
        self._lister = lister

    def _matches(self, p) -> bool:
        if self.port is not None and p.device != self.port:
            return False
        if self.vid is not None and p.vid != self.vid:
            return False
        if self.pid is not None and p.pid != self.pid:
            return False
        return True

    def find(self) -> Optional[DeviceInfo]:
        for p in sorted(self._lister(), key=lambda item: item.device):
            if self._matches(p):
                return DeviceInfo(
                    port=p.device,
                    vid=p.vid,
                    pid=p.pid,
                    description=p.description or "",
                    serial_number=p.serial_number,
                )
        return None


def list_devices(lister: Callable[[], Iterable] = _system_ports) -> list:
    """All serial ports the OS sees, to help choose the VID/PID filter."""
    return [
        DeviceInfo(p.device, p.vid, p.pid, p.description or "", p.serial_number)
        for p in sorted(lister(), key=lambda item: item.device)
    ]
