"""Ponto de entrada: `python -m pricewatcher`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .alerts import avalia
from .collector import coleta
from .config import carrega
from .db import Repo
from .notify import render
from .notify.router import Router
from .notify.telegram import TelegramNotifier
from .tempo import agora_local, fuso

RAIZ = Path(__file__).resolve().parents[2]
log = logging.getLogger("pricewatcher")


class _FormatterComFuso(logging.Formatter):
    """Carimba o log no fuso configurado, com offset explicito.

    Sem isso o horario do log pode nao bater com o horario do agendamento: no
    Windows, TZ com nome IANA nao e entendido pelo runtime C e o logging cai
    para UTC silenciosamente. Quem for investigar "por que nao coletou as 08:00"
    precisa que os dois relogios sejam o mesmo.
    """

    def __init__(self, fmt: str, tz) -> None:
        super().__init__(fmt)
        self._tz = tz

    def formatTime(self, record, datefmt=None) -> str:  # noqa: N802 (API do stdlib)
        from datetime import datetime

        return datetime.fromtimestamp(record.created, self._tz).strftime(
            datefmt or "%Y-%m-%d %H:%M:%S %z"
        )


def _log(nivel: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        _FormatterComFuso("%(asctime)s %(levelname)-7s %(name)-28s %(message)s", fuso())
    )
    raiz = logging.getLogger()
    raiz.handlers[:] = [handler]
    raiz.setLevel(getattr(logging, nivel.upper(), logging.INFO))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pricewatcher")
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--run-once", action="store_true", help="uma coleta e sai")
    modo.add_argument("--serve", action="store_true", help="agendador continuo")
    modo.add_argument("--selftest", action="store_true",
                      help="verifica se o ambiente consegue coletar")
    modo.add_argument("--healthcheck", action="store_true",
                      help="usado pelo HEALTHCHECK do Docker")
    modo.add_argument("--status", action="store_true",
                      help="mostra ultima coleta, aquecimento, precos e agenda")
    modo.add_argument("--test-notify", action="store_true",
                      help="manda uma mensagem de teste aos destinos")

    p.add_argument("--config", type=Path, default=RAIZ / "config.yaml")
    p.add_argument("--env", type=Path, default=RAIZ / ".env")
    p.add_argument("--db", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="avalia e mostra a mensagem, sem enviar")
    p.add_argument("--marcar-teste", action="store_true",
                   help="forca a marca de TESTE MANUAL na mensagem")
    p.add_argument("--run-on-start", action="store_true",
                   help="com --serve, coleta uma vez ao subir")
    p.add_argument("--max-age-hours", type=int, default=24,
                   help="com --healthcheck, idade maxima da ultima coleta")
    p.add_argument("--offline", action="store_true",
                   help="com --selftest, pula a sondagem de rede")
    args = p.parse_args(argv)

    caminho_db = args.db or Path(
        os.environ.get("DB_PATH") or RAIZ / "data" / "prices.db"
    )

    # Healthcheck e chamado a todo instante pelo Docker: sem log verboso,
    # sem carregar config, sem exigir segredo.
    if args.healthcheck:
        from .selftest import healthcheck
        return healthcheck(caminho_db, args.max_age_hours)

    # --status e --selftest sao somente-leitura: nao devem exigir os segredos
    # do Telegram so para responder se a ultima coleta rodou.
    somente_leitura = args.status or args.selftest
    cfg = carrega(args.config, env=args.env, estrito=not somente_leitura)
    _log(os.environ.get("LOG_LEVEL", "INFO"))

    if args.selftest:
        from .selftest import executa
        return executa(cfg, rede=not args.offline)

    if args.status:
        from .status import executa as mostra_status
        return mostra_status(cfg, caminho_db)

    if args.test_notify:
        return _teste_notificacao(cfg)

    if args.run_once:
        return _rodada(
            cfg, caminho_db,
            dry_run=args.dry_run,
            marcar_teste=args.marcar_teste or None,
        )

    if args.serve:
        return _serve(cfg, caminho_db, args)

    # Sem queda livre: um modo novo que esqueca de ser tratado aqui vira erro,
    # nao vira --serve por acidente.
    raise AssertionError("modo nao tratado -- confira os ifs acima")


# ----------------------------------------------------------------- execucao
def _rodada(
    cfg,
    caminho_db: Path,
    dry_run: bool = False,
    marcar_teste: bool | None = None,
) -> int:
    """`marcar_teste=None` deixa a origem ser detectada (ver e_execucao_manual)."""
    with Repo(caminho_db) as repo:
        resumo = coleta(cfg, repo)
        alertas = avalia(
            repo, resumo.produtos, cfg.alerts, alvos={t.id: t for t in cfg.targets}
        )
        _relatorio(repo, resumo, alertas, caminho_db)

        marca = e_execucao_manual() if marcar_teste is None else marcar_teste
        if dry_run:
            if alertas:
                print("--- mensagem que seria enviada ---")
                print(render.digest(alertas, teste=marca))
                print()
        else:
            _notifica(cfg, repo, resumo, alertas, teste=marca)

    return 1 if resumo.falhas else 0


def _serve(cfg, caminho_db: Path, args) -> int:
    from .scheduler import monta, proximas

    def tarefa() -> None:
        log.info("--- coleta agendada iniciando ---")
        try:
            _rodada(cfg, caminho_db)
        except Exception:  # noqa: BLE001 -- o agendador nao pode morrer por isso
            log.exception("coleta agendada falhou por completo")

    sched = monta(cfg.schedule, tarefa)

    if args.run_on_start:
        tarefa()

    for nome, quando in proximas(sched):
        # "estimada" de proposito: o jitter e re-sorteado a cada disparo, entao
        # o horario real desliza dentro da janela. Dizer um horario exato aqui
        # faz quem le o log achar que o agendador atrasou ou adiantou.
        log.info("proxima execucao de %r: ~%s (jitter re-sorteado a cada disparo)",
                 nome, quando)

    log.info("agendador no ar; Ctrl+C para sair")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("encerrando")
    return 0


# ------------------------------------------------------------- notificacao
def e_execucao_manual() -> bool:
    """True quando NAO estamos rodando dentro do container.

    O Dockerfile define PRICEWATCHER_ORIGEM=container. Preferimos detectar em
    vez de depender de alguem lembrar da flag: sem a marca, um teste manual
    fica indistinguivel de uma oportunidade real para quem le o grupo.
    """
    return os.environ.get("PRICEWATCHER_ORIGEM") != "container"


def _transporte():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ausente -- confira o .env")
    return TelegramNotifier(token)


def _notifica(cfg, repo: Repo, resumo, alertas, teste: bool | None = None) -> None:
    try:
        router = Router(cfg.notify, _transporte())
    except Exception as e:  # noqa: BLE001
        log.error("notificacao indisponivel: %s", e)
        return

    if alertas:
        marca = e_execucao_manual() if teste is None else teste
        entregues = router.envia_precos(alertas, agora_local(), teste=marca)
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


# ---------------------------------------------------------------- relatorio
def _relatorio(repo: Repo, resumo, alertas, caminho_db: Path) -> None:
    print()
    print("=" * 72)
    print(f"COLETA CONCLUIDA -- {agora_local():%d/%m/%Y %H:%M %Z}")
    print(f"  banco: {caminho_db}")
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
