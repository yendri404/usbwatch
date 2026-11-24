"""Agrupa las lecturas rápidas en un resumen por intervalo (promedio, mín., máx.)."""

from __future__ import annotations

from .dse import COUNTER_KEYS, Snapshot


class Aggregator:
    def __init__(self):
        self._reset()

    def _reset(self):
        self._sums: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._mins: dict[str, float] = {}
        self._maxs: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self.samples = 0
        self.started_at: float | None = None

    def add(self, snap: Snapshot) -> None:
        if self.started_at is None:
            self.started_at = snap.ts
        self.samples += 1
        for key, value in snap.values.items():
            if value is None:
                continue
            if key in COUNTER_KEYS:
                self._last[key] = value
                continue
            self._sums[key] = self._sums.get(key, 0.0) + value
            self._counts[key] = self._counts.get(key, 0) + 1
            self._mins[key] = min(value, self._mins.get(key, value))
            self._maxs[key] = max(value, self._maxs.get(key, value))

    def flush(self, ts: float) -> dict | None:
        """Devuelve el resumen del intervalo y reinicia; None si no hubo muestras."""
        if not self.samples:
            return None
        values = {k: round(self._sums[k] / self._counts[k], 3) for k in self._sums}
        values.update(self._last)
        summary = {
            "ts": ts,
            "period_s": round(ts - self.started_at, 1),
            "samples": self.samples,
            "values": values,
            "max_kw": self._maxs.get("gen_total_kw"),
            "min_battery_v": self._mins.get("battery_v"),
        }
        self._reset()
        return summary
