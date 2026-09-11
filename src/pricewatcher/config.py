"""Carga e validacao da configuracao.

Segredos vem do ambiente (.env); comportamento vem do config.yaml. O YAML pode
referenciar variaveis de ambiente com ${NOME}, resolvidas no carregamento.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .models import AppConfig

_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expande_env(valor):
    """Troca ${VAR} pelo valor do ambiente, recursivamente."""
    if isinstance(valor, str):
        def troca(m: re.Match[str]) -> str:
            nome = m.group(1)
            v = os.environ.get(nome)
            if v is None:
                raise ValueError(
                    f"config.yaml referencia ${{{nome}}}, mas a variavel nao esta definida. "
                    f"Confira o .env."
                )
            return v
        return _ENV_REF.sub(troca, valor)
    if isinstance(valor, dict):
        return {k: _expande_env(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_expande_env(v) for v in valor]
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


def carrega(caminho: Path, env: Path | None = None) -> AppConfig:
    if env is not None:
        carrega_env(env)
    if not caminho.is_file():
        raise FileNotFoundError(f"config nao encontrado: {caminho}")
    bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(_expande_env(bruto))
