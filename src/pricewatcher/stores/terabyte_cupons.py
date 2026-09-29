"""Adapter de cupons da Terabyteshop.

A pagina de cupons NAO fica em terabyteshop.com.br: o menu da loja aponta
para um subdominio separado, `landing.terabyteshop.com.br/cupons/`, que e uma
SPA (bundle Vite) sem conteudo no HTML inicial. O feed real, encontrado
inspecionando o bundle JS, e um endpoint JSON no dominio principal:

    GET https://www.terabyteshop.com.br/content/coupons.php

Devolve todos os cupons (ativos, futuros e recem-encerrados) com `startTime`/
`endTime` **sem fuso explicito** -- sao horario de Brasilia, igual ao resto do
site (secao 3 da SPEC). Calculamos "ativo" comparando com agora em
America/Sao_Paulo, em vez de confiar em alguma secao separada de "encerrados"
que so existe no front.

Cada cupom traz `condition.product[].department` e/ou `condition.department`:
nomes de categoria estruturados (ex.: "NVIDIA GeForce", "AMD Radeon" no cupom
TERAHORSE). Anexamos isso ao `scope_text` para o filtro de relevancia (que e
so regex sobre texto) ter mais sinal do que só o `subtitle`, que às vezes nem
menciona a categoria (ex.: "No Modelo Selecionado").
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from ..http import Fetcher
from ..models import RawCoupon
from ..tempo import fuso

log = logging.getLogger(__name__)

FEED = "https://www.terabyteshop.com.br/content/coupons.php"


def _parse_horario(valor: str) -> datetime:
    return datetime.strptime(valor, "%Y-%m-%d %H:%M:%S").replace(tzinfo=fuso())


class TerabyteCouponAdapter:
    name = "terabyte"

    def fetch(self, fetcher: Fetcher) -> list[RawCoupon]:
        corpo = fetcher.get(FEED)
        try:
            itens = json.loads(corpo)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"feed de cupons Terabyte nao devolveu JSON valido: {e}"
            ) from e
        if not isinstance(itens, list):
            raise RuntimeError(
                "feed de cupons Terabyte mudou de formato -- esperava uma lista"
            )

        agora = datetime.now(fuso())
        cupons: list[RawCoupon] = []
        for item in itens:
            codigo = item.get("name")
            inicio_raw, fim_raw = item.get("startTime"), item.get("endTime")
            if not codigo or not inicio_raw or not fim_raw:
                continue
            try:
                inicio = _parse_horario(inicio_raw)
                fim = _parse_horario(fim_raw)
            except ValueError:
                continue
            if not (inicio <= agora <= fim):
                continue

            condicao = item.get("condition") or {}
            departamentos = {
                str(d) for d in condicao.get("department", []) if d
            }
            for p in condicao.get("product", []):
                if p.get("department"):
                    departamentos.add(str(p["department"]))

            escopo = item.get("subtitle") or ""
            if departamentos:
                escopo = f"{escopo} {' '.join(sorted(departamentos))}"

            cupons.append(
                RawCoupon(
                    store=self.name,
                    code=codigo,
                    discount_text=item.get("title") or "",
                    scope_text=escopo.strip(),
                    terms_text=None,
                    url=f"https://www.terabyteshop.com.br/?cupom={codigo}",
                )
            )
        return cupons
