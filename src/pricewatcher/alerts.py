"""Motor de alertas: novo minimo historico e volta ao estoque.

Este modulo existe para **nao** avisar. Detectar queda de preco e trivial; o
trabalho real esta nos guardrails que impedem alerta falso, porque um bot que
grita errado duas vezes vira um bot que voce ignora.

Guardrails (secao 7 da SPEC):

* aquecimento -- exige 3 observacoes anteriores, senao todo produto novo seria
  "minimo historico" na primeira vez que o vemos;
* delta minimo -- a queda precisa ser >= 1% **e** >= R$ 100;
* sanidade -- queda maior que 50% quase sempre e parser quebrado, nao promocao;
* cooldown -- no maximo um alerta por produto por janela.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import Repo
from .models import AlertsConfig, Target

log = logging.getLogger(__name__)

NOVO_MINIMO = "new_low"
VOLTA_ESTOQUE = "back_in_stock"

# Abaixo disso a "queda" e quase certamente erro de extracao.
FATOR_SANIDADE = 0.5


@dataclass
class Alerta:
    produto_id: int
    loja: str
    titulo: str
    url: str
    category: str
    model_key: str
    brand: str | None
    is_bundle: bool
    bundle_note: str | None

    preco: int

    vendedor: str | None = None
    """Quem vende de fato. Igual a loja nas fontes diretas; numa fonte agregada
    e a loja real por tras do comparador."""

    melhor_anterior: int | None = None
    novo_minimo: bool = False
    voltou_ao_estoque: bool = False

    @property
    def kinds(self) -> list[str]:
        k = []
        if self.novo_minimo:
            k.append(NOVO_MINIMO)
        if self.voltou_ao_estoque:
            k.append(VOLTA_ESTOQUE)
        return k

    @property
    def queda(self) -> int | None:
        if self.melhor_anterior is None:
            return None
        return self.melhor_anterior - self.preco

    @property
    def queda_percentual(self) -> float | None:
        if not self.melhor_anterior:
            return None
        return (self.melhor_anterior - self.preco) / self.melhor_anterior * 100


def _recente(carimbo: str | None, horas: int) -> bool:
    if not carimbo:
        return False
    try:
        quando = datetime.fromisoformat(carimbo)
    except ValueError:
        return False
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - quando < timedelta(hours=horas)


def _limiares(cfg: AlertsConfig, target: Target | None) -> tuple[int, float, int, int]:
    """Defaults globais, com override por alvo se houver (secao 7)."""
    regra = cfg.new_low
    minimo_obs, pct, brl, cooldown = (
        regra.min_observations, regra.min_drop_percent,
        regra.min_drop_brl, regra.cooldown_hours,
    )
    if target and (ov := target.alerts.get("new_low")):
        minimo_obs = ov.min_observations if ov.min_observations is not None else minimo_obs
        pct = ov.min_drop_percent if ov.min_drop_percent is not None else pct
        brl = ov.min_drop_brl if ov.min_drop_brl is not None else brl
        cooldown = ov.cooldown_hours if ov.cooldown_hours is not None else cooldown
    return minimo_obs, pct, brl, cooldown


def avalia(
    repo: Repo,
    produtos: set[int],
    cfg: AlertsConfig,
    alvos: dict[str, Target] | None = None,
) -> list[Alerta]:
    alvos = alvos or {}
    achados: list[Alerta] = []

    for produto_id in sorted(produtos):
        linha = repo.produto(produto_id)
        if linha is None:
            continue
        historico = repo.historico(produto_id)
        if len(historico) < 2:
            continue

        atual, anteriores = historico[-1], historico[:-1]
        target = alvos.get(linha["model_key"])

        alerta = Alerta(
            produto_id=produto_id,
            loja=linha["store"],
            titulo=linha["title_raw"],
            url=linha["url"],
            vendedor=linha["seller_name"],
            category=linha["category"],
            model_key=linha["model_key"],
            brand=linha["brand"],
            is_bundle=bool(linha["is_bundle"]),
            bundle_note=linha["bundle_note"],
            preco=atual["price_cash"] or 0,
        )

        if _avalia_minimo(repo, cfg, target, alerta, atual, anteriores):
            alerta.novo_minimo = True
        if _avalia_estoque(repo, cfg, alerta, atual, anteriores):
            alerta.voltou_ao_estoque = True

        if alerta.kinds:
            achados.append(alerta)

    # Melhor oferta primeiro.
    achados.sort(key=lambda a: (a.category, a.model_key, a.preco))
    return achados


def _avalia_minimo(repo, cfg, target, alerta, atual, anteriores) -> bool:
    regra = cfg.new_low
    if not regra.enabled:
        return False
    if not atual["available"] or not atual["price_cash"]:
        return False

    minimo_obs, pct_min, brl_min, cooldown = _limiares(cfg, target)

    if len(anteriores) < minimo_obs:
        return False

    precos_validos = [
        p["price_cash"] for p in anteriores if p["available"] and p["price_cash"]
    ]
    if not precos_validos:
        return False

    melhor = min(precos_validos)
    preco = atual["price_cash"]
    if preco >= melhor:
        return False

    # Queda absurda e parser quebrado, nao promocao.
    if preco < melhor * FATOR_SANIDADE:
        log.warning(
            "[%s] queda de %.0f%% em %r ignorada por implausibilidade "
            "(R$ %.2f -> R$ %.2f) -- suspeita de parsing quebrado",
            alerta.loja, (melhor - preco) / melhor * 100, alerta.titulo[:50],
            melhor / 100, preco / 100,
        )
        return False

    queda = melhor - preco
    if queda < brl_min * 100:
        return False
    if (queda / melhor * 100) < pct_min:
        return False

    if _recente(repo.ultimo_alerta(alerta.produto_id, NOVO_MINIMO), cooldown):
        log.debug("cooldown ativo para %s", alerta.produto_id)
        return False

    alerta.melhor_anterior = melhor
    return True


def _avalia_estoque(repo, cfg, alerta, atual, anteriores) -> bool:
    regra = cfg.back_in_stock
    if not regra.enabled:
        return False
    if not atual["available"]:
        return False
    if anteriores[-1]["available"]:
        return False
    if _recente(repo.ultimo_alerta(alerta.produto_id, VOLTA_ESTOQUE), regra.cooldown_hours):
        return False
    return True
