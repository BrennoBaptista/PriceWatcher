"""Fan-out de destinos.

Regra que justifica este modulo existir: **falha em um destino nao pode calar os
outros**. Se o grupo recusar a mensagem, o privado ainda tem que receber o aviso
operacional -- que e justamente como voce descobriria o problema.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..alerts import Alerta
from ..models import Destination, NotifyConfig
from . import render

log = logging.getLogger(__name__)

PRECO = "price"
OPERACIONAL = "operational"


def _aceita(dest: Destination, kind: str, categorias: set[str]) -> bool:
    if dest.kinds and kind not in dest.kinds:
        return False
    if kind == PRECO and dest.categories:
        permitidas = {str(c) for c in dest.categories}
        if not (categorias & permitidas):
            return False
    return True


class Router:
    def __init__(self, cfg: NotifyConfig, transporte) -> None:
        self._destinos = cfg.destinations
        self._transporte = transporte

    def envia_precos(
        self,
        alertas: list[Alerta],
        quando: datetime | None = None,
        teste: bool = False,
    ) -> dict[str, bool]:
        if not alertas:
            return {}
        entregues: dict[str, bool] = {}
        for dest in self._destinos:
            desejados = [
                a for a in alertas
                if _aceita(dest, PRECO, {a.category})
            ]
            if not desejados:
                continue
            texto = render.digest(desejados, quando, teste=teste)
            entregues[dest.id] = self._entrega(dest, texto)
        return entregues

    def envia_operacional(
        self, falhas: list[tuple[str, str, str]], vazios: list[str]
    ) -> dict[str, bool]:
        if not falhas and not vazios:
            return {}
        texto = render.operacional(falhas, vazios)
        entregues: dict[str, bool] = {}
        for dest in self._destinos:
            if not _aceita(dest, OPERACIONAL, set()):
                continue
            entregues[dest.id] = self._entrega(dest, texto)
        return entregues

    def _entrega(self, dest: Destination, texto: str) -> bool:
        try:
            ok = self._transporte.envia(dest.chat_id, texto)
        except Exception:  # noqa: BLE001 -- isolamento por destino e o ponto
            log.exception("destino %r falhou", dest.id)
            return False
        if ok:
            log.info("destino %r: mensagem entregue", dest.id)
        else:
            log.error("destino %r: transporte recusou a mensagem", dest.id)
        return ok
