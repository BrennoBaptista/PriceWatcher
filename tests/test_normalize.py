"""Testes do normalizador -- quem decide o que entra na base."""

from __future__ import annotations

import pytest

from pricewatcher.models import Category, RawOffer, SellerType, Target
from pricewatcher.normalize import Descarte, normaliza


def oferta(titulo: str, preco: int = 500000, **kw) -> RawOffer:
    base = dict(
        store="loja",
        store_sku="1",
        url="https://exemplo/1",
        title_raw=titulo,
        price_cash=preco,
        available=True,
        seller_type=SellerType.FIRST_PARTY,
    )
    base.update(kw)
    return RawOffer(**base)


@pytest.fixture
def gpu():
    return Target(
        id="RX_9070_XT",
        category=Category.GPU,
        label="RX 9070 XT",
        search_terms=["rx 9070 xt"],
        match_regex=r"(?i)\b(rx\s*)?9070\s*xt\b",
        exclude=[r"(?i)^\s*pc\b", r"(?i)\b(pc\s*gamer|computador|notebook)\b"],
        sanity_price_range_brl=(2000, 15000),
    )


def test_aceita_placa_valida(gpu):
    r = normaliza([oferta("Placa de Vídeo ASRock RX 9070 XT Challenger, 16GB")], gpu)
    assert len(r.ofertas) == 1
    o = r.ofertas[0]
    assert o.model_key == "RX_9070_XT"
    assert o.brand == "ASRock"
    assert o.is_bundle is False


def test_descarta_produto_que_so_menciona_o_modelo(gpu):
    """O controle remoto FBG-9070 apareceu de verdade na busca da Kabum."""
    r = normaliza(
        [
            oferta("Controle Remoto Wlw Mbtech Ar Gree Janela Fbg-9070 Y512", 2699),
            oferta("Processador AMD Ryzen 7 5700X", 119999),
            oferta("Placa de Vídeo RX 9070 Challenger 16GB"),  # 9070 liso, sem XT
        ],
        gpu,
    )
    assert r.ofertas == []
    assert r.descartes[Descarte.NAO_CASA] == 3


def test_descarta_pc_montado(gpu):
    r = normaliza(
        [
            oferta("PC IA EXPERT Intel Ultra 7 / RX 9070 XT 16GB", 2104357),
            oferta("PC Gamer com RX 9070 XT"),
        ],
        gpu,
    )
    assert r.ofertas == []
    assert r.descartes[Descarte.EXCLUIDO] == 2


def test_faixa_de_sanidade_barra_preco_absurdo(gpu):
    r = normaliza(
        [
            oferta("Placa de Vídeo XFX RX 9070 XT Swift", 100),        # barato demais
            oferta("Placa de Vídeo XFX RX 9070 XT Swift", 9_999_900),  # caro demais
        ],
        gpu,
    )
    assert r.ofertas == []
    assert r.descartes[Descarte.FORA_DA_FAIXA] == 2


def test_marketplace_e_vendedor_desconhecido_sao_descartados(gpu):
    r = normaliza(
        [
            oferta("Placa de Vídeo RX 9070 XT A", seller_type=SellerType.MARKETPLACE),
            oferta("Placa de Vídeo RX 9070 XT B", seller_type=SellerType.UNKNOWN),
        ],
        gpu,
    )
    assert r.ofertas == []
    assert r.descartes[Descarte.NAO_1P] == 1
    # Falha fechada: indeterminado nunca entra.
    assert r.descartes[Descarte.VENDEDOR_DESCONHECIDO] == 1


def test_sem_preco_a_vista_nao_entra(gpu):
    r = normaliza([oferta("Placa de Vídeo RX 9070 XT", preco=None)], gpu)
    assert r.ofertas == []
    assert r.descartes[Descarte.SEM_PRECO] == 1


# ------------------------------------------------------------------ console
@pytest.fixture
def ps5():
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


@pytest.mark.parametrize(
    "titulo,variante",
    [
        ("Console PlayStation 5 Slim 1TB", "PS5_SLIM_DISC"),
        ("Console PlayStation 5 Slim Digital 1TB", "PS5_SLIM_DIGITAL"),
        # 'Pro' e testado antes de 'Digital' -- a ordem das regras importa.
        ("Console PlayStation 5 Pro Digital 2TB", "PS5_PRO"),
    ],
)
def test_classifica_variante_na_ordem_certa(ps5, titulo, variante):
    r = normaliza([oferta(titulo, 350000)], ps5)
    assert len(r.ofertas) == 1
    assert r.ofertas[0].model_key == variante


def test_bundle_e_marcado_e_nao_confundido_com_console_avulso(ps5):
    r = normaliza(
        [
            oferta("Console PlayStation 5 Slim Digital 1TB", 349900),
            oferta("Console PlayStation 5 Slim 1TB + EA Sports FC 26", 399900),
        ],
        ps5,
    )
    assert len(r.ofertas) == 2
    avulso, bundle = r.ofertas
    assert avulso.is_bundle is False
    assert bundle.is_bundle is True
    assert bundle.bundle_note and "EA Sports" in bundle.bundle_note


def test_exclusao_de_acessorio_nao_derruba_bundle_legitimo(ps5):
    """A lista negra nao contem 'controle' nem 'jogo' de proposito."""
    r = normaliza(
        [
            oferta("Capa Skin Adesiva para Console PS5 1TB", 9900),
            oferta("Console PlayStation 5 Slim 1TB + 2º Controle DualSense", 429900),
        ],
        ps5,
    )
    assert len(r.ofertas) == 1
    assert r.ofertas[0].is_bundle is True
    assert r.descartes[Descarte.EXCLUIDO] == 1


def test_acessorio_sem_sinal_de_console_e_descartado(ps5):
    r = normaliza([oferta("Controle DualSense para PlayStation 5", 35000)], ps5)
    assert r.ofertas == []
    assert r.descartes[Descarte.NAO_CASA] == 1
