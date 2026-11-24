"""Envío de mensajes al addon pp_monitoring de Odoo."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

INGEST_PATH = "/pp_monitoring/api/v1/ingest"


class RejectedError(Exception):
    """Odoo rechazó el lote por contenido inválido: reintentarlo no sirve."""


class OdooUplink:
    def __init__(self, url: str, token: str, generator_code: str,
                 verify_tls: bool = True, timeout_s: float = 15.0):
        self.endpoint = url.rstrip("/") + INGEST_PATH
        self.generator_code = generator_code
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.verify = verify_tls
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "User-Agent": "pp-collector/1.0",
        })

    def send(self, messages: list[dict]) -> bool:
        """True si Odoo aceptó el lote. Lanza RejectedError ante un 400/413."""
        body = {"generator": self.generator_code, "messages": messages}
        try:
            resp = self._session.post(self.endpoint, json=body, timeout=self.timeout_s)
        except requests.RequestException as exc:
            log.warning("Odoo no disponible: %s", exc)
            return False
        if resp.ok:
            return True
        if resp.status_code in (400, 413):
            raise RejectedError(f"{resp.status_code}: {resp.text[:300]}")
        # 401/403 (token), 404 (addon no instalado), 5xx: se conserva y se reintenta.
        log.warning("Odoo respondió %s: %s", resp.status_code, resp.text[:300])
        return False
