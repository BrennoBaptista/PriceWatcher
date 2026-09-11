"""Adapter da Terabyteshop.

Unica das tres lojas do v1 com HTML renderizado no servidor. O card de produto
carrega os dados em atributos, o que e mais estavel que garimpar texto:

    <div class="product-item" data-tss-brand="ASROCK" data-tss-price="5399.9"
         data-tss-estoque="1" data-tss-promo="1">

Confirmado no spike:

* `data-tss-price` e o preco **a vista no Pix** -- bate com o texto exibido.
* `data-tss-estoque="0"` correlaciona 254/254 com "Esgotado"/"Indisponivel" na
  pagina, entao o atributo e sinal confiavel de estoque.
* A busca devolve a categoria inteira (~300 cards), nao so o que casa o termo.
"""

from __future__ import annotations

import html as html_mod
import logging
import urllib.parse

from selectolax.parser import HTMLParser

from ..http import Fetcher
from ..models import RawOffer, SellerType, Target
from .base import para_centavos

log = logging.getLogger(__name__)

BUSCA = "https://www.terabyteshop.com.br/busca?str={termo}"
BASE = "https://www.terabyteshop.com.br"


class TerabyteAdapter:
    name = "terabyte"

    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]:
        ofertas: list[RawOffer] = []
        vistos: set[str] = set()
        for termo in target.search_terms:
            url = BUSCA.format(termo=urllib.parse.quote_plus(termo))
            for oferta in self._busca(url, fetcher):
                if oferta.store_sku not in vistos:
                    vistos.add(oferta.store_sku)
                    ofertas.append(oferta)
        return ofertas

    def _busca(self, url: str, fetcher: Fetcher) -> list[RawOffer]:
        arvore = HTMLParser(fetcher.get(url))
        cards = arvore.css("div.product-item")
        if not cards:
            raise RuntimeError(
                "nenhum div.product-item na resposta da Terabyte -- layout mudou?"
            )

        ofertas = []
        for card in cards:
            link = card.css_first('a[href*="/produto/"]')
            if link is None:
                continue
            href = link.attributes.get("href") or ""
            partes = href.strip("/").split("/")
            if len(partes) < 2 or partes[0] != "produto":
                continue
            sku = partes[1]

            titulo = link.attributes.get("title") or link.text(strip=True)
            preco = para_centavos(card.attributes.get("data-tss-price"))
            estoque = card.attributes.get("data-tss-estoque")

            ofertas.append(
                RawOffer(
                    store=self.name,
                    store_sku=sku,
                    url=BASE + href if href.startswith("/") else href,
                    title_raw=html_mod.unescape(titulo or ""),
                    price_cash=preco,
                    price_installment=None,
                    installments=None,
                    available=estoque == "1",
                    # A Terabyte nao opera marketplace.
                    seller_type=SellerType.FIRST_PARTY,
                    seller_name="Terabyte",
                )
            )
        return ofertas
