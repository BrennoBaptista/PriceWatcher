"""Adapter generico para lojas VTEX.

A VTEX expoe uma **API publica de catalogo** que qualquer loja da plataforma
serve no mesmo caminho:

    /api/catalog_system/pub/products/search?ft=<termo>&_from=N&_to=M

Isso e o oposto de raspar HTML: vem JSON estruturado, com vendedor, estoque e
preco por meio de pagamento. Um adapter parametrizado por dominio cobre todas
as lojas VTEX -- adicionar uma loja nova passa a ser uma entrada de config.

Confirmado no spike da Fase 5 (2026-09-11):

* **Casa e Video** e **Americanas** rodam VTEX e respondem a essa API.
* O **preco a vista no PIX** vem separado, em `Installments`, na entrada com
  `PaymentSystemName == "Pix"` e `NumberOfInstallments == 1`. Na Casa e Video o
  PIX estava 17% abaixo do preco cheio -- usar `Price` daria o numero errado.
* O **vendedor** e explicito: `sellerId == "1"` e a propria loja (1P); qualquer
  outro e marketplace. Isso torna a regra 1P da secao 5 implementavel de fato.

Paginacao: `_from`/`_to` sao inclusivos e a VTEX limita a janela a 50 itens.
"""

from __future__ import annotations

import logging
import urllib.parse

from ...http import Fetcher
from ...models import RawOffer, SellerType, Target
from ..base import para_centavos

log = logging.getLogger(__name__)

CAMINHO = "/api/catalog_system/pub/products/search"
POR_PAGINA = 50
MAX_PAGINAS = 3

# Na VTEX, o seller "1" e sempre a propria loja.
SELLER_PROPRIO = "1"


# A secao 2 da SPEC define o preco de referencia como "a vista (PIX/boleto)".
# O PIX vem primeiro porque costuma ter desconto; o boleto e o mesmo pagamento
# a vista sem desconto, e serve de segunda opcao.
_ORDEM_AVISTA = ("pix", "boleto bancário", "boleto")


def _preco_avista(oferta: dict) -> int | None:
    """Preco a vista, preferindo PIX.

    Nao usar `Price`: ele e o preco cheio. Na Casa e Video o PIX sai 17% abaixo,
    e e esse o numero que a pessoa realmente paga.

    Item esgotado nao traz `Installments` -- devolver None aqui e correto, e o
    normalizador registra a observacao mesmo assim para detectar volta ao
    estoque.
    """
    por_metodo: dict[str, int] = {}
    for parcela in oferta.get("Installments") or []:
        if parcela.get("NumberOfInstallments") != 1:
            continue
        nome = (parcela.get("PaymentSystemName") or "").strip().lower()
        valor = parcela.get("TotalValuePlusInterestRate") or parcela.get("Value")
        centavos = para_centavos(valor)
        if nome and centavos:
            por_metodo.setdefault(nome, centavos)

    for metodo in _ORDEM_AVISTA:
        if metodo in por_metodo:
            return por_metodo[metodo]
    return None


def _max_parcelas(oferta: dict) -> int | None:
    ns = [
        p.get("NumberOfInstallments")
        for p in oferta.get("Installments") or []
        if p.get("NumberOfInstallments")
    ]
    return max(ns) if ns else None


class VtexAdapter:
    """Instanciado com o nome da loja e o dominio, ambos vindos da config."""

    def __init__(self, name: str, base_url: str) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")

    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]:
        ofertas: list[RawOffer] = []
        vistos: set[str] = set()
        for termo in target.search_terms:
            for pagina in range(MAX_PAGINAS):
                inicio = pagina * POR_PAGINA
                url = (
                    f"{self.base_url}{CAMINHO}?ft={urllib.parse.quote(termo)}"
                    f"&_from={inicio}&_to={inicio + POR_PAGINA - 1}"
                )
                lote = self._pagina(url, fetcher)
                novas = [o for o in lote if o.store_sku not in vistos]
                for o in novas:
                    vistos.add(o.store_sku)
                ofertas.extend(novas)
                if len(lote) < POR_PAGINA:
                    break  # acabou o catalogo para este termo
        return ofertas

    def _pagina(self, url: str, fetcher: Fetcher) -> list[RawOffer]:
        import json

        corpo = fetcher.get(url)
        try:
            produtos = json.loads(corpo)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"resposta da API VTEX de {self.name} nao e JSON -- "
                f"loja saiu da plataforma ou esta bloqueando?"
            ) from e
        if not isinstance(produtos, list):
            raise RuntimeError(
                f"API VTEX de {self.name} devolveu {type(produtos).__name__}, "
                f"esperava lista"
            )

        ofertas: list[RawOffer] = []
        for p in produtos:
            for item in p.get("items") or []:
                oferta = self._do_item(p, item)
                if oferta is not None:
                    ofertas.append(oferta)
        return ofertas

    def _do_item(self, produto: dict, item: dict) -> RawOffer | None:
        vendedores = item.get("sellers") or []
        if not vendedores:
            return None

        # Preferimos o vendedor proprio; se nao houver, o primeiro serve para o
        # normalizador descartar com o motivo certo.
        proprio = next(
            (s for s in vendedores if str(s.get("sellerId")) == SELLER_PROPRIO), None
        )
        vendedor = proprio or vendedores[0]
        oferta = vendedor.get("commertialOffer") or {}

        sku = str(item.get("itemId") or "")
        if not sku:
            return None

        disponivel = bool(oferta.get("IsAvailable")) and (
            oferta.get("AvailableQuantity") or 0
        ) > 0

        return RawOffer(
            store=self.name,
            store_sku=sku,
            url=produto.get("link") or f"{self.base_url}/{produto.get('linkText','')}/p",
            title_raw=produto.get("productName") or "",
            price_cash=_preco_avista(oferta),
            price_installment=para_centavos(oferta.get("Price")),
            installments=_max_parcelas(oferta),
            available=disponivel,
            seller_type=(
                SellerType.FIRST_PARTY if proprio is not None else SellerType.MARKETPLACE
            ),
            seller_name=vendedor.get("sellerName"),
        )
