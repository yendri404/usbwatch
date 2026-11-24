# USB monitor (usbwatch)

Detects when the controller is plugged into the Raspberry Pi over USB and reports:

| Event | Log message |
|---|---|
| Plugged in | `Conexion exitosa: /dev/ttyUSB0 [0403:6001] ...` |
| Every hour while plugged in | `Dispositivo conectado y funcionando: ... (conectado hace 2h 00m 00s)` |
| Unplugged | `Dispositivo desconectado: ... (estuvo conectado 2h 05m 11s)` (WARNING level) |
| Engine starts while mains is absent (optional, needs RS485) | `Planta encendida por corte electrico (modo: auto, red: 0.0 V, motor: 1500 rpm)` (WARNING level) |

## Outage detection (generator started by a power cut)

Rule: the engine goes **stopped -> running** while the controller measures **no mains**
(phase-neutral below `--mains-v`, default 90 V). It is deliberately *not* reported when:

- the engine starts with mains present (manual / test mode);
- the engine was already running when the script started (no baseline to compare);
- a read fails (the previous state is kept; the next good read decides);
- the controller does not measure mains at all (the **DSE6110** has no mains monitoring;
  only the 6120 does), because blaming an outage would be a guess.

**This needs live readings, which the DSE exposes over RS485/Modbus, not over its
USB-B port.** Enable it by pointing to the USB-RS485 adapter, with the same Slave ID and
baud rate configured in the DSE:

```bash
python3 -m pp_collector.usbwatch --vid xxxx --pid yyyy \
    --modbus-port /dev/ttyUSB0 --slave-id 10 --baudrate 19200
```

Extra dependency for this mode: `minimalmodbus` (already a dependency of `pp-collector`):
`pip install "minimalmodbus>=2.1,<3"`. Do **not** run this at the same time as the
`pp-collector` service on the same RS485 port: two masters on one bus collide. If you
want both, the clean next step is to feed the watcher from the collector's own
`Snapshot` instead of opening a second connection.

## What "connected" means (read this)

The DSE6110/6120 MKIII datasheet only says the USB-B port is a **programming port**
(for DSE Configuration Suite). It gives no USB VID/PID and no read protocol over USB.
So this script detects the device through the **serial port the OS creates** when it
enumerates; "funcionando" in the hourly message means *still enumerated by the OS*,
not that the controller's data was read. Reading real data is what the RS485 / Modbus
collector (`pp_collector`) already does.

## Modules

| Module | Why | Install |
|---|---|---|
| `pyserial` >= 3.5 | Lists serial ports with their VID/PID | `apt` or `pip` (below) |
| `minimalmodbus` >= 2.1 | Only for outage detection (reads the DSE) | `pip install "minimalmodbus>=2.1,<3"` |
| Python >= 3.8 | `typing.Protocol` | Raspberry Pi OS Bullseye or newer ships 3.9+ |

Everything else is the standard library (`argparse`, `logging`, `signal`, `dataclasses`).
`pyserial` is already a dependency of `pp-collector`, so if you installed that, you are done.

### Install on the Raspberry Pi (pick one)

```bash
# A) System package (lightest on a Pi B+, no compilation)
sudo apt update && sudo apt install -y python3-serial

# B) Virtualenv (same one used by the collector)
python3 -m venv ~/usbwatch-venv
~/usbwatch-venv/bin/pip install "pyserial>=3.5"
```

Permission to open serial ports (only needed if you later read from the port):

```bash
sudo usermod -aG dialout $USER   # log out and back in
```

## Use

Run from `collector/`:

```bash
# 1. Plug the controller in and see what the OS calls it
python3 -m pp_collector.usbwatch --list
#    /dev/ttyACM0   xxxx:yyyy  <description>     <- note the VID:PID

# 2. Monitor, filtering on that device (otherwise ANY serial adapter matches)
python3 -m pp_collector.usbwatch --vid xxxx --pid yyyy
```

Options: `--port /dev/ttyUSB0`, `--poll 2` (seconds between checks), `--heartbeat 3600`, `-v`.
Stop with Ctrl+C or SIGTERM.

### Run at boot (optional, systemd)

`/etc/systemd/system/usbwatch.service`:

```ini
[Unit]
Description=USB monitor for the DSE controller
After=multi-user.target

[Service]
WorkingDirectory=/home/pi/eu_pp_monitoring/collector
ExecStart=/usr/bin/python3 -m pp_collector.usbwatch --vid xxxx --pid yyyy
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now usbwatch
journalctl -u usbwatch -f
```

## Design and how to add WebSockets

```
SerialPortDetector.find()  ->  UsbMonitor.step()  ->  Event  ->  [sink, sink, ...]
   (what is plugged in)        (state machine)        (data)       (where it goes)
```

- `detector.py`: swap it (e.g. for `pyudev`) by implementing `find() -> DeviceInfo | None`.
- `monitor.py`: connect / heartbeat / disconnect logic; one failing sink never stops the others.
- `sinks.py`: a sink is any object with `emit(event)`.

To add WebSockets, write one class and add it to the sinks list in `__main__.py`
(`Event.to_dict()` is already JSON-friendly). Sketch, using the `websockets` package
(`pip install websockets`) and a background thread so the polling loop is not blocked:

```python
import asyncio, json, threading
import websockets

class WebSocketSink:
    def __init__(self, url):
        self._loop = asyncio.new_event_loop()
        self._queue = asyncio.Queue()
        threading.Thread(target=self._run, args=(url,), daemon=True).start()

    def emit(self, event):                      # called by the monitor thread
        self._loop.call_soon_threadsafe(self._queue.put_nowait, json.dumps(event.to_dict()))

    def _run(self, url):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._send_forever(url))

    async def _send_forever(self, url):
        while True:                              # reconnect forever
            try:
                async with websockets.connect(url) as ws:
                    while True:
                        await ws.send(await self._queue.get())
            except Exception:
                await asyncio.sleep(5)
```

(This sketch is untested; it is the extension point, not shipped code.)

## Tests

```bash
pip install pytest && python -m pytest tests/test_usbwatch.py
```

They use a fake port list and a fake clock, so no hardware and no waiting an hour.
