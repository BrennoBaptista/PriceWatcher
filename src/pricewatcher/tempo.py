"""Fuso unico da aplicacao.

Regra: **grava em UTC, mostra em Brasilia.**

O banco guarda ISO-8601 UTC porque comparacao de historico e calculo de cooldown
precisam de um relogio sem ambiguidade. Tudo que uma pessoa le -- log, mensagem
no Telegram, relatorio no terminal -- e convertido para `America/Sao_Paulo`.

Misturar os dois papeis e o que produz aquele bug chato de "o alerta diz 08:00
mas o log diz 11:00".
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PADRAO = "America/Sao_Paulo"


def fuso() -> ZoneInfo:
    """Fuso de exibicao. `TZ` sobrescreve, mas o padrao e Brasilia."""
    nome = os.environ.get("TZ") or PADRAO
    try:
        return ZoneInfo(nome)
    except Exception:  # noqa: BLE001 -- TZ invalido nao pode derrubar a coleta
        return ZoneInfo(PADRAO)


def agora_utc() -> datetime:
    """Para gravar."""
    return datetime.now(timezone.utc)


def agora_local() -> datetime:
    """Para mostrar."""
    return datetime.now(fuso())


def para_local(carimbo: str | datetime | None) -> datetime | None:
    """Converte um carimbo do banco (UTC) para Brasilia."""
    if carimbo is None:
        return None
    if isinstance(carimbo, str):
        try:
            carimbo = datetime.fromisoformat(carimbo)
        except ValueError:
            return None
    if carimbo.tzinfo is None:
        carimbo = carimbo.replace(tzinfo=timezone.utc)
    return carimbo.astimezone(fuso())


def formata(quando: str | datetime | None, fmt: str = "%d/%m %H:%M") -> str:
    local = para_local(quando)
    return local.strftime(fmt) if local else "—"
