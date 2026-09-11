"""Adapter da Kabum.

Fonte: JSON embutido em `__NEXT_DATA__` (Next.js Pages Router), em
`props.pageProps.data.catalogServer.data[]`.

Tres detalhes confirmados no spike da Fase 0:

* `priceWithDiscount` e o preco a vista; `price` e o cheio/parcelado.
* **`available` vem `true` em 100% dos itens**, inclusive esgotados. E inutil.
  A disponibilidade real esta em `quantity > 0`. Ver secao 7.1 da SPEC.
* `sellerId == 0` identifica venda pela propria Kabum (1P). O marketplace nunca
  preenche `quantity`, entao para ele nao ha sinal de estoque confiavel --
  motivo adicional para descartar terceiros.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse

from ..http import Fetcher
from ..models import RawOffer, SellerType, Target
from .base import para_centavos, para_parcelas

log = logging.getLogger(__name__)

BUSCA = "https://www.kabum.com.br/busca/{termo}"
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


class KabumAdapter:
    name = "kabum"

    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]:
        ofertas: list[RawOffer] = []
        vistos: set[str] = set()
        for termo in target.search_terms:
            url = BUSCA.format(termo=urllib.parse.quote(termo))
            for oferta in self._busca(url, fetcher):
                if oferta.store_sku not in vistos:
                    vistos.add(oferta.store_sku)
                    ofertas.append(oferta)
        return ofertas

    def _busca(self, url: str, fetcher: Fetcher) -> list[RawOffer]:
        html = fetcher.get(url)
        m = NEXT_DATA.search(html)
        if not m:
            raise RuntimeError(
                "__NEXT_DATA__ ausente na resposta da Kabum -- layout mudou?"
            )
        dados = json.loads(m.group(1))
        try:
            itens = dados["props"]["pageProps"]["data"]["catalogServer"]["data"]
        except KeyError as e:
            raise RuntimeError(
                f"caminho esperado do JSON da Kabum nao existe mais: {e}"
            ) from e

        ofertas = []
        for p in itens:
            codigo = str(p.get("code") or "")
            if not codigo:
                continue
            seller_id = p.get("sellerId")
            quantidade = p.get("quantity") or 0
            ofertas.append(
                RawOffer(
                    store=self.name,
                    store_sku=codigo,
                    url=(
                        f"https://www.kabum.com.br/produto/{codigo}/"
                        f"{p.get('friendlyName', '')}"
                    ),
                    title_raw=p.get("name") or "",
                    price_cash=para_centavos(p.get("priceWithDiscount")),
                    price_installment=para_centavos(p.get("price")),
                    installments=para_parcelas(p.get("maxInstallment")),
                    # NAO usar p['available'] -- ver docstring do modulo.
                    available=quantidade > 0,
                    seller_type=(
                        SellerType.FIRST_PARTY
                        if seller_id == 0
                        else SellerType.MARKETPLACE
                    ),
                    seller_name=p.get("sellerName"),
                )
            )
        return ofertas
