"""Guarda contra dependencia usada mas nao declarada.

Este teste existe por um erro real: ao trocar httpx por curl_cffi, o venv local
foi atualizado mas o pyproject.toml nao. Tudo passava aqui, e o container
subia sem o pacote -- o erro so apareceu no servidor, no primeiro deploy.

Testar em venv que ja tem tudo instalado nao pega isso. Comparar codigo com
manifesto, pega.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SRC = RAIZ / "src"

# Modulo importado -> pacote no indice. So precisa entrar aqui quem difere.
DISTRIBUICAO = {
    "apscheduler": "apscheduler",
    "yaml": "pyyaml",
    "curl_cffi": "curl-cffi",
}

STDLIB = {
    "__future__", "argparse", "collections", "dataclasses", "datetime", "enum",
    "gzip", "html", "importlib", "io", "json", "logging", "os", "pathlib",
    "platform", "random", "re", "sqlite3", "subprocess", "sys", "time",
    "tomllib", "typing", "urllib", "zipfile", "zlib", "zoneinfo",
}

IMPORT = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.M)


def _normaliza(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def _usados() -> set[str]:
    usados: set[str] = set()
    for arquivo in SRC.rglob("*.py"):
        for modulo in IMPORT.findall(arquivo.read_text(encoding="utf-8")):
            if modulo in STDLIB or modulo == "pricewatcher":
                continue
            usados.add(_normaliza(DISTRIBUICAO.get(modulo, modulo)))
    return usados


def _declarados() -> set[str]:
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    deps = dados["project"]["dependencies"]
    return {
        _normaliza(re.split(r"[<>=!~\[;\s]", d.strip(), maxsplit=1)[0])
        for d in deps
    }


def test_tudo_que_o_codigo_importa_esta_declarado():
    faltando = _usados() - _declarados()
    assert not faltando, (
        f"pacote usado em src/ mas ausente do pyproject.toml: {sorted(faltando)}. "
        "O container vai subir sem ele."
    )


def test_nao_ha_dependencia_declarada_sem_uso():
    """Peso morto na imagem, e pista falsa para quem for ler o manifesto."""
    sobrando = _declarados() - _usados()
    assert not sobrando, (
        f"declarado no pyproject.toml mas nao usado em src/: {sorted(sobrando)}"
    )


def test_curl_cffi_e_a_dependencia_http():
    """Regressao: httpx nao passa no WAF da Pichau e da Terabyte (secao 11.2)."""
    declarados = _declarados()
    assert "curl-cffi" in declarados
    assert "httpx" not in declarados, "httpx leva 403 nessas lojas -- ver secao 11.2"
