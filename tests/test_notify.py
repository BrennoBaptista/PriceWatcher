"""Testes de formatacao e roteamento. Nenhum toca a rede."""

from __future__ import annotations

from datetime import datetime

import pytest

from pricewatcher.alerts import Alerta
from pricewatcher.models import Destination, NotifyConfig
from pricewatcher.notify import render
from pricewatcher.notify.router import Router


@pytest.mark.parametrize(
    "centavos,esperado",
    [
        (519999, "R$ 5.199,99"),
        (100, "R$ 1,00"),
        (5, "R$ 0,05"),
        (1234567890, "R$ 12.345.678,90"),
        (None, "—"),
    ],
)
def test_formato_brl(centavos, esperado):
    assert render.brl(centavos) == esperado


def alerta(**kw) -> Alerta:
    base = dict(
        produto_id=1, loja="terabyte", titulo="Placa de Vídeo RX 9070 XT Pulse",
        url="https://x/1", category="gpu", model_key="RX_9070_XT", brand="Sapphire",
        is_bundle=False, bundle_note=None, preco=429900,
    )
    base.update(kw)
    return Alerta(**base)


def test_digest_traz_preco_anterior_e_percentual():
    a = alerta(novo_minimo=True, melhor_anterior=469000)
    texto = render.digest([a], datetime(2026, 9, 11, 8, 0))
    assert "1 oportunidade" in texto
    assert "NOVO MÍNIMO" in texto
    assert "R$ 4.299,00" in texto
    assert "R$ 4.690,00" in texto
    assert "-8,3%" in texto  # decimal com virgula, pt-BR
    assert "Terabyteshop" in texto


def test_digest_plural_e_um_unico_cabecalho():
    texto = render.digest([alerta(novo_minimo=True, melhor_anterior=469000),
                           alerta(voltou_ao_estoque=True)])
    assert "2 oportunidades" in texto
    assert texto.count("oportunidade") == 1


def test_bundle_aparece_na_mensagem():
    a = alerta(is_bundle=True, bundle_note="EA Sports FC 26", voltou_ao_estoque=True)
    texto = render.digest([a])
    assert "bundle" in texto.lower()
    assert "EA Sports FC 26" in texto


def test_titulo_com_html_e_escapado():
    a = alerta(titulo="Placa <b>RX 9070 XT</b> & cia", voltou_ao_estoque=True)
    texto = render.digest([a])
    assert "&lt;b&gt;" in texto and "&amp;" in texto


# ------------------------------------------------------------------- router
class TransporteFalso:
    def __init__(self, falhar: set[str] | None = None) -> None:
        self.enviados: list[tuple[str, str]] = []
        self.falhar = falhar or set()

    def envia(self, chat_id: str, texto: str) -> bool:
        self.enviados.append((chat_id, texto))
        return chat_id not in self.falhar


def _cfg() -> NotifyConfig:
    return NotifyConfig(
        destinations=[
            Destination(id="grupo", chat_id="-100", kinds=["price"]),
            Destination(id="brenno", chat_id="111", kinds=["operational"]),
        ]
    )


def test_preco_vai_so_para_o_grupo():
    t = TransporteFalso()
    Router(_cfg(), t).envia_precos([alerta(voltou_ao_estoque=True)])
    assert [c for c, _ in t.enviados] == ["-100"]


def test_operacional_vai_so_para_o_privado():
    t = TransporteFalso()
    Router(_cfg(), t).envia_operacional([("kabum", "RX_9070_XT", "erro")], [])
    assert [c for c, _ in t.enviados] == ["111"]


def test_um_digest_por_rodada_nao_um_por_alerta():
    t = TransporteFalso()
    Router(_cfg(), t).envia_precos([alerta(voltou_ao_estoque=True) for _ in range(6)])
    assert len(t.enviados) == 1


def test_falha_em_um_destino_nao_impede_os_outros():
    cfg = NotifyConfig(
        destinations=[
            Destination(id="grupo", chat_id="-100"),
            Destination(id="brenno", chat_id="111"),
        ]
    )
    t = TransporteFalso(falhar={"-100"})
    r = Router(cfg, t).envia_precos([alerta(voltou_ao_estoque=True)])
    assert r == {"grupo": False, "brenno": True}
    assert len(t.enviados) == 2


def test_excecao_no_transporte_nao_derruba_a_rodada():
    class Explode:
        def envia(self, *_):
            raise RuntimeError("boom")

    r = Router(_cfg(), Explode()).envia_precos([alerta(voltou_ao_estoque=True)])
    assert r == {"grupo": False}


def test_filtro_por_categoria_continua_disponivel():
    cfg = NotifyConfig(
        destinations=[Destination(id="so_console", chat_id="-1", categories=["console"])]
    )
    t = TransporteFalso()
    Router(cfg, t).envia_precos([alerta(voltou_ao_estoque=True)])  # category="gpu"
    assert t.enviados == []


# ------------------------------------------------- marca de execucao manual
def test_digest_sem_marca_por_padrao():
    texto = render.digest([alerta(voltou_ao_estoque=True)])
    assert "TESTE MANUAL" not in texto


def test_digest_marcado_avisa_que_nao_e_alerta_real():
    """O grupo tem outras pessoas: teste sem marca vira falso alarme."""
    texto = render.digest([alerta(voltou_ao_estoque=True)], teste=True)
    assert "TESTE MANUAL" in texto
    assert texto.index("TESTE MANUAL") < texto.index("oportunidade")


def test_origem_container_nao_marca(monkeypatch):
    from pricewatcher.__main__ import e_execucao_manual

    monkeypatch.setenv("PRICEWATCHER_ORIGEM", "container")
    assert e_execucao_manual() is False


def test_sem_variavel_de_origem_e_execucao_manual(monkeypatch):
    """Detectar em vez de depender de alguem lembrar da flag."""
    from pricewatcher.__main__ import e_execucao_manual

    monkeypatch.delenv("PRICEWATCHER_ORIGEM", raising=False)
    assert e_execucao_manual() is True


def test_router_repassa_a_marca():
    t = TransporteFalso()
    Router(_cfg(), t).envia_precos([alerta(voltou_ao_estoque=True)], teste=True)
    assert "TESTE MANUAL" in t.enviados[0][1]


def test_bundle_sem_nota_ainda_aparece_como_bundle():
    """'... com 2 Jogos' nao deixa texto sobrando para a nota.

    Sem a marca, o combo ficaria indistinguivel de um console avulso na
    mensagem -- e distinguir os dois e o motivo de rastrear bundle.
    """
    a = alerta(is_bundle=True, bundle_note=None, voltou_ao_estoque=True)
    texto = render.digest([a])
    assert "bundle" in texto.lower()
