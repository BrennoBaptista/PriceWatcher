"""Interface comum dos adapters de loja.

Cada loja e isolada: uma excecao aqui nunca pode derrubar a coleta das outras.
Quem garante esse isolamento e o orquestrador (collector.py), nao o adapter.
"""

from __future__ import annotations

import re
from typing import Protocol

from ..http import Fetcher
from ..models import RawOffer, Target


def para_centavos(valor: str | float | int | None) -> int | None:
    """Converte preco para centavos. Aceita 5399.9, '5399.90' e 'R$ 5.399,90'."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return int(round(float(valor) * 100))

    texto = valor.strip()
    if not texto:
        return None
    texto = texto.replace("R$", "").replace("\xa0", " ").strip()
    # Formato brasileiro: ponto e milhar, virgula e decimal.
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return int(round(float(texto) * 100))
    except ValueError:
        return None


_PARCELAS = re.compile(r"(\d+)\s*x")


def para_parcelas(valor: str | int | None) -> int | None:
    """Numero de parcelas.

    A Kabum devolve isso como texto ('10x de R$ 694,11'), nao como inteiro --
    descoberto quando o pydantic recusou a string na primeira coleta real.
    """
    if valor is None:
        return None
    if isinstance(valor, int):
        return valor
    m = _PARCELAS.search(str(valor))
    return int(m.group(1)) if m else None


class StoreAdapter(Protocol):
    name: str

    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]:
        """Devolve todas as ofertas brutas da busca, sem filtrar nada.

        Filtro e responsabilidade do normalizador -- o adapter so traduz o
        formato da loja para RawOffer.
        """
        ...
