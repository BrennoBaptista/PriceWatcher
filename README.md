# PriceWatcher

Monitor de preços no varejo brasileiro, com alerta no Telegram quando um anúncio bate o
menor preço já visto ou volta ao estoque.

Roda como um único container Docker num servidor pessoal.

| Fase | Produto | Lojas |
|---|---|---|
| v1 | Placas de vídeo **RX 9070 XT** e **RTX 5070 Ti** | Kabum, Pichau, Terabyteshop |
| Fase 5 | Console **PlayStation 5** (todas as variantes e bundles) | Casas Bahia, Ponto, Magalu, Americanas, Casa e Vídeo |

## Status

✅ **Fase 0** — extração das três lojas validada contra os sites reais.
✅ **Fase 1** — núcleo: coleta real persistida em SQLite.
✅ **Fase 2** — alertas e Telegram: digest único por rodada, com guardrails.
🟡 **Fase 3** — container e agendador escritos; **deploy no servidor pendente**.

72 testes, todos offline.

O planejamento completo está em **[SPEC.md](SPEC.md)**.

## Rodando

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
```

Uma coleta, gravando em `data/prices.db` e notificando:

```bash
.venv/Scripts/python -m pricewatcher --run-once
```

Sem enviar nada, só mostrando o que seria enviado:

```bash
.venv/Scripts/python -m pricewatcher --run-once --dry-run
```

Testar só os canais do Telegram:

```bash
.venv/Scripts/python -m pricewatcher --test-notify
```

Verificar se o ambiente consegue coletar (roda isto **antes** de subir o serviço
num servidor novo):

```bash
.venv/Scripts/python -m pricewatcher --selftest
```

## Deploy

```bash
cp .env.example .env && docker compose build
```

Confira o ambiente antes de deixar no ar:

```bash
docker compose run --rm pricewatcher --selftest
```

Se passou, sobe o agendador:

```bash
docker compose up -d
```

Os testes não tocam a rede — rodam contra respostas reais congeladas em
`tests/fixtures/`:

```bash
.venv/Scripts/python -m pytest -q
```

## Como vai funcionar

| | |
|---|---|
| Frequência | 2x/dia (08:00 e 20:00, `America/Sao_Paulo`) |
| Preço de referência | À vista (PIX/boleto) |
| Alertas | Novo mínimo histórico · Volta ao estoque |
| Destinos | Alertas de preço no grupo; alertas operacionais no privado |
| Persistência | SQLite em volume Docker |

## Setup (quando houver código)

```bash
cp .env.example .env   # preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
docker compose up -d
```

O `.env` nunca é versionado. Para descobrir o `TELEGRAM_CHAT_ID`, envie uma mensagem
ao bot e leia `result[].message.chat.id` em
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Roadmap

Ver seção 15 da [SPEC.md](SPEC.md).
