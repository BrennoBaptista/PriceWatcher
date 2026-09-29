"""Adapter de cupons da Pichau.

Unica das tres cujo texto vem HTML renderizado no servidor (confirmado no
fixture real -- MUI/emotion, mas os dados ja chegam no primeiro `GET`, sem
precisar de JS). As classes CSS de estilo (`mui-19bc7ab-percent` etc.) sao
hash de build e podem mudar a qualquer deploy; os seletores aqui evitam essas
classes e usam so o que e estavel: as classes utilitarias do MUI
(`MuiTypography-h6`/`MuiTypography-body2`) e o atributo `aria-label` do botao
de copiar, que carrega o texto que o cliente realmente usaria.

A pagina nao expoe texto legal/condicoes por cupom (so um FAQ generico no
rodape) -- `terms_text` fica sempre `None` aqui.
"""

from __future__ import annotations

import logging

from selectolax.parser import HTMLParser

from ..http import Fetcher
from ..models import RawCoupon

log = logging.getLogger(__name__)

PAGINA = "https://www.pichau.com.br/promocao/cupons"


class PichauCouponAdapter:
    name = "pichau"

    def fetch(self, fetcher: Fetcher) -> list[RawCoupon]:
        arvore = HTMLParser(fetcher.get(PAGINA))
        cards = arvore.css("div.MuiCard-root")
        if not cards:
            raise RuntimeError(
                "nenhum div.MuiCard-root na resposta da Pichau -- layout mudou?"
            )

        cupons: list[RawCoupon] = []
        for card in cards:
            botao = card.css_first('button[aria-label^="Copiar cupom "]')
            if botao is None:
                continue
            codigo = botao.text(strip=True)
            if not codigo:
                continue

            desconto = card.css_first("p.MuiTypography-h6")
            escopo = card.css_first("p.MuiTypography-body2")

            cupons.append(
                RawCoupon(
                    store=self.name,
                    code=codigo,
                    discount_text=desconto.text(strip=True) if desconto else "",
                    scope_text=escopo.text(strip=True) if escopo else "",
                    terms_text=None,
                    url=f"{PAGINA}/{codigo}",
                )
            )
        return cupons
