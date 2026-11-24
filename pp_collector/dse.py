"""Lectura del controlador DSE (familia 61xx MKII/MKIII) por Modbus RTU.

Mapa de registros según DSE GenComm (057-004, v2.228):
dirección absoluta = página * 256 + offset (base 0). Los valores de 32 bits
van con la palabra más significativa en la dirección menor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)

# --- Páginas GenComm -------------------------------------------------------

PAGE_STATUS = 3
PAGE_BASIC = 4
PAGE_DERIVED = 6
PAGE_ACCUM = 7
PAGE_ALARMS = 154


def addr(page: int, offset: int) -> int:
    return page * 256 + offset


# Bloques que se leen en cada ciclo: (página, offset inicial, cantidad).
BLOCKS = (
    (PAGE_STATUS, 0, 8),
    (PAGE_BASIC, 0, 52),
    (PAGE_DERIVED, 0, 23),
    (PAGE_ACCUM, 6, 12),
)

# (clave, página, offset, tipo, escala). La clave es la misma que usa Odoo.
FIELDS = (
    ("oil_pressure_kpa", PAGE_BASIC, 0, "u16", 1),
    ("coolant_temp_c", PAGE_BASIC, 1, "s16", 1),
    ("fuel_level_pct", PAGE_BASIC, 3, "u16", 1),
    ("charge_alt_v", PAGE_BASIC, 4, "u16", 0.1),
    ("battery_v", PAGE_BASIC, 5, "u16", 0.1),
    ("engine_rpm", PAGE_BASIC, 6, "u16", 1),
    ("gen_freq_hz", PAGE_BASIC, 7, "u16", 0.1),
    ("gen_l1_n_v", PAGE_BASIC, 8, "u32", 0.1),
    ("gen_l2_n_v", PAGE_BASIC, 10, "u32", 0.1),
    ("gen_l3_n_v", PAGE_BASIC, 12, "u32", 0.1),
    ("gen_l1_l2_v", PAGE_BASIC, 14, "u32", 0.1),
    ("gen_l2_l3_v", PAGE_BASIC, 16, "u32", 0.1),
    ("gen_l3_l1_v", PAGE_BASIC, 18, "u32", 0.1),
    ("gen_l1_a", PAGE_BASIC, 20, "u32", 0.1),
    ("gen_l2_a", PAGE_BASIC, 22, "u32", 0.1),
    ("gen_l3_a", PAGE_BASIC, 24, "u32", 0.1),
    ("mains_freq_hz", PAGE_BASIC, 35, "u16", 0.1),
    ("mains_l1_n_v", PAGE_BASIC, 36, "u32", 0.1),
    ("mains_l2_n_v", PAGE_BASIC, 38, "u32", 0.1),
    ("mains_l3_n_v", PAGE_BASIC, 40, "u32", 0.1),
    ("mains_l1_l2_v", PAGE_BASIC, 42, "u32", 0.1),
    ("mains_l2_l3_v", PAGE_BASIC, 44, "u32", 0.1),
    ("mains_l3_l1_v", PAGE_BASIC, 46, "u32", 0.1),
    ("gen_total_kw", PAGE_DERIVED, 0, "s32", 0.001),
    ("gen_total_kva", PAGE_DERIVED, 8, "s32", 0.001),
    ("gen_total_kvar", PAGE_DERIVED, 16, "s32", 0.001),
    ("gen_pf", PAGE_DERIVED, 21, "s16", 0.01),
    ("gen_load_pct", PAGE_DERIVED, 22, "s16", 0.1),
    ("engine_hours", PAGE_ACCUM, 6, "u32", 1 / 3600),
    ("gen_kwh", PAGE_ACCUM, 8, "u32", 0.1),
    ("start_count", PAGE_ACCUM, 16, "u32", 1),
)

# Contadores: se reporta el último valor, no el promedio.
COUNTER_KEYS = frozenset({"engine_hours", "gen_kwh", "start_count"})

CONTROL_MODES = {
    0: "stop",
    1: "auto",
    2: "manual",
    3: "test_on_load",
    4: "auto_manual_restore",
    5: "user_config",
    6: "test_off_load",
    7: "off",
}

# Página 154, lista 61xx/72xx/73xx MKII: 4 alarmas por registro, nibble alto primero.
ALARM_NAMES = (
    "Emergency stop", "Low oil pressure", "High coolant temperature", "Low coolant temperature",
    "Under speed", "Over speed", "Generator under frequency", "Generator over frequency",
    "Generator low voltage", "Generator high voltage", "Battery low voltage", "Battery high voltage",
    "Charge alternator failure", "Fail to start", "Fail to stop", "Generator fail to close",
    "Mains fail to close", "Oil pressure sender fault", "Loss of magnetic pick up", "Magnetic pick up open circuit",
    "Generator high current", "Calibration lost", "Low fuel level", "CAN ECU warning",
    "CAN ECU shutdown", "CAN ECU data fail", "Low oil level switch", "High temperature switch",
    "Low fuel level switch", "Expansion unit watchdog alarm", "kW overload alarm", "Negative phase sequence current alarm",
    "Earth fault trip alarm", "Generator phase rotation alarm", "Auto voltage sense fail", "Maintenance alarm",
    "Loading frequency alarm", "Loading voltage alarm", "Fuel usage running", "Fuel usage stopped",
    "Protections disabled", "Protections blocked", "Generator short circuit", "Mains high current",
    "Mains earth fault", "Mains short circuit", "ECU protect", "ECU malfunction",
    "ECU information", "ECU shutdown", "ECU warning", "ECU electrical trip",
    "ECU after treatment", "ECU water in fuel", "Generator reverse power", "Generator positive VAr",
    "Generator negative VAr", "LCD heater low voltage", "LCD heater high voltage", "DEF level low",
    "SCR inducement", "MSC old version", "MSC ID alarm", "MSC failure",
    "MSC priority error", "Fuel sender open circuit", "Over speed runaway", "Over frequency runaway",
    "Coolant sensor open circuit", "Remote display link lost", "Fuel tank bund level", "Charge air temperature",
    "Fuel level high", "Generator breaker failed to open", "Mains breaker failed to open", "Fail to synchronise",
    "AVR data fail", "AVR DM1 red stop lamp", "Escape mode", "Coolant high temp electrical trip",
)

# Códigos de condición de 4 bits que significan alarma activa.
ALARM_SEVERITY = {
    2: "warning",
    3: "shutdown",
    4: "electrical_trip",
    5: "controlled_shutdown",
    10: "indication",
}

# Valores centinela GenComm: los 8 valores superiores de cada rango.
_SENTINEL_FLOOR = {"u16": 0xFFF8, "s16": 0x7FF8, "u32": 0xFFFFFFF8, "s32": 0x7FFFFFF8}


class ModbusError(Exception):
    """Fallo de comunicación con el controlador."""


class RegisterReader(Protocol):
    def read(self, address: int, count: int) -> list[int]: ...


def decode(regs: list[int], offset: int, kind: str, scale: float) -> float | None:
    """Decodifica un valor; devuelve None si es un centinela (no implementado, fallo de sensor...)."""
    if kind in ("u16", "s16"):
        raw = regs[offset]
    else:
        raw = (regs[offset] << 16) | regs[offset + 1]
    floor = _SENTINEL_FLOOR[kind]
    if floor <= raw < floor + 8:
        return None
    if kind == "s16" and raw >= 0x8000:
        raw -= 0x10000
    elif kind == "s32" and raw >= 0x80000000:
        raw -= 0x100000000
    return round(raw * scale, 4)


def decode_alarms(regs: list[int], count: int, names=ALARM_NAMES) -> list[dict]:
    """Devuelve las alarmas activas de la página 154 (regs empieza en el offset 1)."""
    active = []
    for index in range(count):
        reg, slot = divmod(index, 4)
        if reg >= len(regs):
            break
        code = (regs[reg] >> (12 - 4 * slot)) & 0xF
        severity = ALARM_SEVERITY.get(code)
        if severity is None:
            continue
        number = index + 1
        name = names[index] if index < len(names) else f"Alarm {number}"
        active.append({"code": f"dse_{number:03d}", "name": name, "severity": severity})
    return active


@dataclass
class Snapshot:
    ts: float
    values: dict[str, float | None]
    control_mode: str | None
    flags: dict[str, bool]
    alarms: list[dict] = field(default_factory=list)

    def is_running(self) -> bool:
        rpm = self.values.get("engine_rpm") or 0
        freq = self.values.get("gen_freq_hz") or 0
        return rpm > 100 or freq > 10

    def mains_available(self, min_voltage: float) -> bool:
        phases = [self.values.get(f"mains_l{n}_n_v") for n in (1, 2, 3)]
        phases = [v for v in phases if v is not None]
        return bool(phases) and min(phases) >= min_voltage


class DseController:
    def __init__(self, reader: RegisterReader, alarm_names=ALARM_NAMES):
        self.reader = reader
        self.alarm_names = alarm_names

    def read_page(self, page: int, offset: int, count: int) -> list[int]:
        return self.reader.read(addr(page, offset), count)

    def read_snapshot(self) -> Snapshot:
        pages: dict[int, tuple[int, list[int]]] = {}
        for page, start, count in BLOCKS:
            pages[page] = (start, self.read_page(page, start, count))

        values = {}
        for key, page, offset, kind, scale in FIELDS:
            start, regs = pages[page]
            values[key] = decode(regs, offset - start, kind, scale)
        if values["engine_hours"] is not None:
            values["engine_hours"] = round(values["engine_hours"], 2)

        status = pages[PAGE_STATUS][1]
        mode = status[4]
        flag_reg = status[6]
        flags = {
            # n/16 en GenComm es el bit n-1.
            "shutdown": bool(flag_reg & (1 << 12)),
            "electrical_trip": bool(flag_reg & (1 << 11)),
            "warning": bool(flag_reg & (1 << 10)),
            "controlled_shutdown": bool(flag_reg & (1 << 6)),
        }

        return Snapshot(
            ts=time.time(),
            values=values,
            control_mode=CONTROL_MODES.get(mode),
            flags=flags,
            alarms=self.read_alarms(),
        )

    def read_alarms(self) -> list[dict]:
        try:
            count = self.read_page(PAGE_ALARMS, 0, 1)[0]
        except ModbusError:
            # Las páginas anteriores respondieron: el firmware no expone la página 154.
            log.warning("el controlador no respondió la página de alarmas %s", PAGE_ALARMS)
            return []
        if count in (0, 0xFFFF):
            return []
        count = min(count, 256)
        regs = self.read_page(PAGE_ALARMS, 1, min(64, (count + 3) // 4))
        return decode_alarms(regs, count, self.alarm_names)


class MinimalModbusReader:
    """Lector RS485 real basado en minimalmodbus."""

    def __init__(self, port: str, slave_id: int, baudrate: int, parity: str = "N",
                 stopbits: int = 1, timeout_s: float = 1.0, retries: int = 2):
        import minimalmodbus
        import serial

        self._retries = retries
        inst = minimalmodbus.Instrument(port, slave_id, mode=minimalmodbus.MODE_RTU)
        inst.serial.baudrate = baudrate
        inst.serial.bytesize = 8
        inst.serial.parity = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}[parity]
        inst.serial.stopbits = stopbits
        inst.serial.timeout = timeout_s
        inst.clear_buffers_before_each_transaction = True
        inst.close_port_after_each_call = False
        self._inst = inst

    def read(self, address: int, count: int) -> list[int]:
        last_exc = None
        for _ in range(self._retries + 1):
            try:
                return self._inst.read_registers(address, count, functioncode=3)
            except (OSError, ValueError, IOError) as exc:  # minimalmodbus lanza subclases de éstas
                last_exc = exc
                time.sleep(0.2)
        raise ModbusError(f"lectura {address}+{count} falló: {last_exc}") from last_exc

    def close(self) -> None:
        self._inst.serial.close()
