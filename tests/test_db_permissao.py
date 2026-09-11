"""O erro de banco inacessivel precisa explicar a causa provavel.

'unable to open database file' e a mensagem do SQLite para praticamente
qualquer problema de caminho ou permissao. No container a causa e quase sempre
a mesma -- bind mount criado como root, processo rodando como nao-root -- e o
primeiro deploy no servidor derrubou um traceback cru na tela.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pricewatcher.db import PermissaoDoBanco, Repo


def test_diretorio_inacessivel_explica_a_causa(tmp_path, monkeypatch):
    def recusa(*_a, **_k):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", recusa)
    with pytest.raises(PermissaoDoBanco) as e:
        Repo(tmp_path / "sub" / "prices.db")

    msg = str(e.value)
    assert "prices.db" in msg
    assert "chown" in msg or "nao existe" in msg


def test_mensagem_cita_o_uid_do_processo(tmp_path):
    from pricewatcher.db import _explica_falha

    msg = str(_explica_falha(tmp_path / "x.db", OSError("qualquer")))
    assert "10001" in msg          # o uid do container, para o chown
    assert str(tmp_path) in msg    # e o diretorio de verdade


def test_caminho_valido_continua_funcionando(tmp_path):
    with Repo(tmp_path / "ok.db") as repo:
        assert repo.contagens()["product"] == 0
