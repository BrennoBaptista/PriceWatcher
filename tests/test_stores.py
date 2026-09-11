"""Testes de parsing, rodando offline contra respostas reais congeladas.

Os dois primeiros testes de cada bloco sao regressao de bug que ja aconteceu.
Nao remover sem entender o que eles protegem.
"""

from __future__ import annotations

import json
import re

import pytest
from conftest import FetcherFalso, carrega

from pricewatcher.models import SellerType
from pricewatcher.stores.base import para_centavos, para_parcelas
from pricewatcher.stores.kabum import KabumAdapter
from pricewatcher.stores.pichau import PichauAdapter, _extrai_products, _remonta_payload
from pricewatcher.stores.terabyte import TerabyteAdapter


# ---------------------------------------------------------------- conversao
@pytest.mark.parametrize(
    "entrada,esperado",
    [
        (5399.9, 539990),
        ("5399.90", 539990),
        ("R$ 5.399,90", 539990),
        ("R$ 1.199,99", 119999),
        (None, None),
        ("", None),
        ("grátis", None),
    ],
)
def test_para_centavos(entrada, esperado):
    assert para_centavos(entrada) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        # A Kabum devolve parcelas como texto -- quebrou a primeira coleta real.
        ("10x de R$ 694,11", 10),
        ("12x de R$ 529,41 sem juros", 12),
        (12, 12),
        (None, None),
        ("sem parcelamento", None),
    ],
)
def test_para_parcelas(entrada, esperado):
    assert para_parcelas(entrada) == esperado


# -------------------------------------------------------------------- kabum
def test_kabum_extrai_ofertas(alvo_9070):
    ofertas = KabumAdapter().fetch(alvo_9070, FetcherFalso(carrega("kabum_busca.html.gz")))
    assert len(ofertas) > 30
    assert all(o.store == "kabum" and o.store_sku for o in ofertas)
    assert any("9070 XT" in o.title_raw for o in ofertas)


def test_kabum_disponibilidade_vem_de_quantity_nao_de_available():
    """REGRESSAO: `available` vem true em 100% dos itens, inclusive esgotados.

    Se alguem trocar `quantity > 0` por `p['available']`, este teste quebra --
    e o alerta de volta ao estoque deixaria de funcionar em silencio.
    """
    html = carrega("kabum_busca.html.gz")
    bruto = json.loads(
        re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html, re.S,
        ).group(1)
    )
    itens = bruto["props"]["pageProps"]["data"]["catalogServer"]["data"]

    # A premissa do bug: a loja diz que tudo esta disponivel.
    assert all(p["available"] for p in itens), "fixture mudou: available nao e mais 100% true"

    ofertas = KabumAdapter().fetch(
        _alvo_permissivo(), FetcherFalso(html)
    )
    indisponiveis = [o for o in ofertas if not o.available]
    assert indisponiveis, "nenhum item indisponivel -- o adapter voltou a confiar em 'available'"
    assert len(indisponiveis) < len(ofertas), "nada disponivel -- sinal invertido?"


def test_kabum_identifica_marketplace():
    ofertas = KabumAdapter().fetch(
        _alvo_permissivo(), FetcherFalso(carrega("kabum_busca.html.gz"))
    )
    tipos = {o.seller_type for o in ofertas}
    assert SellerType.FIRST_PARTY in tipos
    assert SellerType.MARKETPLACE in tipos
    # Marketplace nunca preenche quantity, entao nunca aparece como disponivel.
    assert not any(
        o.available for o in ofertas if o.seller_type is SellerType.MARKETPLACE
    )


def test_kabum_falha_alto_se_next_data_sumir(alvo_9070):
    with pytest.raises(RuntimeError, match="__NEXT_DATA__"):
        KabumAdapter().fetch(alvo_9070, FetcherFalso("<html><body>nada</body></html>"))


# ------------------------------------------------------------------- pichau
def test_pichau_extrai_products_com_decoder_de_verdade():
    """REGRESSAO: contar chaves na mao descartava 34 dos 36 produtos.

    Descricoes contem HTML com '{' e '}' dentro de strings JSON, o que quebra
    qualquer contagem ingenua de profundidade.
    """
    blob = _remonta_payload(carrega("pichau_busca.html.gz"))
    produtos = _extrai_products(blob)
    itens = produtos["items"]
    assert len(itens) >= 30, f"so {len(itens)} itens -- voltou a contagem manual?"
    assert produtos["total_count"] > 0
    assert produtos["page_info"]["current_page"] == 1


def test_pichau_preco_avista_e_menor_que_o_cheio():
    ofertas = PichauAdapter().fetch(
        _alvo_permissivo(), FetcherFalso(carrega("pichau_busca.html.gz"))
    )
    assert ofertas
    com_ambos = [o for o in ofertas if o.price_cash and o.price_installment]
    assert com_ambos
    # avista (PIX) tem que ser menor que base_price; se inverter, pegamos o campo errado.
    assert all(o.price_cash < o.price_installment for o in com_ambos)


def test_pichau_falha_alto_se_payload_sumir(alvo_9070):
    with pytest.raises(RuntimeError, match="products"):
        PichauAdapter().fetch(alvo_9070, FetcherFalso("<html>vazio</html>"))


# ----------------------------------------------------------------- terabyte
def test_terabyte_extrai_ofertas_e_estoque(alvo_9070):
    ofertas = TerabyteAdapter().fetch(
        alvo_9070, FetcherFalso(carrega("terabyte_busca.html.gz"))
    )
    assert len(ofertas) > 100
    assert all(o.store_sku.isdigit() for o in ofertas)
    assert all(o.url.startswith("https://www.terabyteshop.com.br/produto/") for o in ofertas)

    disponiveis = [o for o in ofertas if o.available]
    # A fixture tem os dois estados -- e isso que torna o teste util.
    assert disponiveis, "nenhum disponivel"
    assert len(disponiveis) < len(ofertas), "nenhum esgotado -- sinal de estoque quebrou"


def test_terabyte_preserva_acentos(alvo_9070):
    ofertas = TerabyteAdapter().fetch(
        alvo_9070, FetcherFalso(carrega("terabyte_busca.html.gz"))
    )
    titulos = " ".join(o.title_raw for o in ofertas)
    assert "Vídeo" in titulos, "entidade HTML nao foi desescapada, ou encoding quebrou"
    assert "&ecirc;" not in titulos and "&aacute;" not in titulos


def test_terabyte_falha_alto_se_card_sumir(alvo_9070):
    with pytest.raises(RuntimeError, match="product-item"):
        TerabyteAdapter().fetch(alvo_9070, FetcherFalso("<html>vazio</html>"))


def _alvo_permissivo():
    """Alvo que aceita tudo -- para testar o adapter, nao o filtro."""
    from pricewatcher.models import Category, Target

    return Target(
        id="QUALQUER",
        category=Category.GPU,
        label="qualquer",
        search_terms=["x"],
        sanity_price_range_brl=(1, 999999),
    )
