"""Adapter para comparadores de preco (Zoom, Buscapé, Bondfaro).

## O que este adapter e, e o que ele nao e

Ele existe por **um** motivo: alcancar lojas que nao conseguimos raspar direto.
Magazine Luiza, Casas Bahia e Ponto ficam atras do Akamai Bot Manager, que exige
execucao de JavaScript, e o servidor nao comporta navegador headless (secao 12
da SPEC). A Amazon esta fora por decisao de projeto (secao 4.1). O agregador
enxerga essas lojas.

Ele **nao** e uma fonte alternativa para KaBuM!, Pichau e Terabyte. Essas nos
raspamos direto, com preco de primeira mao. Aceitar as duas fontes geraria
alerta duplicado e deixaria um preco de segunda mao competir com o original.
Por isso a config traz uma lista explicita de lojas aceitas.

## Verificado no spike de 2026-09-11

* **Zoom, Buscapé e Bondfaro sao a mesma fonte.** Os tres devolveram dados byte
  a byte identicos -- mesmo `objectId`, mesmo preco, mesmas contagens. Sao
  fachadas de um backend so. Configurar mais de um seria coletar o mesmo dado
  varias vezes.
* **O preco parece ser o a vista.** Para a KaBuM!, o Zoom mostrou a PowerColor
  Reaper a R$ 5.599,99 -- exatamente o nosso preco PIX, e nao o preco cheio de
  R$ 6.588,22. Mas **nao ha campo declarando a base**, e a conferencia foi em um
  vendedor so. Por isso existe `--auditoria`.
* **O ganho e concreto:** a Magazine Luiza apareceu com uma RX 9070 XT a
  R$ 4.999,99, contra R$ 5.199,99 do nosso melhor preco direto.

## Limitacao aceita

A busca traz a **melhor oferta** de cada produto, com a loja que a pratica. Nao
traz o preco de cada loja sem pedir a pagina do produto, uma requisicao por
item. Ficamos com a melhor oferta: e o numero que interessa para decidir compra,
e mantem o volume baixo.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse

from ...http import Fetcher
from ...models import RawOffer, SellerType, Target
from ..base import para_centavos

log = logging.getLogger(__name__)

BUSCA = "{base}/search?q={termo}"
NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def _normaliza_loja(nome: str) -> str:
    """'KaBuM!' e 'kabum' precisam casar na config sem exigir grafia exata."""
    return re.sub(r"[^a-z0-9]", "", (nome or "").lower())


class AgregadorAdapter:
    """Instanciado com o nome da fonte e o dominio, ambos vindos da config."""

    def __init__(
        self,
        name: str,
        base_url: str,
        merchants: list[str] | None = None,
        merchants_auditoria: list[str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._aceitos = {_normaliza_loja(m) for m in merchants or []}
        self._auditoria = {_normaliza_loja(m) for m in merchants_auditoria or []}

    # ------------------------------------------------------------- coleta
    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]:
        ofertas: list[RawOffer] = []
        vistos: set[str] = set()
        for termo in target.search_terms:
            for bruta in self._busca(termo, fetcher):
                loja = _normaliza_loja(bruta.seller_name or "")
                if self._aceitos and loja not in self._aceitos:
                    if loja in self._auditoria:
                        log.debug(
                            "[%s] %s ignorada aqui: coletamos direto (auditoria)",
                            self.name, bruta.seller_name,
                        )
                    continue
                if bruta.store_sku not in vistos:
                    vistos.add(bruta.store_sku)
                    ofertas.append(bruta)
        return ofertas

    def ofertas_brutas(self, termo: str, fetcher: Fetcher) -> list[RawOffer]:
        """Sem filtro de loja. Usado pela auditoria, que precisa justamente das
        lojas que a coleta descarta."""
        return self._busca(termo, fetcher)

    # ------------------------------------------------------------ interno
    def _busca(self, termo: str, fetcher: Fetcher) -> list[RawOffer]:
        url = BUSCA.format(base=self.base_url, termo=urllib.parse.quote(termo))
        html = fetcher.get(url)
        m = NEXT_DATA.search(html)
        if not m:
            raise RuntimeError(
                f"__NEXT_DATA__ ausente na resposta de {self.name} -- layout mudou?"
            )
        dados = json.loads(m.group(1))
        try:
            hits = dados["props"]["initialReduxState"]["hits"]["hits"]
        except KeyError as e:
            raise RuntimeError(
                f"caminho esperado do JSON de {self.name} nao existe mais: {e}"
            ) from e

        ofertas = []
        for h in hits:
            oferta = self._do_hit(h)
            if oferta is not None:
                ofertas.append(oferta)
        return ofertas

    def _do_hit(self, hit: dict) -> RawOffer | None:
        sku = str(hit.get("objectId") or hit.get("sourceId") or "")
        preco = para_centavos(hit.get("price"))
        if not sku or not preco:
            return None

        melhor = hit.get("bestOffer") or {}
        loja = melhor.get("merchantName") or melhor.get("sellerName")
        if not loja:
            # Sem saber de que loja e o preco, a oferta e inutil: nao da para
            # confirmar na origem nem aplicar a regra de lojas aceitas.
            return None

        caminho = hit.get("url") or ""
        url = caminho if caminho.startswith("http") else f"{self.base_url}{caminho}"

        return RawOffer(
            store=self.name,
            store_sku=sku,
            url=url,
            title_raw=(hit.get("name") or "").strip(),
            price_cash=preco,
            price_installment=None,
            installments=None,
            # O agregador so lista o que esta a venda. Produto esgotado some da
            # busca em vez de aparecer indisponivel -- por isso nao da para
            # detectar volta ao estoque por aqui.
            available=True,
            # Confiamos na identidade da loja, nao no tipo do anuncio dentro
            # dela: nao ha como saber se e venda propria ou marketplace do
            # Magalu. Ver "Limitacao aceita" na docstring do modulo.
            seller_type=SellerType.FIRST_PARTY,
            seller_name=loja,
        )
