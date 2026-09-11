"""Testes do agendador e do healthcheck. Nenhum bloqueia nem toca a rede."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pricewatcher.db import Repo
from pricewatcher.models import RunStatus, ScheduleConfig
from pricewatcher.scheduler import monta, proximas
from pricewatcher.selftest import healthcheck


def _nada() -> None:
    pass


def test_agenda_um_job_por_horario():
    sched = monta(ScheduleConfig(times=["08:00", "20:00"]), _nada)
    assert len(sched.get_jobs()) == 2
    assert {j.name for j in sched.get_jobs()} == {"coleta 08:00", "coleta 20:00"}


def test_usa_o_fuso_configurado(monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    sched = monta(ScheduleConfig(times=["08:00"]), _nada)
    assert str(sched.timezone) == "America/Sao_Paulo"


def test_nao_acumula_execucoes_perdidas():
    """Se o servidor ficar horas fora, queremos UMA coleta ao voltar, nao N."""
    sched = monta(ScheduleConfig(times=["08:00"]), _nada)
    job = sched.get_jobs()[0]
    assert job.coalesce is True
    assert job.max_instances == 1


def test_proximas_execucoes_listadas():
    sched = monta(ScheduleConfig(times=["08:00", "20:00"]), _nada)
    nomes = [n for n, _ in proximas(sched)]
    assert len(nomes) == 2


def test_horario_sem_minutos_e_aceito():
    sched = monta(ScheduleConfig(times=["7"]), _nada)
    assert len(sched.get_jobs()) == 1


# --------------------------------------------------------------- healthcheck
@pytest.fixture
def base(tmp_path):
    return tmp_path / "h.db"


def test_healthcheck_container_novo_passa(base):
    """Recem-subido ainda nao coletou -- isso nao e doenca."""
    assert healthcheck(base) == 0


def test_healthcheck_coleta_recente_passa(base):
    with Repo(base) as repo:
        rid = repo.inicia_run("kabum", "RX_9070_XT")
        repo.encerra_run(rid, RunStatus.OK, 10, 5)
    assert healthcheck(base) == 0


def test_healthcheck_coleta_velha_falha(base):
    with Repo(base) as repo:
        rid = repo.inicia_run("kabum", "RX_9070_XT")
        repo.encerra_run(rid, RunStatus.OK, 10, 5)
        antigo = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        repo.con.execute("UPDATE collection_run SET finished_at=? WHERE id=?",
                         (antigo, rid))
        repo.commit()
    assert healthcheck(base, max_horas=24) == 1


def test_healthcheck_ignora_rodada_que_falhou(base):
    """Coletor quebrado nao pode fazer o container parecer saudavel."""
    with Repo(base) as repo:
        rid = repo.inicia_run("kabum", "RX_9070_XT")
        repo.encerra_run(rid, RunStatus.FAILED, erro="403")
        antigo = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        repo.con.execute("UPDATE collection_run SET finished_at=? WHERE id=?",
                         (antigo, rid))
        # e uma rodada boa, mas igualmente velha
        rid2 = repo.inicia_run("pichau", "RX_9070_XT")
        repo.encerra_run(rid2, RunStatus.OK, 10, 5)
        repo.con.execute("UPDATE collection_run SET finished_at=? WHERE id=?",
                         (antigo, rid2))
        repo.commit()
    assert healthcheck(base, max_horas=24) == 1
