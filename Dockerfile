# PriceWatcher -- imagem unica, servico de vida longa.
#
# Alvo: Ubuntu 24.04 amd64 num Core 2 Duo (secao 12 da SPEC). CPU x86-64
# baseline, sem SSE4.2/AVX -- por isso NAO ha navegador headless aqui, e por
# isso o `--selftest` existe: ele diz em uma linha se o binario compilado do
# curl_cffi roda nesse processador.

# ---------- build ----------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

# ---------- runtime ----------
FROM python:3.12-slim

# curl fica instalado de proposito: e o plano B documentado na secao 11.2 se o
# binario do curl_cffi nao rodar no CPU do servidor.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 watcher

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/prices.db \
    TZ=America/Sao_Paulo

WORKDIR /app
COPY config.yaml ./config.yaml

RUN mkdir -p /data && chown -R watcher:watcher /data /app
USER watcher
VOLUME ["/data"]

# Saudavel = houve coleta bem-sucedida nas ultimas 24h. Container recem-subido
# ainda nao coletou, e isso nao conta como doenca.
HEALTHCHECK --interval=15m --timeout=30s --start-period=2m --retries=3 \
    CMD ["python", "-m", "pricewatcher", "--healthcheck", "--config", "/app/config.yaml"]

ENTRYPOINT ["python", "-m", "pricewatcher", "--config", "/app/config.yaml"]
CMD ["--serve"]
