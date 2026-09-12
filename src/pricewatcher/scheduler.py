"""Agendador interno.

O container e um servico de vida longa, nao um one-shot: o cron mora dentro do
processo. Isso evita depender do crontab do host e mantem o deploy como uma
unica unidade.

O jitter existe para nao bater no mesmo minuto exato todo dia -- e educado com
a loja e menos parecido com robo.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .models import ScheduleConfig
from .tempo import fuso  # noqa: F401 -- reexportado por conveniencia

log = logging.getLogger(__name__)


def gatilhos(cfg: ScheduleConfig) -> list[tuple[str, CronTrigger]]:
    """Os gatilhos, sem montar scheduler nenhum.

    Separado de `monta` para o `--status` conseguir ler os horarios sem
    instanciar um agendador -- que despejaria log de "Adding job tentatively"
    no meio do relatorio.
    """
    tz = fuso()
    jitter = max(0, cfg.jitter_minutes) * 60
    saida = []
    for horario in cfg.times:
        hora, _, minuto = horario.partition(":")
        saida.append((
            f"coleta {horario}",
            CronTrigger(
                hour=int(hora), minute=int(minuto or 0), timezone=tz, jitter=jitter
            ),
        ))
    return saida


def monta(cfg: ScheduleConfig, tarefa: Callable[[], None]) -> BlockingScheduler:
    tz = fuso()
    sched = BlockingScheduler(timezone=tz)

    for nome, gatilho in gatilhos(cfg):
        sched.add_job(
            tarefa,
            gatilho,
            id=nome.replace(" ", "-"),
            name=nome,
            max_instances=1,
            coalesce=True,          # acumulou por downtime? roda uma vez, nao N
            misfire_grace_time=3600,
        )
        log.info("agendado para %s (%s) com jitter de ate %d min",
                 nome, tz.key, cfg.jitter_minutes)
    return sched


def proximas_execucoes(cfg: ScheduleConfig) -> list[tuple[str, datetime | None]]:
    """Proxima execucao de cada horario, sem efeito colateral."""
    agora = datetime.now(fuso())
    return [
        (nome, gatilho.get_next_fire_time(None, agora)) for nome, gatilho in gatilhos(cfg)
    ]


def proximas(sched: BlockingScheduler) -> list[tuple[str, datetime | None]]:
    """Proxima execucao de cada job.

    Consultamos o *trigger*, nao `job.next_run_time`: antes de `start()` os jobs
    estao pendentes e nem tem esse atributo. Como queremos logar o agendamento
    ao subir, ler do trigger e o unico jeito que funciona nos dois estados.
    """
    agora = datetime.now(sched.timezone)
    return [
        (j.name, j.trigger.get_next_fire_time(None, agora)) for j in sched.get_jobs()
    ]
