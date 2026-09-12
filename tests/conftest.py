from __future__ import annotations

import gzip
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
RAIZ_PROJETO = Path(__file__).resolve().parents[1]


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


def target_do_config(alvo_id: str):
    """Le o alvo do config.yaml de producao.

    Fixture com regras escritas a mao vira mentira: os testes de filtro
    passavam contra um require_any que a aplicacao ja nao usava mais. Lendo o
    arquivo real, teste e producao nao podem divergir em silencio.

    Nao usa `config.carrega` de proposito -- ela expande ${VARIAVEIS} de
    ambiente, e os alvos nao dependem de segredo nenhum.
    """
    import yaml
    from pricewatcher.models import Target

    bruto = yaml.safe_load((RAIZ_PROJETO / "config.yaml").read_text(encoding="utf-8"))
    for alvo in bruto["targets"]:
        if alvo["id"] == alvo_id:
            return Target.model_validate(alvo)
    raise KeyError(f"alvo {alvo_id!r} nao existe no config.yaml")


@pytest.fixture
def ps5_real():
    return target_do_config("PS5")


@pytest.fixture
def gpu_real():
    return target_do_config("RX_9070_XT")
