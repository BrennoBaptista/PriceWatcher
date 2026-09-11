"""Ponto de entrada: `python -m pricewatcher`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .collector import coleta
from .config import carrega
from .db import Repo

RAIZ = Path(__file__).resolve().parents[2]


def _log(nivel: str) -> None:
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pricewatcher")
    p.add_argument("--run-once", action="store_true",
                   help="executa uma coleta e sai")
    p.add_argument("--serve", action="store_true",
                   help="roda o agendador continuamente (Fase 3)")
    p.add_argument("--config", type=Path, default=RAIZ / "config.yaml")
    p.add_argument("--env", type=Path, default=RAIZ / ".env")
    p.add_argument("--db", type=Path, default=None)
    args = p.parse_args(argv)

    if not (args.run_once or args.serve):
        p.error("escolha --run-once ou --serve")

    cfg = carrega(args.config, env=args.env)
    _log(os.environ.get("LOG_LEVEL", "INFO"))

    if args.serve:
        print("--serve chega na Fase 3 (agendador). Use --run-once.", file=sys.stderr)
        return 2

    caminho_db = args.db or Path(os.environ.get("DB_PATH") or RAIZ / "data" / "prices.db")
    with Repo(caminho_db) as repo:
        resumo = coleta(cfg, repo)
        _relatorio(repo, resumo, caminho_db)

    if resumo.falhas:
        return 1
    return 0


def _relatorio(repo: Repo, resumo, caminho_db: Path) -> None:
    print()
    print("=" * 72)
    print(f"COLETA CONCLUIDA -- {caminho_db}")
    print("=" * 72)
    print(f"  ofertas encontradas : {resumo.encontradas}")
    print(f"  ofertas mantidas    : {resumo.mantidas}")

    if resumo.falhas:
        print(f"\n  FALHAS ({len(resumo.falhas)}):")
        for f in resumo.falhas:
            print(f"    - {f}")
    if resumo.vazias:
        print(f"\n  ZERO VALIDOS ({len(resumo.vazias)}) -- possivel parser quebrado:")
        for v in resumo.vazias:
            print(f"    - {v}")

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
            valor = f"R$ {menor/100:>9,.2f}" if menor else "  (sem estoque)"
            print(f"      {ln['store']:<10} {valor}   ({ln['anuncios']} anuncios)")

    print()
    print("  Base:", ", ".join(f"{k}={v}" for k, v in repo.contagens().items()))
    print()


if __name__ == "__main__":
    raise SystemExit(main())
