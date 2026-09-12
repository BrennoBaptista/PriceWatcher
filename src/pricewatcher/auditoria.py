"""Confere o preco do agregador contra o que coletamos direto.

## Por que isso existe

O agregador publica preco de **segunda mao**. No spike, o preco que o Zoom
atribuia a KaBuM! bateu exatamente com o nosso preco PIX -- mas isso foi
verificado em um vendedor, num instante, e **nao ha campo declarando a base do
preco**. Se o comparador passar a publicar preco cheio em vez de a vista, todos
os produtos dele "cairiam" uns 15% de uma vez, e isso passaria por todos os
guardrails da secao 7: a queda e plausivel, gradual entre produtos, e bate o
minimo historico de cada um.

Seria um disparo em massa de alertas falsos, sem nada no log sugerindo o motivo.

Este comando fecha esse buraco de graca. O agregador conhece KaBuM!, Pichau e
Terabyte, que nos coletamos direto. Comparar o que ele diz sobre elas com o que
buscamos nelas mede o erro do agregador **usando dado que ja temos**.

Nao coleta nada para a base e nao envia mensagem: so le e compara.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .collector import monta_adapter
from .db import Repo
from .http import Fetcher
from .models import AppConfig
from .normalize import normaliza
from .notify.render import brl
from .stores.platforms.agregador import AgregadorAdapter, _normaliza_loja

log = logging.getLogger(__name__)

# Acima disso, a base do preco do agregador provavelmente mudou.
DIVERGENCIA_SUSPEITA = 5.0


def executa(cfg: AppConfig, caminho_db: Path) -> int:
    agregadores = [
        (nome, ajustes)
        for nome, ajustes in cfg.stores.items()
        if ajustes.enabled and ajustes.platform == "agregador"
    ]
    if not agregadores:
        print("nenhum agregador habilitado na config")
        return 0
    if not caminho_db.exists():
        print(f"banco nao existe: {caminho_db} -- rode uma coleta primeiro")
        return 1

    print("=" * 72)
    print("AUDITORIA DO AGREGADOR")
    print("=" * 72)
    print(
        "\n  Compara o preco que o agregador atribui a cada loja com o que\n"
        "  coletamos direto nela. Divergencia grande e sinal de que a base do\n"
        "  preco mudou -- e o que geraria alerta falso em massa.\n"
    )

    suspeitas = 0
    with Repo(caminho_db) as repo, Fetcher(
        timeout=cfg.http.timeout_seconds, retries=2
    ) as fetcher:
        for nome, ajustes in agregadores:
            adapter = monta_adapter(nome, cfg)
            if not isinstance(adapter, AgregadorAdapter):
                continue
            suspeitas += _audita_um(repo, fetcher, cfg, nome, ajustes, adapter)

    print()
    if suspeitas:
        print(f"  {suspeitas} divergencia(s) acima de {DIVERGENCIA_SUSPEITA:.0f}%.")
        print("  Confira se o agregador mudou a base do preco antes de confiar")
        print("  nos alertas vindos dele.")
    else:
        print("  Nenhuma divergencia relevante. O preco do agregador segue")
        print("  compativel com o que coletamos direto.")
    print()
    return 1 if suspeitas else 0


def _audita_um(repo, fetcher, cfg, nome, ajustes, adapter) -> int:
    auditaveis = {_normaliza_loja(m) for m in ajustes.merchants_auditoria}
    if not auditaveis:
        print(f"  [{nome}] sem merchants_auditoria na config -- nada a conferir")
        return 0

    suspeitas = 0
    for alvo in cfg.targets:
        if nome not in cfg.stores_for(alvo.category):
            continue

        brutas = []
        for termo in alvo.search_terms:
            try:
                brutas.extend(adapter.ofertas_brutas(termo, fetcher))
            except Exception as e:  # noqa: BLE001
                print(f"  [{nome}/{alvo.id}] falhou: {type(e).__name__}: {e}")
                return suspeitas + 1

        # So as lojas que tambem coletamos direto.
        interesse = [
            b for b in brutas if _normaliza_loja(b.seller_name or "") in auditaveis
        ]
        if not interesse:
            print(f"  [{alvo.id}] o agregador nao citou nenhuma loja auditavel")
            continue

        res = normaliza(interesse, alvo)
        por_loja: dict[str, int] = {}
        for o in res.ofertas:
            chave = _normaliza_loja(o.raw.seller_name or "")
            preco = o.raw.price_cash
            if preco and (chave not in por_loja or preco < por_loja[chave]):
                por_loja[chave] = preco

        print(f"\n  {alvo.id}")
        for chave, preco_agg in sorted(por_loja.items()):
            nosso = _nosso_menor(repo, chave, alvo.id)
            if nosso is None:
                print(f"    {chave:12} agregador {brl(preco_agg):>13}   "
                      f"(sem preco proprio para comparar)")
                continue
            delta = (preco_agg - nosso) / nosso * 100
            marca = "  <-- SUSPEITO" if abs(delta) > DIVERGENCIA_SUSPEITA else ""
            if marca:
                suspeitas += 1
            print(
                f"    {chave:12} agregador {brl(preco_agg):>13} | "
                f"nosso {brl(nosso):>13} | {delta:+6.1f}%{marca}"
            )
    return suspeitas


def _nosso_menor(repo: Repo, loja_normalizada: str, model_key: str) -> int | None:
    """Menor preco a vista disponivel que coletamos direto nessa loja."""
    linha = repo.con.execute(
        """
        SELECT MIN(pp.price_cash)
        FROM product p
        JOIN price_point pp ON pp.product_id = p.id
        WHERE p.model_key = ?
          AND pp.available = 1
          AND pp.price_cash IS NOT NULL
          AND pp.observed_at = (
              SELECT MAX(observed_at) FROM price_point WHERE product_id = p.id
          )
          AND REPLACE(REPLACE(LOWER(p.store), '!', ''), ' ', '') = ?
        """,
        (model_key, loja_normalizada),
    ).fetchone()
    return linha[0] if linha and linha[0] else None
