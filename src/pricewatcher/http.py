"""Cliente HTTP compartilhado pelos adapters.

## Por que curl_cffi e nao httpx

Pichau e Terabyte ficam atras de um WAF que faz **fingerprint de TLS** (JA3/JA4).
O handshake do modulo `ssl` do Python e reconhecivel e leva 403 -- e nao ha
cabecalho que resolva. Medido durante a Fase 1:

| Cliente | Kabum | Pichau | Terabyte |
|---------|-------|--------|----------|
| httpx HTTP/1.1                    | 200 | 403 | 403 |
| httpx HTTP/2                      | 200 | 403 | 403 |
| httpx + ciphers de Chrome         | 200 | 403 | 403 |
| curl_cffi `impersonate="chrome"`  | 200 | 200 | 200 |

Ajustar `SSLContext` nao basta: o WAF olha ordem de extensoes e GREASE, que o
`ssl` do Python nao produz. O curl_cffi reproduz o handshake do Chrome inteiro.

`impersonate="firefox"` passa na Terabyte mas leva 403 na Pichau -- por isso o
perfil padrao e o do Chrome.

## Risco conhecido

O curl_cffi embarca `libcurl-impersonate` compilado. O servidor de destino e um
Core 2 Duo (x86-64 baseline, sem SSE4.2/AVX -- secao 12 da SPEC), e **nao esta
verificado** que esse binario roda la. Testar no deploy da Fase 3. Se nao rodar,
o plano B e chamar o `curl` do sistema por subprocess, que resolve o mesmo
problema (foi assim que o spike da Fase 0 funcionou).
"""

from __future__ import annotations

import logging
import random
import time

from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import HTTPError, RequestException

log = logging.getLogger(__name__)

# Perfil de navegador imitado no handshake TLS.
IMPERSONATE = "chrome"

# O impersonate ja instala o conjunto de cabecalhos coerente com o Chrome.
# Aqui so acrescentamos o que e especifico do nosso uso.
HEADERS_EXTRA = {"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}


class BlocoDeAcesso(RuntimeError):
    """403/429 -- a loja recusou a requisicao, nao adianta insistir na rodada."""


class Fetcher:
    """Busca paginas com retry, backoff e atraso educado entre requisicoes."""

    def __init__(
        self,
        timeout: int = 20,
        retries: int = 3,
        delay_min: float = 2.0,
        delay_max: float = 6.0,
        impersonate: str = IMPERSONATE,
    ) -> None:
        self._sessao = curl_requests.Session(
            impersonate=impersonate,
            headers=HEADERS_EXTRA,
            timeout=timeout,
        )
        self._retries = retries
        self._delay = (delay_min, delay_max)
        self._primeira = True

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self._sessao.close()

    def _espera(self) -> None:
        if self._primeira:
            self._primeira = False
            return
        time.sleep(random.uniform(*self._delay))

    def get(self, url: str) -> str:
        self._espera()
        ultima: Exception | None = None
        for tentativa in range(1, self._retries + 1):
            try:
                r = self._sessao.get(url)
            except RequestException as e:
                ultima = e
            else:
                # Qualquer 2xx serve. A API de catalogo da VTEX responde 206
                # (Partial Content) em busca paginada -- e o comportamento
                # correto dela, nao erro.
                if 200 <= r.status_code < 300:
                    return r.text
                if r.status_code in (403, 429):
                    raise BlocoDeAcesso(
                        f"{r.status_code} em {url} -- bloqueio de acesso"
                    )
                if 400 <= r.status_code < 500:
                    raise HTTPError(f"{r.status_code} em {url}")
                ultima = HTTPError(f"{r.status_code} em {url}")

            if tentativa < self._retries:
                espera = 2 ** tentativa
                log.warning(
                    "tentativa %d/%d falhou para %s (%s); aguardando %ds",
                    tentativa, self._retries, url, ultima, espera,
                )
                time.sleep(espera)

        assert ultima is not None
        raise ultima
