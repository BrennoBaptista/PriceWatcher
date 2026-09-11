"""Relatorio de situacao: `python -m pricewatcher --status`.

Existe por uma friccao real. Para responder "a ultima coleta rodou?" foi preciso
abrir o SQLite na mao e cruzar `collection_run` com `price_point` -- justamente
no momento em que a duvida era se o agendador estava funcionando.

Nao toca a rede e nao escreve nada: e seguro rodar a qualquer hora.
"""

from __future__ import annotations

from pathlib import Path

from .db import Repo
from .models import AppConfig
from .notify.render import brl
from .tempo import agora_local, formata, para_local

REGUA = "=" * 68


def _cabecalho(titulo: str) -> None:
    print(f"\n{titulo}")
    print("-" * len(titulo))


def executa(cfg: AppConfig, caminho_db: Path) -> int:
    print(REGUA)
    print(f"PRICEWATCHER -- situacao em {agora_local():%d/%m/%Y %H:%M %Z}")
    print(REGUA)

    if not caminho_db.exists():
        print(f"\n  banco ainda nao existe: {caminho_db}")
        print("  rode uma coleta primeiro (--run-once)")
        return 1

    with Repo(caminho_db) as repo:
        _coletas(repo)
        _aquecimento(repo, cfg)
        _precos(repo)
        _alertas(repo)
    _agenda(cfg)
    print()
    return 0


def _coletas(repo: Repo) -> None:
    _cabecalho("Ultima coleta por loja e alvo")
    linhas = repo.con.execute(
        """
        SELECT store, target_id, status, offers_found, offers_kept,
               MAX(finished_at) AS quando
        FROM collection_run
        WHERE finished_at IS NOT NULL
        GROUP BY store, target_id
        ORDER BY store, target_id
        """
    ).fetchall()
    if not linhas:
        print("  nenhuma coleta concluida ainda")
        return

    for ln in linhas:
        marca = {"ok": "OK  ", "partial": "PARC", "failed": "FALHA"}.get(
            ln["status"], ln["status"]
        )
        print(
            f"  {marca:5} {ln['store']:<11} {ln['target_id']:<13} "
            f"{formata(ln['quando'], '%d/%m %H:%M'):>12}  "
            f"{ln['offers_found'] or 0:>4} brutas -> {ln['offers_kept'] or 0:>3}"
        )

    ruins = repo.falhas_consecutivas(minimo=2)
    if ruins:
        print("\n  ATENCAO -- falhando ha 2 rodadas seguidas:")
        for loja, alvo, erro in ruins:
            print(f"    {loja}/{alvo}: {erro[:60]}")


def _aquecimento(repo: Repo, cfg: AppConfig) -> None:
    """Quantos produtos ja podem disparar alerta de minimo historico."""
    minimo = cfg.alerts.new_low.min_observations
    _cabecalho(f"Aquecimento (alerta exige {minimo} observacoes anteriores)")

    dist = dict(
        repo.con.execute(
            "SELECT n, COUNT(*) FROM (SELECT product_id, COUNT(*) n "
            "FROM price_point GROUP BY product_id) GROUP BY n"
        ).fetchall()
    )
    total = sum(dist.values())
    if not total:
        print("  nenhuma observacao ainda")
        return

    prontos = sum(q for n, q in dist.items() if n > minimo)
    print(f"  observacoes por produto: {dict(sorted(dist.items()))}")
    print(f"  produtos aptos a alertar: {prontos} de {total}")
    if prontos == 0:
        faltam = minimo + 1 - max(dist)
        print(f"  faltam ~{faltam} coleta(s) -- silencio ate la e o esperado")


def _precos(repo: Repo) -> None:
    _cabecalho("Menor preco a vista disponivel")
    linhas = repo.resumo()
    if not linhas:
        print("  sem dados")
        return
    atual = None
    for ln in linhas:
        chave = (ln["category"], ln["model_key"])
        if chave != atual:
            atual = chave
            print(f"\n  {ln['category']}/{ln['model_key']}")
        menor = ln["menor_disponivel"]
        valor = brl(menor) if menor else "(sem estoque)"
        print(f"    {ln['store']:<12} {valor:>14}   {ln['anuncios']:>3} anuncios")


def _alertas(repo: Repo) -> None:
    _cabecalho("Alertas enviados")
    linhas = repo.con.execute(
        """
        SELECT a.kind, a.price_cash, a.previous_best, a.sent_at, a.delivered,
               p.store, p.title_raw
        FROM alert a JOIN product p ON p.id = a.product_id
        ORDER BY a.sent_at DESC LIMIT 10
        """
    ).fetchall()
    if not linhas:
        print("  nenhum ate agora")
        return
    for ln in linhas:
        entregue = "" if ln["delivered"] else "  (NAO ENTREGUE)"
        print(
            f"  {formata(ln['sent_at'], '%d/%m %H:%M')}  {ln['kind']:<14} "
            f"{brl(ln['price_cash']):>13}  {ln['store']:<11} "
            f"{ln['title_raw'][:34]}{entregue}"
        )


def _agenda(cfg: AppConfig) -> None:
    _cabecalho("Proximas coletas")
    from .scheduler import proximas_execucoes

    for nome, quando in proximas_execucoes(cfg.schedule):
        local = para_local(quando)
        print(f"  {nome:<16} {local:%d/%m %H:%M %Z}" if local else f"  {nome}: ?")
    print(f"  (jitter de ate {cfg.schedule.jitter_minutes} min a cada disparo)")
