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


# ------------------------------------------------------- acessorios reais
# Todos estes passaram por console na primeira versao do filtro, porque o
# require_any aceitava a palavra "console" -- e acessorio se anuncia como
# "para console PS5". Revisao manual dos 88 titulos coletados pegou.
ACESSORIOS_REAIS = [
    "Mochila de console de jogos lzhywei compatível com Sony PS5/PS5 Pro",
    "Chave Torx T8 30mm c/ Furo p/ abrir Console Ps3 Ps4 Ps5 Xbox",
    "Kit 4 Chaves Torx T8 30mm c/ Furo abre Console Ps4 Ps5 Xbox",
    "Cabo Flat Power 6 vias Botão Liga Desliga On Off Console PS5",
    "Carregador Base Dock Station Para Console Play 5 Ps5",
    "Tampa Do Console Playstation 5 Slim Chroma Pearl Sony - Ps5",
    "Face Plates Cover Skins Shell Panels para console PS5, roxo",
    "Ventilador de resfriamento Sanpyl para console PS5 Slim com luz LED",
    "Base de resfriamento de ventilador multifuncional compative console PS5",
    "Reprodutor Remoto PlayStation Portal Para Console PS5",
    "PlayStation Portal - Reprodutor Remoto para Console PS5 Branco",
    "Estojo de transporte para Playstation Portal Remote Player, console PS5",
    "Saco de armazenamento para ps5 console de jogos",
    "Adaptador de câmera para psvr/ps5, cabo conversor ps vr",
    "Bolsa de console para PS5 com compartimentos",
]


@pytest.mark.parametrize("titulo", ACESSORIOS_REAIS)
def test_acessorio_real_nao_vira_console(ps5_real, titulo):
    # Preco DENTRO da faixa de sanidade de proposito: se o acessorio cair por
    # ser barato, o teste nao prova nada sobre o filtro de titulo. Acessorio
    # anunciado a preco de console e justamente o caso perigoso.
    r = normaliza([oferta(titulo, 400000)], ps5_real)
    assert r.ofertas == [], f"acessorio aceito como console: {titulo!r}"
    assert r.descartes[Descarte.NAO_CASA] + r.descartes[Descarte.EXCLUIDO] == 1


# Consoles de verdade, coletados junto com os acessorios acima.
CONSOLES_REAIS = [
    "Console PlayStation 5 1TB Sony",
    "Console PlayStation 5 825GB Sony Standard",
    "Console Playstation 5 - PS5",
    "Novo Console Playstation PS5",
    "Playstation 5 Sony, 825GB, 1 Controle Sem Fio, Standard com Disco",
    "Console PS5 Slim Digital 1TB - Sony",
    "Console Playstation 5 Pro Sony Ssd 2Tb Com Controle Branco",
    "Console Video Game Ps5 1tb Slim Midia Fisica",
]


@pytest.mark.parametrize("titulo", CONSOLES_REAIS)
def test_console_real_e_aceito(ps5_real, titulo):
    r = normaliza([oferta(titulo, 400000)], ps5_real)
    assert len(r.ofertas) == 1, f"console legitimo rejeitado: {titulo!r}"


@pytest.mark.parametrize(
    "titulo,bundle",
    [
        # Jogo listado sem "com", sem "+" e sem a palavra bundle.
        ("Console Playstation 5 God Of War Ragnarok 825GB Sony", True),
        ("Console PlayStation PS5 Slim Disk Astro Bot e Gran Turismo 7 1TB", True),
        ("Console sony playstation 5 PS5 digital 2 Jogos ssd 1TB", True),
        ("Console PlayStation 5 Slim Disk com 2 Jogos", True),
        ("Console Playstation 5 Ps5 Standard 2 Controles Dualsense", True),
        # Um controle e o conteudo padrao da caixa, nao combo.
        ("Console PS5 Slim Físico 1TB com Controle DualSense Branco Sony", False),
        ("Playstation 5 Sony, 825GB, 1 Controle Sem Fio, Standard com Disco", False),
        ("Console PlayStation 5 825GB Sony Standard", False),
    ],
)
def test_bundle_em_titulo_real(ps5_real, titulo, bundle):
    r = normaliza([oferta(titulo, 400000)], ps5_real)
    assert len(r.ofertas) == 1
    assert r.ofertas[0].is_bundle is bundle


# Acessorios que a lista de exclusao NAO nomeia. Estes so caem porque o
# require_any exige que o titulo comece com "console" ou declare capacidade --
# acessorio se anuncia como "para console PS5", no meio da frase.
#
# A distincao importa: a lista negra cobre o que ja vimos, o require_any cobre
# o que ainda nao vimos. Sem este teste, ninguem saberia que o segundo faz
# diferenca, porque os acessorios reais coletados caem pelos dois criterios.
ACESSORIOS_NAO_CATALOGADOS = [
    "Luminária decorativa formato console PS5 3D",
    "Camiseta estampa console PS5 tamanho G",
    "Miniatura colecionável do console PS5 em resina",
    "Etiqueta identificadora para console PS5",
]


@pytest.mark.parametrize("titulo", ACESSORIOS_NAO_CATALOGADOS)
def test_acessorio_fora_da_lista_negra_cai_pelo_sinal_positivo(ps5_real, titulo):
    r = normaliza([oferta(titulo, 400000)], ps5_real)
    assert r.ofertas == [], f"aceito como console: {titulo!r}"
    assert r.descartes[Descarte.NAO_CASA] == 1, (
        "deveria cair por falta de sinal positivo, nao pela lista de exclusao"
    )
