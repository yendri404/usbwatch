"""Uso:
    python -m pp_collector run   [-c config.toml]   servicio continuo
    python -m pp_collector probe [-c config.toml]   lee el DSE una vez y muestra los valores
    python -m pp_collector raw PAGE [OFFSET COUNT]  vuelca registros crudos de una página
    python -m pp_collector scan                     busca baudrate/ID del DSE
    python -m pp_collector ping-odoo                envía un mensaje de prueba a Odoo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import config as config_mod
from .dse import DseController, MinimalModbusReader, ModbusError, addr
from .outbox import Outbox
from .service import Service, iso, status_message
from .uplink import OdooUplink

DEFAULT_CONFIG = "/etc/pp-collector/config.toml"
SCAN_BAUDRATES = (115200, 57600, 38400, 19200, 9600)
SCAN_SLAVE_IDS = (10, 1)


def make_reader(cfg, baudrate=None, slave_id=None):
    m = cfg.modbus
    return MinimalModbusReader(m.port, slave_id or m.slave_id, baudrate or m.baudrate,
                               m.parity, m.stopbits, m.timeout_s)


def make_uplink(cfg):
    o = cfg.odoo
    return OdooUplink(o.url, o.token, cfg.generator_code, o.verify_tls, o.timeout_s)


def cmd_run(cfg, _args):
    service = Service(cfg, DseController(make_reader(cfg)),
                      Outbox(cfg.storage.outbox_path, cfg.storage.max_outbox_rows), make_uplink(cfg))
    service.run()


def cmd_probe(cfg, _args):
    snap = DseController(make_reader(cfg)).read_snapshot()
    print(json.dumps(status_message(snap, snap.ts, cfg.poll.mains_present_v), indent=2, ensure_ascii=False))


def cmd_raw(cfg, args):
    regs = make_reader(cfg).read(addr(args.page, args.offset), args.count)
    for i, value in enumerate(regs):
        print(f"p{args.page} +{args.offset + i:<3} ({addr(args.page, args.offset + i)}): {value:>5}  0x{value:04X}")


def cmd_scan(cfg, _args):
    for baud in SCAN_BAUDRATES:
        for slave in SCAN_SLAVE_IDS:
            reader = make_reader(cfg, baud, slave)
            try:
                regs = reader.read(addr(3, 0), 2)
                print(f"OK  baudrate={baud} slave_id={slave}  fabricante={regs[0]} modelo={regs[1]}")
                return
            except ModbusError:
                print(f"--  baudrate={baud} slave_id={slave}")
            finally:
                reader.close()
    print("Sin respuesta: revisa cableado A/B, que Modbus esté activo en el DSE y la paridad.")
    sys.exit(1)


def cmd_ping_odoo(cfg, _args):
    message = status_message(None, time.time(), cfg.poll.mains_present_v)
    message["ts"] = iso(time.time())
    message["test"] = True
    ok = make_uplink(cfg).send([message])
    print("Odoo aceptó el mensaje" if ok else "Odoo no aceptó el mensaje (ver log arriba)")
    sys.exit(0 if ok else 1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pp_collector")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("probe").set_defaults(func=cmd_probe)
    raw = sub.add_parser("raw")
    raw.add_argument("page", type=int)
    raw.add_argument("offset", type=int, nargs="?", default=0)
    raw.add_argument("count", type=int, nargs="?", default=16)
    raw.set_defaults(func=cmd_raw)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    sub.add_parser("ping-odoo").set_defaults(func=cmd_ping_odoo)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(config_mod.load(args.config), args)


if __name__ == "__main__":
    main()
