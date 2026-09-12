"""Autoteste de ambiente -- roda no servidor antes de confiar no deploy.

Existe por causa de um risco concreto, nao por zelo generico: o `curl_cffi`
embarca `libcurl-impersonate` compilado, e o servidor de destino e um Core 2 Duo
(x86-64 baseline, sem SSE4.2/AVX -- secao 12 da SPEC). Se esse binario nao rodar
la, a coleta falha inteira, e o sintoma seria um silencio que parece "nenhuma
oferta nova".

Este comando transforma esse risco em uma resposta de uma linha.
"""

from __future__ import annotations

import logging
import platform
import sys

from .models import AppConfig

log = logging.getLogger(__name__)

# Nao ha lista de URLs aqui de proposito. A primeira versao tinha, e as duas
# lojas VTEX da Fase 5 ficaram fora dela: estavam habilitadas na config e o
# autoteste as ignorava em silencio -- exatamente no comando que existe para
# dizer se o ambiente coleta. Agora sondamos pelos adapters reais, que e
# tambem um teste de parsing de graca.


def executa(cfg: AppConfig, rede: bool = True) -> int:
    falhas = 0
    print("=" * 64)
    print("AUTOTESTE DE AMBIENTE")
    print("=" * 64)
    print(f"  python      : {platform.python_version()}")
    print(f"  plataforma  : {platform.platform()}")
    print(f"  arquitetura : {platform.machine()}")

    # 1. O binario compilado carrega neste CPU?
    try:
        from curl_cffi import requests as curl_requests  # noqa: F401
        from curl_cffi import __version__ as versao
        print(f"  curl_cffi   : {versao} carregou OK")
    except Exception as e:  # noqa: BLE001
        print(f"  curl_cffi   : FALHOU AO CARREGAR -- {type(e).__name__}: {e}")
        print()
        print("  O binario libcurl-impersonate nao roda neste CPU.")
        print("  Plano B (secao 11.2 da SPEC): usar o curl do sistema por subprocess.")
        return 1

    if not rede:
        print("\n  (sondagem de rede pulada)")
        return 0

    # 2. Cada loja habilitada responde E o parser entende a resposta?
    from .collector import monta_adapter
    from .http import Fetcher

    pares = _pares_para_sondar(cfg)
    if not pares:
        print("\n  nenhuma loja habilitada com alvo correspondente na config")
        return 1

    print("\n  Sondando cada loja habilitada, pelo adapter real:")
    with Fetcher(timeout=cfg.http.timeout_seconds, retries=1) as f:
        for loja, alvo in pares:
            adapter = monta_adapter(loja, cfg)
            if adapter is None:
                print(f"    {loja:11} FALHOU -- sem adapter para esta loja")
                falhas += 1
                continue
            try:
                brutas = adapter.fetch(alvo, f)
            except Exception as e:  # noqa: BLE001
                print(f"    {loja:11} FALHOU -- {type(e).__name__}: {str(e)[:56]}")
                falhas += 1
                continue
            if not brutas:
                print(f"    {loja:11} VAZIO -- respondeu, mas 0 ofertas ({alvo.id})")
                falhas += 1
            else:
                print(f"    {loja:11} OK -- {len(brutas):>3} ofertas ({alvo.id})")

    print()
    if falhas:
        print(f"  {falhas} problema(s). Ver secoes 4 e 11.2 da SPEC.")
    else:
        print("  Todas as lojas responderam e foram interpretadas. Ambiente apto.")
    print()
    return 1 if falhas else 0


def _pares_para_sondar(cfg: AppConfig) -> list[tuple[str, object]]:
    """Um (loja, alvo) por loja habilitada, respeitando a categoria."""
    pares = []
    for loja, ajustes in cfg.stores.items():
        if not ajustes.enabled:
            continue
        alvo = next(
            (t for t in cfg.targets if loja in cfg.stores_for(t.category)), None
        )
        if alvo is not None:
            pares.append((loja, alvo))
        else:
            log.warning("loja %r habilitada mas sem alvo da categoria dela", loja)
    return pares


def healthcheck(caminho_db, max_horas: int = 24) -> int:
    """Usado pelo HEALTHCHECK do Docker.

    Saudavel = houve coleta bem-sucedida nas ultimas `max_horas`. Container
    recem-subido ainda nao coletou, e isso nao e doenca: nesse caso passa.
    """
    from datetime import datetime, timedelta, timezone
    from .db import Repo

    try:
        with Repo(caminho_db) as repo:
            linha = repo.con.execute(
                "SELECT MAX(finished_at) FROM collection_run WHERE status != 'failed'"
            ).fetchone()
    except Exception as e:  # noqa: BLE001
        print(f"healthcheck: banco inacessivel: {e}", file=sys.stderr)
        return 1

    ultimo = linha[0] if linha else None
    if not ultimo:
        print("healthcheck: nenhuma coleta ainda (container novo) -- ok")
        return 0

    quando = datetime.fromisoformat(ultimo)
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    idade = datetime.now(timezone.utc) - quando
    if idade > timedelta(hours=max_horas):
        print(f"healthcheck: ultima coleta ha {idade}, acima de {max_horas}h",
              file=sys.stderr)
        return 1
    print(f"healthcheck: ultima coleta ha {idade} -- ok")
    return 0
