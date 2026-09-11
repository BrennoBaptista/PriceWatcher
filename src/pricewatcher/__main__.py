"""Ponto de entrada: `python -m pricewatcher`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .alerts import avalia
from .collector import coleta
from .config import carrega
from .db import Repo
from .notify import render
from .notify.router import Router
from .notify.telegram import TelegramNotifier

RAIZ = Path(__file__).resolve().parents[2]
log = logging.getLogger("pricewatcher")


def _log(nivel: str) -> None:
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pricewatcher")
    p.add_argument("--run-once", action="store_true", help="executa uma coleta e sai")
    p.add_argument("--serve", action="store_true",
                   help="roda o agendador continuamente (Fase 3)")
    p.add_argument("--config", type=Path, default=RAIZ / "config.yaml")
    p.add_argument("--env", type=Path, default=RAIZ / ".env")
    p.add_argument("--db", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="avalia os alertas e mostra a mensagem, sem enviar nada")
    p.add_argument("--test-notify", action="store_true",
                   help="so envia uma mensagem de teste aos destinos e sai")
    args = p.parse_args(argv)

    if not (args.run_once or args.serve or args.test_notify):
        p.error("escolha --run-once, --serve ou --test-notify")

    cfg = carrega(args.config, env=args.env)
    _log(os.environ.get("LOG_LEVEL", "INFO"))

    if args.test_notify:
        return _teste_notificacao(cfg)

    if args.serve:
        print("--serve chega na Fase 3 (agendador). Use --run-once.", file=sys.stderr)
        return 2

    caminho_db = args.db or Path(os.environ.get("DB_PATH") or RAIZ / "data" / "prices.db")
    with Repo(caminho_db) as repo:
        resumo = coleta(cfg, repo)
        alertas = avalia(
            repo, resumo.produtos, cfg.alerts,
            alvos={t.id: t for t in cfg.targets},
        )
        _relatorio(repo, resumo, alertas, caminho_db)

        if args.dry_run:
            if alertas:
                print("--- mensagem que seria enviada ---")
                print(render.digest(alertas))
                print()
        else:
            _notifica(cfg, repo, resumo, alertas)

    return 1 if resumo.falhas else 0


def _transporte():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ausente -- confira o .env")
    return TelegramNotifier(token)


def _notifica(cfg, repo: Repo, resumo, alertas) -> None:
    try:
        router = Router(cfg.notify, _transporte())
    except Exception as e:  # noqa: BLE001
        log.error("notificacao indisponivel: %s", e)
        return

    if alertas:
        entregues = router.envia_precos(alertas, datetime.now())
        houve_entrega = any(entregues.values())
        for a in alertas:
            for kind in a.kinds:
                repo.registra_alerta(
                    a.produto_id, kind, a.preco, a.melhor_anterior, houve_entrega
                )

    falhas = repo.falhas_consecutivas(minimo=2)
    if falhas or resumo.vazias:
        router.envia_operacional(falhas, resumo.vazias)


def _teste_notificacao(cfg) -> int:
    router = Router(cfg.notify, _transporte())
    texto = (
        "✅ <b>PriceWatcher</b> — teste de canal.\n\n"
        "Se esta mensagem chegou com acentos corretos (mínimo, R$ 5.199,99), "
        "o encoding está certo."
    )
    ok = True
    for dest in cfg.notify.destinations:
        resultado = router._entrega(dest, texto)
        print(f"  {dest.id:10} {'OK' if resultado else 'FALHOU'}")
        ok = ok and resultado
    return 0 if ok else 1


def _relatorio(repo: Repo, resumo, alertas, caminho_db: Path) -> None:
    print()
    print("=" * 72)
    print(f"COLETA CONCLUIDA -- {caminho_db}")
    print("=" * 72)
    print(f"  ofertas encontradas : {resumo.encontradas}")
    print(f"  ofertas mantidas    : {resumo.mantidas}")
    print(f"  alertas disparados  : {len(alertas)}")

    if resumo.falhas:
        print(f"\n  FALHAS ({len(resumo.falhas)}):")
        for f in resumo.falhas:
            print(f"    - {f}")
    if resumo.vazias:
        print(f"\n  ZERO VALIDOS ({len(resumo.vazias)}) -- possivel parser quebrado:")
        for v in resumo.vazias:
            print(f"    - {v}")

    if alertas:
        print("\n  Alertas:")
        for a in alertas:
            tipo = "+".join(a.kinds)
            extra = f" (antes {render.brl(a.melhor_anterior)})" if a.melhor_anterior else ""
            print(f"    [{tipo}] {render.brl(a.preco)}{extra}  {a.loja:<9} {a.titulo[:44]}")

    linhas = repo.resumo()
    if linhas:
        print("\n  Menor preco a vista disponivel, por alvo e loja:")
        atual = None
        for ln in linhas:
            chave = (ln["category"], ln["model_key"])
            if chave != atual:
                atual = chave
                print(f"\n    {ln['category']}/{ln['model_key']}")
            menor = ln["menor_disponivel"]
            valor = render.brl(menor) if menor else "(sem estoque)"
            print(f"      {ln['store']:<10} {valor:>14}   ({ln['anuncios']} anuncios)")

    print()
    print("  Base:", ", ".join(f"{k}={v}" for k, v in repo.contagens().items()))
    print()


if __name__ == "__main__":
    raise SystemExit(main())
