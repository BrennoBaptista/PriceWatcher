"""Testes do motor de alertas.

Quase todo teste aqui verifica que o bot **fica calado**. Detectar queda e facil;
o valor do modulo esta em nao disparar errado.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pricewatcher.alerts import NOVO_MINIMO, VOLTA_ESTOQUE, avalia
from pricewatcher.db import Repo
from pricewatcher.models import AlertsConfig

MIL = 100  # centavos por real, para deixar os numeros legiveis


@pytest.fixture
def repo(tmp_path):
    with Repo(tmp_path / "t.db") as r:
        yield r


def semear(repo: Repo, observacoes: list[tuple[int | None, bool]], sku: str = "1") -> int:
    """Cria um produto e um historico com carimbos crescentes.

    `observacoes` e uma lista de (preco_em_reais, disponivel); a ultima e a
    coleta atual.
    """
    repo.con.execute(
        "INSERT INTO product (store, store_sku, url, title_raw, category, model_key,"
        " seller_type, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("kabum", sku, "https://x/1", "Placa de Vídeo RX 9070 XT Pulse", "gpu",
         "RX_9070_XT", "first_party", "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00"),
    )
    pid = repo.con.execute(
        "SELECT id FROM product WHERE store_sku=?", (sku,)
    ).fetchone()[0]
    run = repo.inicia_run("kabum", "RX_9070_XT")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, (preco, disp) in enumerate(observacoes):
        repo.con.execute(
            "INSERT INTO price_point (product_id, observed_at, price_cash, available,"
            " run_id) VALUES (?,?,?,?,?)",
            (pid, (base + timedelta(hours=i)).isoformat(),
             None if preco is None else preco * MIL, int(disp), run),
        )
    repo.commit()
    return pid


CFG = AlertsConfig()


# ------------------------------------------------------------- novo minimo
def test_dispara_novo_minimo(repo):
    pid = semear(repo, [(5000, True), (4900, True), (4850, True), (4700, True)])
    alertas = avalia(repo, {pid}, CFG)
    assert len(alertas) == 1
    a = alertas[0]
    assert a.novo_minimo is True
    assert a.melhor_anterior == 4850 * MIL
    assert a.queda == 150 * MIL


def test_nao_dispara_sem_aquecimento(repo):
    """Produto novo seria 'minimo historico' na primeira vez -- inutil."""
    pid = semear(repo, [(5000, True), (4000, True)])
    assert avalia(repo, {pid}, CFG) == []


def test_nao_dispara_por_queda_pequena_em_reais(repo):
    """R$ 60 de queda nao justifica notificacao (limiar: R$ 100)."""
    pid = semear(repo, [(5000, True), (4950, True), (4900, True), (4840, True)])
    assert avalia(repo, {pid}, CFG) == []


def test_nao_dispara_por_queda_pequena_em_percentual(repo):
    """R$ 150 numa placa de R$ 20 mil e 0,75% -- abaixo do limiar de 1%."""
    pid = semear(repo, [(20000, True), (20000, True), (20000, True), (19850, True)])
    assert avalia(repo, {pid}, CFG) == []


def test_ignora_queda_implausivel(repo):
    """Queda de 90% e parser quebrado, nao promocao."""
    pid = semear(repo, [(5000, True), (5000, True), (5000, True), (500, True)])
    assert avalia(repo, {pid}, CFG) == []


def test_compara_com_o_minimo_historico_nao_com_o_ultimo(repo):
    """Preco sobe e volta: nao e minimo novo, mesmo caindo desde ontem."""
    pid = semear(repo, [(4500, True), (5000, True), (5200, True), (4800, True)])
    assert avalia(repo, {pid}, CFG) == []


def test_produto_indisponivel_nao_gera_minimo(repo):
    pid = semear(repo, [(5000, True), (4900, True), (4800, True), (4000, False)])
    assert avalia(repo, {pid}, CFG) == []


def test_cooldown_bloqueia_segundo_alerta(repo):
    pid = semear(repo, [(5000, True), (4900, True), (4850, True), (4700, True)])
    assert len(avalia(repo, {pid}, CFG)) == 1

    repo.registra_alerta(pid, NOVO_MINIMO, 4700 * MIL, 4850 * MIL, True)
    assert avalia(repo, {pid}, CFG) == []


def test_cooldown_expirado_permite_novo_alerta(repo):
    pid = semear(repo, [(5000, True), (4900, True), (4850, True), (4700, True)])
    antigo = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    repo.con.execute(
        "INSERT INTO alert (product_id, kind, price_cash, sent_at, delivered)"
        " VALUES (?,?,?,?,1)",
        (pid, NOVO_MINIMO, 4700 * MIL, antigo),
    )
    repo.commit()
    assert len(avalia(repo, {pid}, CFG)) == 1


# ----------------------------------------------------------- volta ao estoque
def test_dispara_volta_ao_estoque(repo):
    pid = semear(repo, [(5000, True), (None, False), (5000, True)])
    alertas = avalia(repo, {pid}, CFG)
    assert len(alertas) == 1
    assert alertas[0].voltou_ao_estoque is True
    assert alertas[0].novo_minimo is False


def test_nao_repete_enquanto_continua_em_estoque(repo):
    pid = semear(repo, [(None, False), (5000, True), (5000, True)])
    assert avalia(repo, {pid}, CFG) == []


def test_alerta_combinado_quando_volta_mais_barato(repo):
    """Voltou ao estoque E bateu minimo: um alerta so, com os dois motivos."""
    pid = semear(
        repo, [(5000, True), (4900, True), (4850, True), (None, False), (4600, True)]
    )
    alertas = avalia(repo, {pid}, CFG)
    assert len(alertas) == 1
    assert set(alertas[0].kinds) == {NOVO_MINIMO, VOLTA_ESTOQUE}


# -------------------------------------------------------------- operacional
def test_falhas_consecutivas_detectadas(repo):
    from pricewatcher.models import RunStatus

    for _ in range(2):
        rid = repo.inicia_run("pichau", "RX_9070_XT")
        repo.encerra_run(rid, RunStatus.FAILED, erro="BlocoDeAcesso: 403")
    ruins = repo.falhas_consecutivas(minimo=2)
    assert ("pichau", "RX_9070_XT", "BlocoDeAcesso: 403") in ruins


def test_falha_isolada_nao_alarma(repo):
    from pricewatcher.models import RunStatus

    rid = repo.inicia_run("pichau", "RX_9070_XT")
    repo.encerra_run(rid, RunStatus.FAILED, erro="erro")
    rid = repo.inicia_run("pichau", "RX_9070_XT")
    repo.encerra_run(rid, RunStatus.OK, 10, 5)
    assert repo.falhas_consecutivas(minimo=2) == []
