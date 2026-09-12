"""Testes da linha de comando.

O primeiro existe por um bug real: `--marcar-teste` foi declarada no argparse e
nunca lida. Ficou documentada na ajuda, e nao fazia nada. Nenhum teste pegaria
isso, porque a flag simplesmente nao participava de caminho nenhum.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pricewatcher import __main__ as cli

FONTE = Path(cli.__file__).read_text(encoding="utf-8")

# add_argument("--nome-da-flag", ...) -> dest "nome_da_flag"
_FLAGS = re.findall(r'add_argument\(\s*"--([a-z0-9-]+)"', FONTE)


def test_encontrou_as_flags():
    """Guarda o proprio guarda: se o regex parar de casar, o teste abaixo vira
    vacuo e ninguem nota."""
    assert len(_FLAGS) >= 8, f"so achei {_FLAGS}"


@pytest.mark.parametrize("flag", sorted(set(_FLAGS)))
def test_flag_declarada_e_lida_em_algum_lugar(flag):
    """Toda flag precisa ser consumida, nao apenas documentada na ajuda."""
    dest = flag.replace("-", "_")
    usos = len(re.findall(rf"args\.{dest}\b", FONTE))
    assert usos >= 1, (
        f"--{flag} aparece na ajuda mas nunca e lida como args.{dest}: "
        f"flag morta"
    )


def test_exige_um_modo():
    with pytest.raises(SystemExit):
        cli.main([])


def test_healthcheck_de_base_inexistente_passa(tmp_path):
    """Container recem-subido ainda nao coletou -- nao e doenca."""
    assert cli.main(["--healthcheck", "--db", str(tmp_path / "x.db")]) == 0


def test_status_sem_base_avisa_em_vez_de_estourar(tmp_path):
    codigo = cli.main([
        "--status",
        "--db", str(tmp_path / "nao_existe.db"),
        "--env", str(tmp_path / "sem.env"),
    ])
    assert codigo == 1  # sinaliza, mas nao levanta excecao


def test_serve_e_run_once_sao_mutuamente_exclusivos():
    with pytest.raises(SystemExit):
        cli.main(["--run-once", "--serve"])


# --------------------------------------------------------------- autoteste
def test_selftest_cobre_toda_loja_habilitada():
    """REGRESSAO: a primeira versao tinha uma lista fixa de URLs, e as duas
    lojas VTEX da Fase 5 ficaram fora dela -- habilitadas na config e ignoradas
    em silencio pelo comando que existe para validar o ambiente."""
    from pathlib import Path

    from pricewatcher.config import carrega
    from pricewatcher.selftest import _pares_para_sondar

    cfg = carrega(Path("config.yaml"), estrito=False)
    habilitadas = {n for n, s in cfg.stores.items() if s.enabled}
    sondadas = {loja for loja, _ in _pares_para_sondar(cfg)}
    assert sondadas == habilitadas, (
        f"habilitadas mas nao sondadas: {sorted(habilitadas - sondadas)}"
    )


def test_selftest_casa_loja_com_alvo_da_categoria_certa():
    from pathlib import Path

    from pricewatcher.config import carrega
    from pricewatcher.selftest import _pares_para_sondar

    cfg = carrega(Path("config.yaml"), estrito=False)
    for loja, alvo in _pares_para_sondar(cfg):
        assert loja in cfg.stores_for(alvo.category), (
            f"{loja} sondada com alvo {alvo.id} de categoria que ela nao atende"
        )
