"""Carga e validacao da configuracao.

Segredos vem do ambiente (.env); comportamento vem do config.yaml. O YAML pode
referenciar variaveis de ambiente com ${NOME}, resolvidas no carregamento.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from .models import AppConfig

log = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expande_env(valor, faltantes: set[str]):
    """Troca ${VAR} pelo valor do ambiente, recursivamente.

    Variavel ausente vira string vazia e entra em `faltantes`. Quem decide se
    isso e fatal e `carrega`, porque depende do modo: coletar e notificar
    exigem os segredos, ler o status nao.
    """
    if isinstance(valor, str):
        def troca(m: re.Match[str]) -> str:
            nome = m.group(1)
            v = os.environ.get(nome)
            if v is None:
                faltantes.add(nome)
                return ""
            return v
        return _ENV_REF.sub(troca, valor)
    if isinstance(valor, dict):
        return {k: _expande_env(v, faltantes) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_expande_env(v, faltantes) for v in valor]
    return valor


def carrega_env(caminho: Path) -> None:
    """Le um .env simples para os.environ, sem sobrescrever o que ja existe."""
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip())


def carrega(caminho: Path, env: Path | None = None, estrito: bool = True) -> AppConfig:
    """Carrega e valida a config.

    `estrito=True` (padrao) falha se o YAML referenciar variavel de ambiente
    ausente -- e o que queremos ao coletar ou subir o agendador, para o problema
    aparecer no boot e nao na hora de mandar a mensagem.

    `estrito=False` tolera a ausencia. Usado por `--status`, `--selftest` e
    `--healthcheck`: sao somente-leitura e nao deveriam exigir os segredos do
    Telegram para responder "a ultima coleta rodou?".
    """
    if env is not None:
        carrega_env(env)
    if not caminho.is_file():
        raise FileNotFoundError(f"config nao encontrado: {caminho}")
    bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}

    faltantes: set[str] = set()
    dados = _expande_env(bruto, faltantes)
    if faltantes:
        nomes = ", ".join(sorted(faltantes))
        if estrito:
            raise ValueError(
                f"config.yaml referencia variavel(is) de ambiente nao definida(s): "
                f"{nomes}. Confira o .env."
            )
        log.warning("variavel(is) de ambiente ausente(s), seguindo vazio: %s", nomes)
    return AppConfig.model_validate(dados)
