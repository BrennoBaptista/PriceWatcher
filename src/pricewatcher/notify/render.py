"""Montagem das mensagens.

Separado do transporte de proposito: trocar Telegram por ntfy/Matrix nao deveria
mexer em formatacao, e testar texto nao deveria exigir rede.
"""

from __future__ import annotations

import html
from datetime import datetime

from ..alerts import Alerta

LOJAS = {
    "kabum": "KaBuM!",
    "pichau": "Pichau",
    "terabyte": "Terabyteshop",
}


def brl(centavos: int | None) -> str:
    """Formata em real brasileiro: 519999 -> 'R$ 5.199,99'."""
    if centavos is None:
        return "—"
    inteiro, resto = divmod(abs(centavos), 100)
    milhar = f"{inteiro:,}".replace(",", ".")
    sinal = "-" if centavos < 0 else ""
    return f"{sinal}R$ {milhar},{resto:02d}"


def _loja(nome: str) -> str:
    return LOJAS.get(nome, nome.capitalize())


def _bloco(a: Alerta) -> str:
    linhas = []

    if a.novo_minimo and a.voltou_ao_estoque:
        linhas.append("📉📦 <b>NOVO MÍNIMO — E VOLTOU AO ESTOQUE</b>")
    elif a.novo_minimo:
        linhas.append("📉 <b>NOVO MÍNIMO</b>")
    else:
        linhas.append("📦 <b>VOLTOU AO ESTOQUE</b>")

    titulo = html.escape(a.titulo)
    linhas.append(titulo if len(titulo) <= 90 else titulo[:89] + "…")

    if a.is_bundle and a.bundle_note:
        linhas.append(f"🎁 bundle: {html.escape(a.bundle_note)}")

    preco = f"<b>{brl(a.preco)}</b> à vista"
    if a.novo_minimo and a.melhor_anterior:
        # Decimal com virgula: o resto da mensagem e em pt-BR.
        pct = f"{a.queda_percentual or 0:.1f}".replace(".", ",")
        preco += f"  (antes: {brl(a.melhor_anterior)} · -{pct}%)"
    linhas.append(preco)

    linhas.append(f'{_loja(a.loja)} → <a href="{html.escape(a.url, quote=True)}">ver</a>')
    return "\n".join(linhas)


def digest(alertas: list[Alerta], quando: datetime | None = None) -> str:
    """Uma mensagem por rodada, nunca uma por alerta (secao 7)."""
    quando = quando or datetime.now()
    n = len(alertas)
    palavra = "oportunidade" if n == 1 else "oportunidades"
    cabecalho = f"🟢 <b>{n} {palavra}</b> — {quando:%d/%m %H:%M}"
    return "\n\n".join([cabecalho, *(_bloco(a) for a in alertas)])


def operacional(falhas: list[tuple[str, str, str]], vazios: list[str]) -> str:
    """Aviso de coletor quebrado. Vai para o privado, nao para o grupo."""
    partes = ["⚠️ <b>PriceWatcher — problema na coleta</b>"]
    for loja, alvo, erro in falhas:
        partes.append(
            f"• <b>{_loja(loja)}</b> / {html.escape(alvo)} falhando há 2 rodadas\n"
            f"  <code>{html.escape(erro[:180])}</code>"
        )
    for v in vazios:
        partes.append(f"• <b>{html.escape(v)}</b>: zero ofertas válidas — parser quebrado?")
    return "\n\n".join(partes)
