"""Cola persistente en SQLite: nada se pierde si se cae internet o Odoo."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class Outbox:
    def __init__(self, path: str | Path, max_rows: int = 20000):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)"
        )
        self._db.commit()
        self.max_rows = max_rows

    def push(self, payload: dict) -> None:
        self._db.execute("INSERT INTO outbox (body) VALUES (?)", (json.dumps(payload, separators=(",", ":")),))
        # Si la cola crece demasiado (semanas sin internet) se descartan los más viejos.
        self._db.execute(
            "DELETE FROM outbox WHERE id <= (SELECT MAX(id) FROM outbox) - ?", (self.max_rows,)
        )
        self._db.commit()

    def peek(self, limit: int) -> list[tuple[int, dict]]:
        rows = self._db.execute("SELECT id, body FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [(row_id, json.loads(body)) for row_id, body in rows]

    def delete(self, ids: list[int]) -> None:
        self._db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
        self._db.commit()

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def close(self) -> None:
        self._db.close()
