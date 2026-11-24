"""CLI: ``python -m pp_collector.usbwatch [--list] [--vid 0403 --pid 6001] ...``"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .detector import SerialPortDetector, list_devices
from .monitor import UsbMonitor
from .powerwatch import ModbusStatusSource, PowerOutageWatcher
from .sinks import LogSink


def _hex(value: str) -> int:
    return int(value, 16)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="usbwatch", description="Monitor the USB connection of the controller")
    ap.add_argument("--list", action="store_true", help="list serial ports (with VID:PID) and exit")
    ap.add_argument("--port", help="exact port, e.g. /dev/ttyUSB0")
    ap.add_argument("--vid", type=_hex, help="USB vendor id in hex, e.g. 0403")
    ap.add_argument("--pid", type=_hex, help="USB product id in hex, e.g. 6001")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between checks (default 2)")
    ap.add_argument("--heartbeat", type=float, default=3600.0, help="seconds between heartbeats (default 3600)")
    ap.add_argument("-v", "--verbose", action="store_true")
    grp = ap.add_argument_group("outage detection (reads the DSE over RS485/Modbus; optional)")
    grp.add_argument("--modbus-port", help="enables outage detection, e.g. /dev/ttyUSB0 (the USB-RS485 adapter)")
    grp.add_argument("--slave-id", type=int, default=10)
    grp.add_argument("--baudrate", type=int, default=19200)
    grp.add_argument("--mains-v", type=float, default=90.0, help="min phase-neutral volts to count as mains present")
    grp.add_argument("--status-poll", type=float, default=5.0, help="seconds between controller reads (default 5)")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.list:
        devices = list_devices()
        if not devices:
            print("No serial ports found.")
        for d in devices:
            ids = f"{d.vid:04x}:{d.pid:04x}" if d.vid is not None and d.pid is not None else "----:----"
            print(f"{d.port:<14} {ids}  {d.description}")
        return 0

    if args.port is None and args.vid is None and args.pid is None:
        logging.getLogger("usbwatch").warning(
            "Sin filtro (--port/--vid/--pid): cualquier adaptador serie contara como el dispositivo"
        )

    sinks = [LogSink()]  # add more sinks here (e.g. a WebSocketSink)
    watchers = []
    if args.modbus_port:
        watchers.append(PowerOutageWatcher(
            ModbusStatusSource(args.modbus_port, args.slave_id, args.baudrate),
            sinks, mains_present_v=args.mains_v, interval_s=args.status_poll,
        ))

    monitor = UsbMonitor(
        SerialPortDetector(port=args.port, vid=args.vid, pid=args.pid),
        sinks,
        poll_interval_s=args.poll,
        heartbeat_interval_s=args.heartbeat,
        watchers=watchers,
    )
    signal.signal(signal.SIGINT, monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)
    monitor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
