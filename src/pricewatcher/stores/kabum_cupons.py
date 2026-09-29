"""Adapter de cupons da Kabum.

Fonte: **nao** e a mesma da busca de produto. `/hotsite/cupons` e um microsite
estatico cujo `js/script.js` busca os cartoes de uma planilha Google Sheets
publicada (endpoint `gviz/tq`) -- o HTML puro nao carrega cupom nenhum, so o
feed conta. Descoberto inspecionando o script real: sem isso o adapter
tentaria raspar `div.cupom.ativo`, que so existe depois do JS rodar.

Cada linha da planilha (colunas B..H): data inicial, data final, codigo,
desconto, condicao (escopo), link, texto legal. A propria Kabum calcula o
status (ativo/em breve/encerrado) comparando as datas com "agora" no
navegador -- replicamos a mesma logica aqui, em vez de confiar em uma coluna
de status que nao existe na planilha.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from ..http import Fetcher
from ..models import RawCoupon

log = logging.getLogger(__name__)

PLANILHA = (
    "https://docs.google.com/spreadsheets/d/"
    "1Hi1d81MW60rycvtmE8jFaFdekrlgndbvXrWglu33RtA/gviz/tq?"
)
RESPOSTA = re.compile(r"setResponse\((.*)\);\s*$", re.S)


class KabumCouponAdapter:
    name = "kabum"

    def fetch(self, fetcher: Fetcher) -> list[RawCoupon]:
        corpo = fetcher.get(PLANILHA)
        m = RESPOSTA.search(corpo)
        if not m:
            raise RuntimeError(
                "resposta da planilha de cupons Kabum em formato inesperado -- "
                "endpoint mudou?"
            )
        try:
            linhas = json.loads(m.group(1))["table"]["rows"]
        except (json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(
                f"planilha de cupons Kabum sem a estrutura table.rows esperada: {e}"
            ) from e

        agora = datetime.now(timezone.utc)
        cupons: list[RawCoupon] = []
        for linha in linhas:
            valores = [c["v"] if c else None for c in linha.get("c", [])]
            if len(valores) < 8:
                continue
            inicio_raw, fim_raw, codigo, desconto, escopo, link, legal = valores[1:8]
            if not codigo or not inicio_raw or not fim_raw:
                continue  # linha de cabecalho ou incompleta
            try:
                inicio = datetime.fromisoformat(inicio_raw)
                fim = datetime.fromisoformat(fim_raw)
            except (TypeError, ValueError):
                continue
            if not (inicio <= agora <= fim):
                continue  # em breve ou ja encerrado
            cupons.append(
                RawCoupon(
                    store=self.name,
                    code=codigo,
                    discount_text=desconto or "",
                    scope_text=escopo or "",
                    terms_text=legal,
                    url=link or "https://www.kabum.com.br/",
                )
            )
        return cupons
