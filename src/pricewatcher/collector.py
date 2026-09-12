"""Orquestrador de coleta.

Responsabilidade central: **isolar falhas**. Uma loja que quebra nao pode
derrubar a coleta das outras, e nenhuma excecao pode sumir em silencio -- toda
falha vira `collection_run.status='failed'` com o erro persistido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .db import Repo
from .http import Fetcher
from .models import AppConfig, RunStatus, Target
from .normalize import normaliza
from .stores.base import StoreAdapter
from .stores.kabum import KabumAdapter
from .stores.pichau import PichauAdapter
from .stores.platforms.agregador import AgregadorAdapter
from .stores.platforms.vtex import VtexAdapter
from .stores.terabyte import TerabyteAdapter

log = logging.getLogger(__name__)

# Lojas com adapter proprio.
ADAPTERS: dict[str, type[StoreAdapter]] = {
    "kabum": KabumAdapter,
    "pichau": PichauAdapter,
    "terabyte": TerabyteAdapter,
}

# Lojas atendidas por um adapter de plataforma, parametrizado pela config.
PLATAFORMAS = {"vtex": VtexAdapter, "agregador": AgregadorAdapter}


def monta_adapter(loja: str, cfg: AppConfig) -> StoreAdapter | None:
    """Resolve a loja para um adapter, proprio ou de plataforma."""
    if loja in ADAPTERS:
        return ADAPTERS[loja]()

    ajustes = cfg.stores.get(loja)
    if not ajustes or ajustes.platform not in PLATAFORMAS:
        return None
    if not ajustes.base_url:
        log.error("loja %r usa plataforma %r mas nao tem base_url na config",
                  loja, ajustes.platform)
        return None

    if ajustes.platform == "agregador":
        if not ajustes.merchants:
            log.error(
                "agregador %r sem lista de lojas aceitas: coletaria tambem as "
                "que ja raspamos direto, duplicando alerta", loja,
            )
            return None
        return AgregadorAdapter(
            loja, ajustes.base_url, ajustes.merchants, ajustes.merchants_auditoria
        )
    return PLATAFORMAS[ajustes.platform](loja, ajustes.base_url)


@dataclass
class ResumoColeta:
    encontradas: int = 0
    mantidas: int = 0
    falhas: list[str] = field(default_factory=list)
    vazias: list[str] = field(default_factory=list)
    produtos: set[int] = field(default_factory=set)
    """Ids tocados nesta coleta -- e sobre eles que o motor de alertas roda."""

    @property
    def ok(self) -> bool:
        return not self.falhas and not self.vazias


def coleta(cfg: AppConfig, repo: Repo) -> ResumoColeta:
    resumo = ResumoColeta()
    with Fetcher(
        timeout=cfg.http.timeout_seconds,
        retries=cfg.http.retries,
        delay_min=cfg.http.delay_min_seconds,
        delay_max=cfg.http.delay_max_seconds,
    ) as fetcher:
        for target in cfg.targets:
            lojas = cfg.stores_for(target.category)
            if not lojas:
                log.info("alvo %s sem loja habilitada para %s", target.id, target.category)
                continue
            for loja in lojas:
                _coleta_uma(cfg, repo, fetcher, target, loja, resumo)
    return resumo


def _coleta_uma(
    cfg: AppConfig,
    repo: Repo,
    fetcher: Fetcher,
    target: Target,
    loja: str,
    resumo: ResumoColeta,
) -> None:
    adapter = monta_adapter(loja, cfg)
    if adapter is None:
        log.error("loja %r habilitada na config mas sem adapter", loja)
        resumo.falhas.append(f"{loja}/{target.id}: adapter inexistente")
        return

    run_id = repo.inicia_run(loja, target.id)
    try:
        brutas = adapter.fetch(target, fetcher)
    except Exception as e:  # noqa: BLE001 -- isolamento e o objetivo
        log.exception("[%s/%s] coleta falhou", loja, target.id)
        repo.encerra_run(run_id, RunStatus.FAILED, erro=f"{type(e).__name__}: {e}")
        resumo.falhas.append(f"{loja}/{target.id}: {type(e).__name__}: {e}")
        return

    res = normaliza(brutas, target)
    for oferta in res.ofertas:
        resumo.produtos.add(repo.registra(oferta, run_id))
    repo.commit()

    resumo.encontradas += len(brutas)
    resumo.mantidas += len(res.ofertas)

    # Zero validos com brutos na mao e o sintoma classico de parser quebrado.
    status = RunStatus.OK
    if brutas and not res.ofertas:
        status = RunStatus.PARTIAL
        resumo.vazias.append(f"{loja}/{target.id}")
        log.warning(
            "[%s/%s] %d brutos, 0 validos -- possivel parser quebrado. Descartes: %s",
            loja, target.id, len(brutas), dict(res.descartes),
        )
    repo.encerra_run(run_id, status, len(brutas), len(res.ofertas))

    log.info(
        "[%s/%s] %d brutos -> %d mantidos (descartes: %s)",
        loja, target.id, len(brutas), len(res.ofertas), dict(res.descartes),
    )
