"""Testes da regra grava-em-UTC / mostra-em-Brasilia."""

from __future__ import annotations

from datetime import datetime, timezone

from pricewatcher.tempo import agora_local, agora_utc, formata, fuso, para_local

# 11/09/2026 21:00 UTC = 18:00 em Brasilia (UTC-3)
UTC_REF = "2026-09-11T21:00:00+00:00"


def test_fuso_padrao_e_brasilia(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert fuso().key == "America/Sao_Paulo"


def test_tz_invalido_cai_no_padrao(monkeypatch):
    """TZ errado no ambiente nao pode derrubar a coleta."""
    monkeypatch.setenv("TZ", "Nao/Existe")
    assert fuso().key == "America/Sao_Paulo"


def test_converte_utc_para_brasilia(monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    local = para_local(UTC_REF)
    assert local.hour == 18
    assert local.utcoffset().total_seconds() == -3 * 3600


def test_carimbo_sem_fuso_e_tratado_como_utc(monkeypatch):
    """O banco grava ISO com offset, mas base antiga pode vir sem."""
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    assert para_local("2026-09-11T21:00:00").hour == 18


def test_formata_em_pt_br(monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    assert formata(UTC_REF) == "11/09 18:00"


def test_formata_aceita_vazio():
    assert formata(None) == "—"
    assert formata("isso nao e data") == "—"


def test_grava_em_utc_mostra_em_local():
    """O mesmo instante, dois relogios: e esse o contrato do modulo."""
    utc, local = agora_utc(), agora_local()
    assert utc.tzinfo is timezone.utc
    assert local.tzinfo is not None
    assert abs((utc - local).total_seconds()) < 5  # mesmo instante


def test_digest_usa_horario_de_brasilia(monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    from pricewatcher.alerts import Alerta
    from pricewatcher.notify import render

    a = Alerta(
        produto_id=1, loja="kabum", titulo="Placa RX 9070 XT", url="https://x",
        category="gpu", model_key="RX_9070_XT", brand=None, is_bundle=False,
        bundle_note=None, preco=500000, voltou_ao_estoque=True,
    )
    texto = render.digest([a], datetime.fromisoformat(UTC_REF).astimezone(fuso()))
    assert "11/09 18:00" in texto
