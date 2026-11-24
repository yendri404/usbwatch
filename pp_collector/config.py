from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModbusConfig:
    port: str = "/dev/ttyUSB0"
    slave_id: int = 10
    baudrate: int = 19200
    parity: str = "N"
    stopbits: int = 1
    timeout_s: float = 1.0


@dataclass(frozen=True)
class PollConfig:
    interval_s: float = 5.0
    report_interval_s: float = 60.0
    mains_present_v: float = 90.0


@dataclass(frozen=True)
class OdooConfig:
    url: str
    token: str
    verify_tls: bool = True
    timeout_s: float = 15.0
    batch_size: int = 20


@dataclass(frozen=True)
class StorageConfig:
    outbox_path: str = "/var/lib/pp-collector/outbox.sqlite3"
    max_outbox_rows: int = 20000


@dataclass(frozen=True)
class Config:
    generator_code: str
    modbus: ModbusConfig
    poll: PollConfig
    odoo: OdooConfig
    storage: StorageConfig


def load(path: str | Path) -> Config:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    parity = raw.get("modbus", {}).get("parity", "N").upper()
    if parity not in ("N", "E", "O"):
        raise ValueError(f"paridad inválida: {parity}")
    return Config(
        generator_code=raw["device"]["generator_code"],
        modbus=ModbusConfig(**{**raw.get("modbus", {}), "parity": parity}),
        poll=PollConfig(**raw.get("poll", {})),
        odoo=OdooConfig(**raw["odoo"]),
        storage=StorageConfig(**raw.get("storage", {})),
    )
