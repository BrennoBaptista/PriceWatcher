"""Titulo bruto -> oferta normalizada, ou descarte com motivo.

O normalizador e o unico lugar que decide se um anuncio entra na base. Os adapters
so traduzem formato; o filtro que vale esta aqui.

Motivo do descarte importa: se um adapter comecar a descartar 100% por mudanca de
layout, queremos ver isso nos logs antes de estranhar o silencio dos alertas.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from .models import Category, NormalizedOffer, RawOffer, SellerType, Target

log = logging.getLogger(__name__)


class Descarte(StrEnum):
    NAO_CASA = "nao_casa_o_alvo"
    EXCLUIDO = "casou_lista_de_exclusao"
    SEM_PRECO = "sem_preco_a_vista"
    FORA_DA_FAIXA = "preco_fora_da_faixa_de_sanidade"
    NAO_1P = "vendedor_nao_e_a_propria_loja"
    VENDEDOR_DESCONHECIDO = "vendedor_indeterminado"


# Fabricantes conhecidos, do mais especifico para o mais generico.
MARCAS = [
    "ASRock", "PowerColor", "Sapphire", "Gigabyte", "Gainward", "Inno3D",
    "Colorful", "Biostar", "Yeston", "Zotac", "Galax", "Palit", "MSI",
    "ASUS", "XFX", "PNY", "EVGA", "Sony", "Microsoft", "Nintendo",
]
_MARCAS_RE = [(m, re.compile(rf"(?i)\b{re.escape(m)}\b")) for m in MARCAS]

# Ruido comum nos titulos, removido para sobrar a linha do modelo.
_RUIDO = re.compile(
    r"(?i)\b(placa\s+de\s+v[ií]deo|placa\s+de\s+video|console|gpu|vga|"
    r"\d+\s*gb|gddr\d+x?|\d+\s*bits?|\d+\s*bit|ray\s*tracing|fsr|dlss\s*\d*|"
    r"amd|nvidia|radeon|geforce|rx|rtx)\b"
)

# Sinais de que o anuncio e um combo.
# Jogos que aparecem em combo de console no varejo brasileiro. A lista existe
# porque muitos titulos listam o jogo sem nenhuma palavra-chave: "Console
# Playstation 5 God Of War Ragnarok 825GB Sony" e um bundle, e nao tem "+",
# nem "com", nem "bundle".
_JOGOS = (
    r"god\s+of\s+war|gran\s+turismo|astro\s*bot|horizon|spider[\s-]*man"
    r"|returnal|ratchet|ea\s+(sports\s+)?fc|fifa|call\s+of\s+duty"
    r"|last\s+of\s+us|ghost\s+of|miles\s+morales|hogwarts|mortal\s+kombat"
)

# Cuidado com o numero de controles: TODO PS5 vem com um. "1 Controle" e o
# conteudo padrao da caixa; "2 controles" ou "controle extra" e que sao combo.
_BUNDLE = re.compile(
    r"(?i)("
    r"\+"
    r"|\bbundle\b|\bcombo\b|\bacompanha\b"
    # Qualquer mencao a jogo incluso, com ou sem "com": "2 Jogos", "com jogo".
    r"|\bjogos?\b"
    r"|\bcontrole\s+extra\b"
    r"|\b[2-9]\s*[ºo°]?\s*controles?\b"
    r"|\bedi[çc][ãa]o\s+bundle\b"
    rf"|{_JOGOS}"
    r")"
)


@dataclass
class Resultado:
    ofertas: list[NormalizedOffer] = field(default_factory=list)
    descartes: Counter[str] = field(default_factory=Counter)

    @property
    def total_descartado(self) -> int:
        return sum(self.descartes.values())


def _compilados(padroes: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in padroes]


def _marca(titulo: str) -> str | None:
    for nome, padrao in _MARCAS_RE:
        if padrao.search(titulo):
            return nome
    return None


def _linha_modelo(titulo: str, marca: str | None) -> str | None:
    texto = titulo
    if marca:
        texto = re.sub(rf"(?i)\b{re.escape(marca)}\b", " ", texto)
    texto = _RUIDO.sub(" ", texto)
    texto = re.sub(r"[,\-–—/]+", " ", texto)
    texto = re.sub(r"\b\d{3,}\b", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:60] or None


def _variante(titulo: str, target: Target) -> str:
    """Classifica a variante. A ordem das regras importa: primeiro match vence."""
    padrao_default = None
    for v in target.variants:
        if v.default:
            padrao_default = v.key
            continue
        if v.match_regex and re.search(v.match_regex, titulo):
            return v.key
    return padrao_default or target.id


def _bundle(titulo: str, target: Target) -> tuple[bool, str | None]:
    if not target.include_bundles:
        return False, None
    m = _BUNDLE.search(titulo)
    if not m:
        return False, None
    # O que vem depois do sinal de combo costuma ser o conteudo extra.
    resto = titulo[m.end():].strip(" ,-–—")
    return True, (resto[:80] or None)


def normaliza(
    brutas: list[RawOffer], target: Target, somente_1p: bool = True
) -> Resultado:
    res = Resultado()

    match_re = re.compile(target.match_regex) if target.match_regex else None
    req_all = _compilados(target.require_all)
    req_any = _compilados(target.require_any)
    excluir = _compilados(target.exclude)
    piso, teto = target.sanity_range_cents

    for bruta in brutas:
        titulo = bruta.title_raw

        if match_re and not match_re.search(titulo):
            res.descartes[Descarte.NAO_CASA] += 1
            continue
        if req_all and not all(p.search(titulo) for p in req_all):
            res.descartes[Descarte.NAO_CASA] += 1
            continue
        if req_any and not any(p.search(titulo) for p in req_any):
            res.descartes[Descarte.NAO_CASA] += 1
            continue
        if excluir and any(p.search(titulo) for p in excluir):
            res.descartes[Descarte.EXCLUIDO] += 1
            continue

        # Falha fechada: vendedor indeterminado nunca entra (secao 5).
        if somente_1p:
            if bruta.seller_type is SellerType.UNKNOWN:
                res.descartes[Descarte.VENDEDOR_DESCONHECIDO] += 1
                continue
            if bruta.seller_type is not SellerType.FIRST_PARTY:
                res.descartes[Descarte.NAO_1P] += 1
                continue

        if bruta.price_cash is None:
            # Indisponivel sem preco e o estado NORMAL de um item esgotado -- a
            # VTEX, por exemplo, nem envia `Installments` nesse caso. Precisamos
            # registrar essa observacao: sem ela nao existe a transicao
            # indisponivel -> disponivel, e o alerta de volta ao estoque nunca
            # dispararia. Descartar so faz sentido quando o item esta a venda e
            # mesmo assim veio sem preco, o que ai sim e parser quebrado.
            if bruta.available:
                res.descartes[Descarte.SEM_PRECO] += 1
                continue
        elif not (piso <= bruta.price_cash <= teto):
            log.warning(
                "[%s] preco fora da faixa de sanidade: R$ %.2f em %r (%s)",
                bruta.store, bruta.price_cash / 100, titulo[:60], bruta.url,
            )
            res.descartes[Descarte.FORA_DA_FAIXA] += 1
            continue

        marca = _marca(titulo)
        is_bundle, nota = _bundle(titulo, target)
        res.ofertas.append(
            NormalizedOffer(
                raw=bruta,
                category=target.category,
                model_key=_variante(titulo, target),
                brand=marca,
                model_line=_linha_modelo(titulo, marca),
                is_bundle=is_bundle,
                bundle_note=nota,
            )
        )

    return res
