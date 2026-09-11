# PriceWatcher

Monitor de preços no varejo brasileiro, com alerta no Telegram quando um anúncio bate o
menor preço já visto ou volta ao estoque.

Roda como um único container Docker num servidor pessoal.

| Fase | Produto | Lojas |
|---|---|---|
| v1 | Placas de vídeo **RX 9070 XT** e **RTX 5070 Ti** | Kabum, Pichau, Terabyteshop |
| Fase 5 | Console **PlayStation 5** (todas as variantes e bundles) | Casas Bahia, Ponto, Magalu, Americanas, Casa e Vídeo |

## Status

✅ **Fase 0 concluída** — a extração das três lojas está validada contra os sites reais.
🚧 **Fase 1 — núcleo** é o próximo passo. Ainda sem código de aplicação.

O planejamento completo está em **[SPEC.md](SPEC.md)**; os resultados do spike, na
seção 4.

```bash
python spikes/fase0_probe.py
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
