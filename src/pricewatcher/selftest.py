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

SONDAS = {
    "kabum": "https://www.kabum.com.br/busca/rx%209070%20xt",
    "pichau": "https://www.pichau.com.br/search?q=rx+9070+xt&page=1",
    "terabyte": "https://www.terabyteshop.com.br/busca?str=rx+9070+xt",
}


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

    # 2. O fingerprint de TLS passa nas lojas?
    print("\n  Sondando as lojas habilitadas:")
    from .http import Fetcher

    habilitadas = [n for n, s in cfg.stores.items() if s.enabled and n in SONDAS]
    with Fetcher(timeout=cfg.http.timeout_seconds, retries=1) as f:
        for nome in habilitadas:
            try:
                corpo = f.get(SONDAS[nome])
            except Exception as e:  # noqa: BLE001
                print(f"    {nome:10} FALHOU -- {type(e).__name__}: {str(e)[:60]}")
                falhas += 1
            else:
                print(f"    {nome:10} OK ({len(corpo) // 1024} KB)")

    print()
    if falhas:
        print(f"  {falhas} loja(s) inacessivel(is). Ver secao 11.2 da SPEC.")
    else:
        print("  Todas as lojas responderam. Ambiente apto.")
    print()
    return 1 if falhas else 0


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
