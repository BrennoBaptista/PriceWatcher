"""Testes da carga de configuracao."""

from __future__ import annotations

import pytest

from pricewatcher.config import carrega

YAML = """
notify:
  destinations:
    - id: grupo
      chat_id: ${FALTA_ESSA}
stores:
  kabum: { enabled: true, categories: [gpu] }
targets:
  - id: X
    category: gpu
    label: X
    search_terms: ["x"]
    sanity_price_range_brl: [100, 200]
"""


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    monkeypatch.delenv("FALTA_ESSA", raising=False)
    p = tmp_path / "config.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


def test_estrito_falha_com_variavel_ausente(cfg_file):
    """Coletar e notificar exigem os segredos: o erro tem que aparecer no boot."""
    with pytest.raises(ValueError, match="FALTA_ESSA"):
        carrega(cfg_file, estrito=True)


def test_tolerante_segue_com_variavel_ausente(cfg_file):
    """--status e somente-leitura: nao deve exigir segredo do Telegram."""
    cfg = carrega(cfg_file, estrito=False)
    assert cfg.notify.destinations[0].chat_id == ""
    assert cfg.targets[0].id == "X"


def test_erro_lista_todas_as_faltantes(tmp_path, monkeypatch):
    for nome in ("UM", "DOIS"):
        monkeypatch.delenv(nome, raising=False)
    p = tmp_path / "c.yaml"
    p.write_text(
        "notify:\n  destinations:\n"
        "    - {id: a, chat_id: '${UM}'}\n"
        "    - {id: b, chat_id: '${DOIS}'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as e:
        carrega(p, estrito=True)
    assert "DOIS" in str(e.value) and "UM" in str(e.value)


def test_variavel_presente_e_substituida(cfg_file, monkeypatch):
    monkeypatch.setenv("FALTA_ESSA", "-100123")
    cfg = carrega(cfg_file, estrito=True)
    assert cfg.notify.destinations[0].chat_id == "-100123"


def test_config_inexistente_falha_claro(tmp_path):
    with pytest.raises(FileNotFoundError, match="config nao encontrado"):
        carrega(tmp_path / "nao_existe.yaml")


def test_config_de_producao_carrega(monkeypatch):
    """Guarda contra YAML invalido ou regex que nao compila no config real."""
    from pathlib import Path

    cfg = carrega(Path("config.yaml"), estrito=False)
    assert {t.id for t in cfg.targets} >= {"RX_9070_XT", "RTX_5070_TI", "PS5"}
    assert "casaevideo" in cfg.stores_for("console")
    assert "americanas" in cfg.stores_for("console")
    assert "kabum" in cfg.stores_for("gpu")
    # Lojas bloqueadas pelo Akamai ficam desabilitadas (secao 18 da SPEC).
    for bloqueada in ("casasbahia", "ponto", "magalu"):
        assert not cfg.stores[bloqueada].enabled
