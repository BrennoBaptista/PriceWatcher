"""Adapter da Pichau.

A Pichau **nao** expoe GraphQL publico -- `/graphql` cai numa pagina de manutencao
do frontend Magento antigo. O site atual e Next.js App Router, e os dados chegam no
payload RSC (`self.__next_f`), que ainda carrega objetos de origem GraphQL.

Estrutura util: `"products": {"items": [...], "total_count": N, "page_info": {...}}`.

Duas licoes que custaram caro no spike:

* **Nao contar chaves na mao para achar o objeto JSON.** As descricoes de produto
  contem HTML com `{` e `}` dentro de strings, o que quebra qualquer contagem
  ingenua de profundidade. O caminho certo e `json.JSONDecoder().raw_decode`, que
  respeita strings. Com a contagem manual, 34 dos 36 produtos eram descartados em
  silencio.
* **A busca e muito frouxa**: "rtx 5070 ti" devolve 2054 resultados em 58 paginas,
  e a maioria e PC montado (`BundleProduct`) que apenas menciona a placa. Os itens
  relevantes ficam nas primeiras paginas, entao paginamos com parada antecipada.
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

BUSCA = "https://www.pichau.com.br/search?q={termo}&page={pagina}"
CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)
MAX_PAGINAS = 3

_DECODER = json.JSONDecoder()


def _remonta_payload(html: str) -> str:
    """Junta e desescapa os chunks do payload RSC."""
    blob = "".join(CHUNK.findall(html))
    return (
        blob.encode()
        .decode("unicode_escape")
        .encode("latin1", "ignore")
        .decode("utf-8", "ignore")
    )


def _extrai_products(blob: str) -> dict:
    i = blob.find('"products":{')
    if i < 0:
        raise RuntimeError(
            'bloco "products" ausente no payload da Pichau -- layout mudou?'
        )
    obj, _ = _DECODER.raw_decode(blob, i + len('"products":'))
    return obj


class PichauAdapter:
    name = "pichau"

    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]:
        # Usado apenas para decidir quando parar de paginar. O filtro que vale
        # continua sendo o do normalizador.
        relevante = re.compile(target.match_regex) if target.match_regex else None

        ofertas: list[RawOffer] = []
        vistos: set[str] = set()
        for termo in target.search_terms:
            for pagina in range(1, MAX_PAGINAS + 1):
                url = BUSCA.format(
                    termo=urllib.parse.quote_plus(termo), pagina=pagina
                )
                lote = self._pagina(url, fetcher)
                novos = [o for o in lote if o.store_sku not in vistos]
                for o in novos:
                    vistos.add(o.store_sku)
                ofertas.extend(novos)

                if not lote:
                    break
                if relevante and not any(relevante.search(o.title_raw) for o in lote):
                    log.debug(
                        "pichau: pagina %d de %r sem item relevante, parando",
                        pagina, termo,
                    )
                    break
        return ofertas

    def _pagina(self, url: str, fetcher: Fetcher) -> list[RawOffer]:
        blob = _remonta_payload(fetcher.get(url))
        produtos = _extrai_products(blob)

        ofertas = []
        for p in produtos.get("items", []):
            sku = p.get("sku")
            if not sku:
                continue
            precos = p.get("pichau_prices") or {}
            ofertas.append(
                RawOffer(
                    store=self.name,
                    store_sku=str(sku),
                    url=f"https://www.pichau.com.br/{p.get('url_key', '')}",
                    title_raw=p.get("name") or "",
                    price_cash=para_centavos(precos.get("avista")),
                    price_installment=para_centavos(precos.get("base_price")),
                    installments=para_parcelas(precos.get("max_installments")),
                    available=p.get("stock_status") == "IN_STOCK",
                    # A Pichau nao opera marketplace: tudo e venda propria.
                    seller_type=SellerType.FIRST_PARTY,
                    seller_name="Pichau",
                )
            )
        return ofertas
