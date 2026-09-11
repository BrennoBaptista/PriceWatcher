from __future__ import annotations

import gzip
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def carrega(nome: str) -> str:
    """Le uma fixture gzipada. Resposta real das lojas, congelada."""
    return gzip.decompress((FIXTURES / nome).read_bytes()).decode("utf-8", "replace")


class FetcherFalso:
    """Devolve sempre a mesma pagina. Os testes de parsing nao tocam a rede."""

    def __init__(self, corpo: str) -> None:
        self.corpo = corpo
        self.chamadas: list[str] = []

    def get(self, url: str) -> str:
        self.chamadas.append(url)
        return self.corpo


@pytest.fixture
def alvo_9070():
    from pricewatcher.models import Category, Target

    return Target(
        id="RX_9070_XT",
        category=Category.GPU,
        label="Radeon RX 9070 XT",
        search_terms=["rx 9070 xt"],
        match_regex=r"(?i)\b(rx\s*)?9070\s*xt\b",
        exclude=[r"(?i)^\s*pc\b", r"(?i)\b(pc\s*gamer|computador|notebook)\b"],
        sanity_price_range_brl=(2000, 15000),
    )
