"""Testes do adapter VTEX, offline, contra resposta real da API de catalogo.

A fixture foi escolhida para cobrir os quatro estados que importam:
1P/marketplace x disponivel/esgotado x com PIX/sem PIX.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from pricewatcher.models import Category, SellerType, Target
from pricewatcher.normalize import Descarte, normaliza
from pricewatcher.stores.platforms.vtex import VtexAdapter, _preco_avista

FIXTURE = Path(__file__).parent / "fixtures" / "vtex_ps5.json.gz"


class FetcherFalso:
    def __init__(self, corpo: str) -> None:
        self.corpo = corpo
        self.urls: list[str] = []

    def get(self, url: str) -> str:
        self.urls.append(url)
        return self.corpo


@pytest.fixture
def corpo() -> str:
    return gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")


@pytest.fixture
def ps5() -> Target:
    return Target(
        id="PS5",
        category=Category.CONSOLE,
        label="PlayStation 5",
        search_terms=["playstation 5"],
        include_bundles=True,
        require_all=[r"(?i)\b(playstation\s*5|ps5)\b"],
        require_any=[r"(?i)\bconsole\b", r"(?i)\b[12]\s*tb\b", r"(?i)\b825\s*gb\b"],
        exclude=[r"(?i)\b(capa|skin|suporte|headset|cart[ãa]o|psn)\b"],
        variants=[
            {"key": "PS5_PRO", "match_regex": r"(?i)\bpro\b"},
            {"key": "PS5_SLIM_DIGITAL", "match_regex": r"(?i)\bdigital\b"},
            {"key": "PS5_SLIM_DISC", "default": True},
        ],
        sanity_price_range_brl=(2000, 9000),
    )


def _adapter() -> VtexAdapter:
    return VtexAdapter("americanas", "https://www.americanas.com.br")


# ------------------------------------------------------------------ extracao
def test_monta_url_da_api_publica(corpo, ps5):
    f = FetcherFalso(corpo)
    _adapter().fetch(ps5, f)
    assert "/api/catalog_system/pub/products/search" in f.urls[0]
    assert "ft=playstation%205" in f.urls[0]
    assert "_from=0&_to=49" in f.urls[0]


def test_extrai_campos_essenciais(corpo, ps5):
    ofertas = _adapter().fetch(ps5, FetcherFalso(corpo))
    assert ofertas
    for o in ofertas:
        assert o.store == "americanas"
        assert o.store_sku
        assert o.url.startswith("http")
        assert o.title_raw


def test_fixture_traz_vendas_da_propria_loja(corpo, ps5):
    tipos = {o.seller_type for o in _adapter().fetch(ps5, FetcherFalso(corpo))}
    assert SellerType.FIRST_PARTY in tipos


@pytest.mark.parametrize(
    "sellers,esperado,nome",
    [
        ([{"sellerId": "1", "sellerName": "AMERICANAS SA", "commertialOffer": {}}],
         SellerType.FIRST_PARTY, "AMERICANAS SA"),
        ([{"sellerId": "CV021", "sellerName": "WEBCONTINENTAL", "commertialOffer": {}}],
         SellerType.MARKETPLACE, "WEBCONTINENTAL"),
        # Item vendido pelos dois: a propria loja tem que vencer, senao
        # descartariamos uma oferta legitima pela regra 1P.
        ([{"sellerId": "CV021", "sellerName": "WEBCONTINENTAL", "commertialOffer": {}},
          {"sellerId": "1", "sellerName": "AMERICANAS SA", "commertialOffer": {}}],
         SellerType.FIRST_PARTY, "AMERICANAS SA"),
    ],
)
def test_escolha_de_vendedor(sellers, esperado, nome):
    produto = {"productName": "Console PlayStation 5 Slim 1TB", "link": "https://x/p"}
    item = {"itemId": "42", "sellers": sellers}
    oferta = _adapter()._do_item(produto, item)
    assert oferta.seller_type is esperado
    assert oferta.seller_name == nome


def test_fixture_cobre_disponivel_e_esgotado(corpo, ps5):
    ofertas = _adapter().fetch(ps5, FetcherFalso(corpo))
    estados = {o.available for o in ofertas}
    assert estados == {True, False}, "fixture perdeu um dos estados de estoque"


# --------------------------------------------------------------- preco PIX
def test_preco_avista_usa_pix_e_nao_o_preco_cheio():
    """REGRESSAO: `Price` e o preco cheio. Na Casa e Video o PIX sai 17% abaixo."""
    oferta = {
        "Price": 669.16,
        "Installments": [
            {"PaymentSystemName": "Visa", "NumberOfInstallments": 1,
             "TotalValuePlusInterestRate": 669.16},
            {"PaymentSystemName": "Pix", "NumberOfInstallments": 1,
             "TotalValuePlusInterestRate": 555.40},
            {"PaymentSystemName": "Visa", "NumberOfInstallments": 12,
             "TotalValuePlusInterestRate": 800.00},
        ],
    }
    assert _preco_avista(oferta) == 55540


def test_boleto_e_a_segunda_opcao_de_avista():
    oferta = {
        "Price": 100.0,
        "Installments": [
            {"PaymentSystemName": "Visa", "NumberOfInstallments": 1,
             "TotalValuePlusInterestRate": 100.0},
            {"PaymentSystemName": "Boleto Bancário", "NumberOfInstallments": 1,
             "TotalValuePlusInterestRate": 95.0},
        ],
    }
    assert _preco_avista(oferta) == 9500


def test_item_esgotado_nao_tem_preco():
    """A VTEX nao envia Installments para item sem estoque. None e o correto."""
    assert _preco_avista({"Price": 0, "Installments": []}) is None
    assert _preco_avista({}) is None


# --------------------------------------------------- integracao com o filtro
def test_console_esgotado_e_registrado_e_nao_descartado(corpo, ps5):
    """REGRESSAO: descartar item sem preco quebrava o alerta de volta ao estoque.

    Sem a observacao "indisponivel" gravada, nunca existe a transicao
    indisponivel -> disponivel, e o gatilho nunca dispara.
    """
    ofertas = _adapter().fetch(ps5, FetcherFalso(corpo))
    res = normaliza(ofertas, ps5)

    esgotados = [o for o in res.ofertas if not o.raw.available]
    assert esgotados, "console esgotado sumiu da base"
    assert any(o.raw.price_cash is None for o in esgotados)
    assert res.descartes[Descarte.SEM_PRECO] == 0


def test_item_a_venda_sem_preco_continua_sendo_descartado(ps5):
    """Esse caso e parser quebrado de verdade, e precisa continuar caindo fora."""
    from pricewatcher.models import RawOffer

    quebrado = RawOffer(
        store="americanas", store_sku="1", url="https://x",
        title_raw="Console PlayStation 5 Slim 1TB",
        price_cash=None, available=True, seller_type=SellerType.FIRST_PARTY,
    )
    res = normaliza([quebrado], ps5)
    assert res.ofertas == []
    assert res.descartes[Descarte.SEM_PRECO] == 1


def test_variantes_e_bundles_saem_classificados(corpo, ps5):
    res = normaliza(_adapter().fetch(ps5, FetcherFalso(corpo)), ps5)
    assert res.ofertas, "nenhum console reconhecido na fixture"
    assert all(o.model_key.startswith("PS5_") for o in res.ofertas)
    assert all(o.category is Category.CONSOLE for o in res.ofertas)


def test_resposta_que_nao_e_lista_falha_alto(ps5):
    with pytest.raises(RuntimeError, match="esperava lista"):
        _adapter().fetch(ps5, FetcherFalso('{"erro": "bloqueado"}'))


def test_resposta_que_nao_e_json_falha_alto(ps5):
    with pytest.raises(RuntimeError, match="nao e JSON"):
        _adapter().fetch(ps5, FetcherFalso("<html>Access Denied</html>"))
