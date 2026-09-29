"""Filtro de relevancia e alerta de cupom -- paralelo a normalize.py+alerts.py.

Cupom nao e por `Target` (busca por termo), e por loja inteira -- por isso e
um pipeline a parte, nao uma extensao do normalizador de oferta. O que os dois
mundos compartilham e a forma do filtro: `require_any`/`exclude` sobre texto,
igual a `Target`.

Achado real (pagina de cupons da Kabum, secao 19 da SPEC): o texto curto de um
cupom pode parecer relevante enquanto as letras miudas EXCLUEM a categoria
("nao e valido para... Placas de Video..."). E por isso que `relevancia()`
testa `require_any` contra descricao+desconto e `exclude` contra o texto legal.

`exclude` so roda quando `terms_text` existe -- **nao** cai para o mesmo
texto do `require_any` quando a loja nao expoe termos (Pichau/Terabyte). Caiu
nisso seria auto-cancelante: `exclude` usa a mesma lista de palavras-chave do
`require_any` (sao as duas faces da mesma categoria), entao testar contra o
`scope_text` que acabou de bater no `require_any` sempre bateria de novo no
`exclude`, zerando todo cupom relevante que a loja nao explica em letras
miudas.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .alerts import recente
from .db import Repo
from .models import Category, CouponsConfig, RawCoupon

log = logging.getLogger(__name__)


def relevancia(cupom: RawCoupon, cfg: CouponsConfig) -> list[Category]:
    texto_positivo = f"{cupom.scope_text} {cupom.discount_text}"

    achadas = []
    for categoria, regra in cfg.categories.items():
        if not regra.require_any:
            continue
        bate = any(re.search(p, texto_positivo) for p in regra.require_any)
        if not bate:
            continue
        if cupom.terms_text and any(
            re.search(p, cupom.terms_text) for p in regra.exclude
        ):
            continue
        achadas.append(categoria)
    return achadas


@dataclass
class AlertaCupom:
    coupon_id: int
    loja: str
    codigo: str
    discount_text: str
    scope_text: str
    url: str
    categorias: list[Category]


def avalia_cupons(
    repo: Repo, novos: list[int], cfg: CouponsConfig
) -> list[AlertaCupom]:
    """Cooldown por cupom -- cobre o caso raro de um codigo reaparecer depois
    de expirar, ja que `novos` so sinaliza a primeira vez que o `(store,
    code)` foi visto."""
    achados: list[AlertaCupom] = []
    for coupon_id in novos:
        linha = repo.cupom(coupon_id)
        if linha is None:
            continue
        if recente(repo.ultimo_alerta_cupom(coupon_id), cfg.cooldown_hours):
            log.debug("cooldown ativo para cupom %s", coupon_id)
            continue
        achados.append(
            AlertaCupom(
                coupon_id=coupon_id,
                loja=linha["store"],
                codigo=linha["code"],
                discount_text=linha["discount_text"],
                scope_text=linha["scope_text"],
                url=linha["url"],
                categorias=[Category(c) for c in linha["categories"].split(",") if c],
            )
        )
    return achados
