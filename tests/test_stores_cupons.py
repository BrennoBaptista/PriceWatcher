"""Testes de parsing dos adapters de cupom, offline contra respostas reais
congeladas -- mesmo padrao de test_stores.py, arquivo separado porque a fonte
de dados (paginas/feeds de cupom) nao tem nada a ver com busca de produto.
"""

from __future__ import annotations

import pytest
from conftest import FetcherFalso, carrega

from pricewatcher.stores.kabum_cupons import KabumCouponAdapter
from pricewatcher.stores.pichau_cupons import PichauCouponAdapter
from pricewatcher.stores.terabyte_cupons import TerabyteCouponAdapter


# -------------------------------------------------------------------- kabum
def test_kabum_extrai_cupons_ativos():
    cupons = KabumCouponAdapter().fetch(
        FetcherFalso(carrega("kabum_cupons_planilha.json.gz"))
    )
    assert len(cupons) > 10
    assert all(c.store == "kabum" and c.code for c in cupons)
    # GAMEPLAY (R$ 150 OFF em Console selecionado) estava ativo na captura.
    assert any(c.code == "GAMEPLAY" for c in cupons)


def test_kabum_cupom_carrega_texto_legal():
    """O texto legal e o unico lugar onde varios cupons excluem categoria
    (ex.: "nao e valido para... Placas de Video...") -- sem capturar isso, o
    filtro de relevancia teria falso positivo (secao 19 da SPEC)."""
    cupons = KabumCouponAdapter().fetch(
        FetcherFalso(carrega("kabum_cupons_planilha.json.gz"))
    )
    tonokabum = next(c for c in cupons if c.code == "TONOKABUM")
    assert tonokabum.terms_text
    assert "Placas de V" in tonokabum.terms_text


def test_kabum_falha_alto_se_planilha_mudar_de_formato():
    with pytest.raises(RuntimeError, match="formato inesperado"):
        KabumCouponAdapter().fetch(FetcherFalso("nao e uma resposta de planilha"))


# ------------------------------------------------------------------- pichau
def test_pichau_extrai_cupons_ativos():
    cupons = PichauCouponAdapter().fetch(FetcherFalso(carrega("pichau_cupons.html.gz")))
    assert len(cupons) == 4
    assert all(c.store == "pichau" and c.code for c in cupons)
    assert {"SKRP10", "PRUMO10OFF", "PRUMO5OFF"} <= {c.code for c in cupons}
    # Pagina nao expoe termos por cupom -- ver docstring do adapter.
    assert all(c.terms_text is None for c in cupons)


def test_pichau_falha_alto_se_layout_mudar():
    with pytest.raises(RuntimeError, match="MuiCard-root"):
        PichauCouponAdapter().fetch(FetcherFalso("<html><body>nada</body></html>"))


# ----------------------------------------------------------------- terabyte
def test_terabyte_extrai_cupons_ativos():
    cupons = TerabyteCouponAdapter().fetch(
        FetcherFalso(carrega("terabyte_cupons.json.gz"))
    )
    assert len(cupons) > 5
    assert all(c.store == "terabyte" and c.code for c in cupons)


def test_terabyte_enriquece_escopo_com_departamento():
    """Varios cupons de "Modelo Selecionado" so dizem a categoria via
    `condition.product[].department` -- sem anexar isso ao scope_text, o
    filtro de relevancia nao teria como saber que e AM4/AM5 (nem, no caso de
    um cupom de GPU, que e Placa de Video)."""
    cupons = TerabyteCouponAdapter().fetch(
        FetcherFalso(carrega("terabyte_cupons.json.gz"))
    )
    prultracore = next(c for c in cupons if c.code == "PRULTRACORE")
    assert "INTEL" in prultracore.scope_text


def test_terabyte_ignora_cupom_expirado():
    """TERAHORSE (GPU) e um dos 22 cupons do feed mas ja tinha expirado no
    momento da captura -- confirma que o filtro de data exclui, nao so
    "aceita tudo que o feed devolve"."""
    cupons = TerabyteCouponAdapter().fetch(
        FetcherFalso(carrega("terabyte_cupons.json.gz"))
    )
    assert "TERAHORSE" not in {c.code for c in cupons}


def test_terabyte_falha_alto_se_feed_nao_for_json():
    with pytest.raises(RuntimeError, match="JSON valido"):
        TerabyteCouponAdapter().fetch(FetcherFalso("<html>nada</html>"))


def test_terabyte_falha_alto_se_feed_mudar_de_formato():
    with pytest.raises(RuntimeError, match="lista"):
        TerabyteCouponAdapter().fetch(FetcherFalso('{"nao": "e uma lista"}'))
