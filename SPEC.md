# PriceWatcher — Especificação

**Status:** rascunho para aprovação · **Data:** 2026-09-11 · **Autor:** Brenno + Claude

---

## 1. Objetivo

Monitorar automaticamente os preços de produtos-alvo no varejo brasileiro, manter um
histórico próprio de preços e disponibilidade, e enviar alerta no Telegram quando
surgir uma oportunidade real de compra.

A aplicação roda como um único container Docker no servidor pessoal, sem intervenção
manual.

**Produtos por fase:**

| Fase | Produto | Lojas |
|---|---|---|
| v1 (Fases 0–3) | Placas de vídeo **RX 9070 XT** e **RTX 5070 Ti** | Kabum, Pichau, Terabyteshop |
| Fase 5 | Console **Sony PlayStation 5** (todas as variantes, incluindo bundles) | Americanas, Casa e Vídeo |

O modelo de dados e os coletores são **agnósticos de categoria** desde a Fase 1 — ver
seção 5. Adicionar um produto novo é configuração, não refatoração.

### Critério de sucesso

- Coleta roda 2x/dia sem falhar silenciosamente.
- Quando um anúncio bate o menor preço já visto, o alerta chega em até 12h.
- Zero (ou quase zero) alertas falsos causados por erro de parsing.

---

## 2. Escopo

### Dentro do escopo (v1)

| Item | Definição |
|---|---|
| GPUs monitoradas | RX 9070 XT e RTX 5070 Ti — **todos** os fabricantes/modelos (ASUS, Gigabyte, MSI, Sapphire, PowerColor, XFX, Zotac, Galax, PNY, ASRock...) |
| Lojas | Kabum, Pichau, Terabyteshop |
| Frequência | 2x/dia (configurável) |
| Preço de referência | **À vista (PIX/boleto)** — preço parcelado guardado como dado secundário |
| Gatilhos de alerta | **(a)** novo mínimo histórico · **(b)** produto voltou ao estoque |
| Notificação | Telegram (mensagem para um chat privado) |
| Persistência | SQLite em volume Docker |

### Fora do escopo (v1)

- Compra automática / carrinho.
- **PlayStation 5 e o varejo generalista** (Americanas, Casa e Vídeo) — planejado na
  seção 18, entregue na **Fase 5**.
- **Amazon.com.br** — ver a análise na seção 4.1. Reavaliada na Fase 4 via agregador
  brasileiro, não por scraping direto.
- Mercado Livre, marketplaces de terceiros dentro das lojas.
- Interface web / dashboard (ver Fase 4 no roadmap).
- Alertas por queda percentual ou preço-alvo fixo (decidido: ruído desnecessário — o
  mínimo histórico já cobre o caso de uso).

### Extensibilidade planejada

Os alvos ficam num arquivo de config, e o esquema de dados não conhece a palavra "GPU".
Adicionar uma RTX 5080 deve ser só editar YAML — sem tocar em código. Adicionar uma
**categoria** nova (console, monitor, CPU) custa um adapter por plataforma de loja,
mais uma entrada de config.

> **Decisão de sequenciamento:** a generalização do esquema (seção 5 e 6) entra já na
> **Fase 1**, mesmo com o PS5 só chegando na Fase 5. Renomear coluna depois, com
> histórico de preço acumulado em produção, é migração chata e evitável — e aqui o
> custo de fazer certo desde o início é praticamente zero.

---

## 3. Arquitetura

```
┌───────────────────────────── Container Docker ─────────────────────────────┐
│                                                                            │
│   Scheduler (APScheduler, cron interno)                                    │
│        │  dispara 2x/dia                                                   │
│        ▼                                                                   │
│   Orquestrador de coleta                                                   │
│        │                                                                   │
│        ├─► Adapter Kabum ──────┐                                           │
│        ├─► Adapter Pichau ─────┼─► lista de RawOffer (title, price, url,   │
│        └─► Adapter Terabyte ───┘    sku, available, ...)                   │
│                                     │                                      │
│                                     ▼                                      │
│                              Normalizador                                  │
│                   (identifica GPU, fabricante, modelo; descarta ruído)     │
│                                     │                                      │
│                                     ▼                                      │
│                         Repositório  ──►  SQLite (volume)                  │
│                                     │                                      │
│                                     ▼                                      │
│                            Motor de alertas                                │
│                    (mínimo histórico · volta ao estoque)                   │
│                                     │                                      │
│                                     ▼                                      │
│                          Notificador Telegram                              │
└────────────────────────────────────────────────────────────────────────────┘
```

### Princípio central: adapters isolados

Cada loja é uma classe que implementa a mesma interface:

```python
class StoreAdapter(Protocol):
    name: str
    def fetch(self, target: Target, fetcher: Fetcher) -> list[RawOffer]: ...
```

Uma loja mudar o HTML **não pode** derrubar a coleta das outras. Falha de um adapter
é capturada, logada e reportada — as demais seguem normalmente.

---

## 4. Coletores por loja

✅ **Validado pelo spike da Fase 0 em 2026-09-11** (`spikes/fase0_probe.py`). As três
lojas entregam título, SKU, preço à vista e disponibilidade. A tabela abaixo descreve o
que **funciona de fato**, não o que se esperava.

| Loja | Fonte confirmada | Preço à vista | Disponibilidade | SKU |
|---|---|---|---|---|
| **Kabum** | `__NEXT_DATA__` → `props.pageProps.data.catalogServer.data[]` (Next.js Pages Router) | `priceWithDiscount` | `quantity > 0` ⚠️ | `code` |
| **Pichau** | Payload RSC do App Router (`self.__next_f`) | `pichau_prices.avista` (método PIX) | `stock_status == "IN_STOCK"` | `sku` |
| **Terabyteshop** | HTML server-rendered, atributos no card | `data-tss-price` | `data-tss-estoque == "1"` | da URL `/produto/<id>/` |

Resultado da execução (busca por `rx 9070 xt`):

```
[kabum]    42 brutos -> 17 casam regex -> 7 validos (1P + faixa) -> 7 disponiveis
[pichau]   36 brutos -> 18 casam regex -> 18 validos             -> 18 disponiveis
[terabyte] 300 brutos -> 34 casam regex -> 34 validos            -> 4 disponiveis
FALHAS: 0
```

**Correções que o spike impôs ao plano original:**

1. **Pichau não tem GraphQL público.** A hipótese de Magento 2 estava errada — `/graphql`
   devolve uma página de manutenção do frontend legado. Eles migraram para Next.js App
   Router, e os dados vêm no payload RSC (`self.__next_f`), que ainda é JSON de origem
   GraphQL (os objetos carregam `__typename`). A extração exige remontar os chunks e
   desescapar, mas o objeto final é limpo e traz `pichau_prices.avista` já separado do
   parcelado — exatamente o campo que queremos.
2. **O campo `available` da Kabum é inútil.** Ele vem `true` em **42 de 42** itens,
   incluindo produtos claramente esgotados. Usar esse campo quebraria o alerta de volta
   ao estoque de forma silenciosa. O sinal correto é `quantity > 0`. Ver seção 7.1.
3. **Pichau e Terabyte devolvem 403 para cliente Python.** No spike isso parecia
   questão de cabeçalho, e ajustá-los bastou naquele dia. **Era conclusão errada:** a
   Fase 1 mostrou que o bloqueio é por *fingerprint de TLS* e voltou a acontecer mesmo
   com os cabeçalhos completos. Ver **seção 11.2** — é o motivo de o cliente HTTP ser
   `curl_cffi` e não `httpx`.
4. **A busca da Kabum devolve lixo de verdade.** Entre os 42 resultados de "rx 9070 xt"
   vieram um processador Ryzen, uma fonte, um water cooler, uma RTX 5070 Ti e um
   **controle remoto de ar-condicionado** (modelo FBG-**9070**). O `match_regex` da
   seção 5 não é excesso de zelo — sem ele, o bot alertaria sobre um controle remoto de
   R$ 26,99 como "queda de preço de GPU".
5. ~~Pichau parece paginar em 36 itens.~~ **Resolvido na Fase 1, e a causa era outra.**
   O payload traz `total_count` e `page_info.total_pages`: a busca por "rtx 5070 ti"
   devolve **2054 resultados em 58 páginas**, e a maior parte é PC montado que apenas
   menciona a placa. O número baixo de itens casando não era paginação — era um **bug
   meu no spike**: eu localizava o objeto JSON contando chaves na mão, e as descrições
   de produto contêm HTML com `{` e `}` dentro de strings, o que descartava 34 dos 36
   produtos em silêncio. Com `json.JSONDecoder().raw_decode`, que respeita strings,
   a extração fica completa. A paginação existe (`&page=N`) e é usada com parada
   antecipada, já que os itens relevantes ficam nas primeiras páginas.

### 4.2 Placar das lojas

Situação consolidada em 2026-09-11. Verificado contra os sites reais, não presumido.

**✅ Coletando (5)**

| Loja | Categoria | Fonte | Preço à vista | Disponibilidade | Onde está |
|---|---|---|---|---|---|
| **Kabum** | GPU | `__NEXT_DATA__` (Next.js Pages) | `priceWithDiscount` | `quantity > 0` | `main`, em produção |
| **Pichau** | GPU | payload RSC (App Router) | `pichau_prices.avista` | `stock_status` | `main`, em produção |
| **Terabyteshop** | GPU | HTML + `data-tss-*` | `data-tss-price` | `data-tss-estoque` | `main`, em produção |
| **Americanas** | console | API pública VTEX | `Installments` → PIX 1x | `IsAvailable` + qtd | `fase5-ps5` |
| **Casa e Vídeo** | console | API pública VTEX | `Installments` → PIX 1x | `IsAvailable` + qtd | `fase5-ps5` |

As duas últimas usam **o mesmo adapter**, parametrizado por domínio.

**❌ Fora (4)**

| Loja | Motivo | Reversível? |
|---|---|---|
| **Casas Bahia** | Akamai Bot Manager — exige execução de JS | Só com navegador headless, que o servidor não comporta |
| **Ponto** | idem (mesmo grupo da Casas Bahia) | idem |
| **Magalu** | Akamai Bot Manager | idem |
| **Amazon.com.br** | Decisão de projeto: ToS restritivo e PA-API exige conta de Associados com vendas. Google Shopping foi avaliado como intermediário e rejeitado por qualidade de dado (seção 4.1) | Via agregador BR, na Fase 4 |

**🔍 Não avaliada**

| Loja | Situação |
|---|---|
| **Zoom / Buscapé** | Experimento previsto para a Fase 4. Cobriria indiretamente parte do que se perdeu acima |

**Leitura honesta do placar.** Das 9 lojas consideradas, 5 coletam. As 4 que ficaram de
fora caem em dois grupos: uma decisão consciente (Amazon) e um obstáculo técnico real
(Akamai). Nenhuma foi perdida por limitação do nosso código — e vale notar que Casas
Bahia e Ponto são a mesma empresa, então a perda efetiva são **dois grupos de varejo**,
não três lojas independentes.

### 4.1 Cobertura da Amazon — análise e decisão

A Amazon ficou **fora do v1**. Registro aqui o porquê e as alternativas avaliadas,
para não reabrirmos a discussão do zero depois.

**Por que não scraping direto:** a Amazon combina detecção ativa de bots com termos de
uso restritivos. O caminho oficial (Product Advertising API 5.0) exige conta de
Associados aprovada **e** vendas qualificadas para manter o acesso — inviável para uso
pessoal. Nenhuma tentativa de burlar CAPTCHA será implementada.

**Por que não via Google Shopping:** foi considerado usar o Google Shopping como
intermediário para os preços da Amazon. Descartado por três motivos, em ordem de peso:

1. **Qualidade do dado (decisivo).** Preço no Google Shopping vem de feed de merchant,
   defasado em horas ou dias, e cada lojista alimenta o campo `price` com uma base
   diferente — uns mandam preço cheio, outros o preço PIX. Isso é ruim entre lojas, mas
   é pior ao longo do tempo: se um merchant muda a base do que alimenta, nosso
   histórico enxerga uma queda de ~10% que nunca existiu e dispara um "novo mínimo"
   falso — passando pelos guardrails da seção 7, porque a queda parece plausível.
2. **Não existe API oficial.** A Content API for Shopping serve para lojistas subirem
   o próprio catálogo, não para consultar o de terceiros. A Shopping Search API foi
   descontinuada.
3. **Bloqueio pior, não melhor.** Consulta automatizada ao Google é contra o ToS deles
   e é bloqueada mais agressivamente que a própria Amazon.

SERP APIs pagas (SerpApi, ScraperAPI) resolveriam o item 3 por poucos dólares no nosso
volume, mas não resolvem o item 1 — que é o que realmente importa aqui.

**Caminho escolhido:** o v1 entrega com Kabum, Pichau e Terabyte, que cobrem a maior
parte do mercado de GPU no Brasil; o que aparece na Amazon BR costuma ser vendedor de
marketplace com preço acima dessas três. Na **Fase 4**, testamos um agregador
brasileiro (**Zoom** ou Buscapé) — menos protegido que o Google e com preço à vista
normalmente explícito. O critério de sucesso do experimento é objetivo: *o agregador
trouxe alguma oferta melhor que as três lojas diretas já traziam?* Se não trouxer, a
questão se encerra.

### Boas práticas de coleta (todas as lojas)

- **Conjunto completo de cabeçalhos de navegador, não só `User-Agent`.** Pichau e
  Terabyte respondem **403** sem `Accept`, `Accept-Language`, `Accept-Encoding` e
  `Sec-Fetch-*` coerentes — confirmado no spike. O cliente HTTP deve nascer com esses
  defaults e tratar resposta gzip/deflate.
- Delay aleatório de 2–6s entre requisições e jitter no horário da coleta.
- Retry com backoff exponencial (3 tentativas) apenas em erro de rede/5xx.
- Timeout de 20s por requisição.
- Volume total: ~6–12 requisições por rodada (3 lojas × 2 GPUs, mais eventuais páginas
  de detalhe), 2x/dia. Tráfego desprezível para qualquer uma dessas lojas.

---

## 5. Normalização e identidade de produto

### Chave canônica

A identidade de um anúncio é `(store, store_sku)` — ou, se a loja não expuser SKU
estável, a URL canônica do produto. Isso evita que uma mudança de título quebre o
histórico.

### Parsing do título

O normalizador é agnóstico de categoria: recebe um título e o `target` que originou a
busca, e devolve sempre os mesmos campos.

**Exemplo GPU** — `"Placa de Vídeo RX 9070 XT Pulse Sapphire AMD Radeon, 16GB GDDR6"`:

| Campo | Valor |
|---|---|
| `category` | `gpu` |
| `model_key` | `RX_9070_XT` |
| `brand` | `Sapphire` |
| `model_line` | `Pulse` |
| `is_bundle` | `false` |

**Exemplo console** — `"Console PlayStation 5 Slim Digital 1TB + EA Sports FC 26"`:

| Campo | Valor |
|---|---|
| `category` | `console` |
| `model_key` | `PS5_SLIM_DIGITAL` |
| `brand` | `Sony` |
| `is_bundle` | `true` |
| `bundle_note` | `EA Sports FC 26` |

`title_raw` é sempre preservado, em qualquer categoria.

### Bundles são uma dimensão, não ruído

Rastreamos bundles de PS5 (decisão da seção 17), e isso exige uma regra explícita:

> **Um bundle nunca é comparado com um console avulso.**

Isso sai de graça do desenho, porque a identidade é `(store, store_sku)` e cada SKU tem
sua própria série histórica — o "novo mínimo" de um combo com jogo só olha para o
próprio combo. Os dois pontos que exigem cuidado consciente são:

1. **Exibição.** A mensagem de alerta sempre mostra o `bundle_note`, para você julgar
   se o preço faz sentido. Um combo a R$ 3.900 pode ser melhor ou pior que um console
   avulso a R$ 3.500 dependendo do jogo — essa avaliação é sua, não do bot.
2. **Agregação futura.** Qualquer visão de "melhor preço do PS5" (Fase 4, gráficos)
   deve filtrar por `is_bundle` antes de comparar. Misturar as duas populações produz
   um "mínimo histórico" que não existe.

### Filtros anti-falso-positivo

Descartar resultados de busca que:

- Não casem com o regex do alvo (ex.: busca por "9070 XT" retornando um 9070 liso ou um
  suporte de placa; busca por "PS5" retornando capa de controle, headset ou cartão PSN).
- Tenham preço fora da faixa de sanidade configurada para aquele alvo. Preço fora da
  faixa vira log de aviso, não registro.
- **Não sejam vendidos pela própria loja (1P).** Ver regra abaixo.

### Regra de vendedor (1P)

No varejo generalista, boa parte dos anúncios é de terceiro no marketplace da loja —
onde se concentram preço irreal e vendedor duvidoso. Decisão: **só 1P**.

- `seller_type` é resolvido por adapter, porque cada plataforma expõe isso de um jeito.
- **Se o vendedor não puder ser determinado, o anúncio é descartado**, não aceito. Falha
  fechada: é melhor perder uma oferta legítima do que alertar sobre um vendedor
  desconhecido a preço suspeito.
- Todo descarte por essa regra é contado e logado, para sabermos se um adapter passou a
  descartar tudo por mudança de layout.

O regex de match, a faixa de sanidade e os limiares de alerta ficam na config do alvo,
não no código.

---

## 6. Modelo de dados (SQLite)

```sql
-- Um anúncio específico de uma loja específica
CREATE TABLE product (
    id            INTEGER PRIMARY KEY,
    store         TEXT NOT NULL,          -- 'kabum' | 'magalu' | 'casasbahia' | ...
    store_sku     TEXT NOT NULL,
    url           TEXT NOT NULL,
    title_raw     TEXT NOT NULL,

    category      TEXT NOT NULL,          -- 'gpu' | 'console'
    model_key     TEXT NOT NULL,          -- 'RX_9070_XT' | 'PS5_SLIM_DIGITAL' | ...
    brand         TEXT,
    model_line    TEXT,
    is_bundle     INTEGER NOT NULL DEFAULT 0,
    bundle_note   TEXT,                   -- 'EA Sports FC 26 + 2º controle'

    seller_type   TEXT NOT NULL,          -- 'first_party' | 'marketplace'
    seller_name   TEXT,

    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    UNIQUE (store, store_sku)
);
CREATE INDEX idx_product_model ON product(category, model_key, is_bundle);

-- Uma observação de preço/estoque num instante
CREATE TABLE price_point (
    id                 INTEGER PRIMARY KEY,
    product_id         INTEGER NOT NULL REFERENCES product(id),
    observed_at        TEXT NOT NULL,
    price_cash         INTEGER,           -- centavos, à vista (PIX/boleto). NULL se indisponível
    price_installment  INTEGER,           -- centavos, preço cheio/parcelado
    installments       INTEGER,
    available          INTEGER NOT NULL,  -- 0/1
    run_id             INTEGER NOT NULL REFERENCES collection_run(id)
);
CREATE INDEX idx_pp_product_time ON price_point(product_id, observed_at DESC);

-- Auditoria de cada rodada de coleta
CREATE TABLE collection_run (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    store         TEXT NOT NULL,
    status        TEXT NOT NULL,          -- 'ok' | 'partial' | 'failed'
    offers_found  INTEGER,
    error         TEXT
);

-- Alertas enviados (deduplicação e histórico)
CREATE TABLE alert (
    id            INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES product(id),
    kind          TEXT NOT NULL,          -- 'new_low' | 'back_in_stock'
    price_cash    INTEGER,
    previous_best INTEGER,
    sent_at       TEXT NOT NULL,
    delivered     INTEGER NOT NULL DEFAULT 0
);
```

Preços são armazenados como **inteiro em centavos** — nunca float.

**Fuso: grava em UTC, mostra em Brasília.** O banco guarda ISO-8601 UTC porque
comparação de histórico e cálculo de cooldown precisam de um relógio sem ambiguidade.
Tudo que uma pessoa lê — log, mensagem no Telegram, relatório no terminal — é
convertido para `America/Sao_Paulo` pelo módulo `tempo.py`, que é o único lugar que
conhece fuso.

Misturar os dois papéis produz aquele bug chato de *"o alerta diz 08:00 mas o log diz
11:00"* — que chegou a acontecer aqui: o `TZ` com nome IANA não é entendido pelo
runtime C do Windows e o logging caía para UTC em silêncio. Por isso o formatter de
log carimba o offset explicitamente.

---

## 7. Motor de alertas

### Gatilho A — Novo mínimo histórico

```
Dispara quando, para um produto:
    available == true
E   price_cash < min(price_cash de todas as observações anteriores desse produto
                     em que available == true)
```

**Guardrails contra ruído e erro de parsing:**

| Guardrail | Regra | Motivo |
|---|---|---|
| Aquecimento | Exige ≥ 3 observações anteriores do produto | Todo produto novo é "mínimo histórico" na primeira vez |
| Delta mínimo | Exige queda ≥ 1% **e** ≥ R$ 100 vs. o mínimo anterior | Só avisa quando a economia é material, não por variação de centavos |
| Sanidade | Ignora (e loga aviso) preço < 50% do mínimo histórico | Queda absurda quase sempre é parsing quebrado, não promoção |
| Cooldown | Máximo 1 alerta `new_low` por produto a cada 12h | Evita repetição entre rodadas próximas |

**Limiares são por alvo.** Os valores acima são o *default global*; cada `target` pode
sobrescrevê-los. Isso deixa de ser detalhe quando o PS5 entra: R$ 100 foi calibrado
para placa de R$ 4.500, e num console de R$ 3.200 esse mesmo valor representa uma
exigência bem mais dura. Vale notar que, nas duas faixas de preço, **o limiar em reais
é o que efetivamente manda** — 1% de R$ 3.200 são R$ 32, muito abaixo de R$ 100. A
regra percentual só voltaria a ter efeito em produto acima de R$ 10.000.

### Gatilho B — Volta ao estoque

```
Dispara quando a observação anterior tinha available == false
e a atual tem available == true.
```

Cooldown de 24h por produto. Se o produto voltar ao estoque **e** com mínimo
histórico, envia um único alerta combinado.

### 7.1 De onde vem `available` — uma armadilha por loja

O spike da Fase 0 mostrou que **não existe um campo universal de disponibilidade**.
Cada adapter é responsável por produzir um booleano confiável, e a regra é diferente
em cada loja:

| Loja | Sinal correto | Armadilha |
|---|---|---|
| Kabum | `quantity > 0` | O campo `available` vem `true` em **100%** dos itens (42/42), inclusive esgotados. **Nunca usar.** |
| Pichau | `stock_status == "IN_STOCK"` | Confiável. |
| Terabyte | `data-tss-estoque == "1"` | Confiável: bate 254/254 com o texto "Esgotado"/"Indisponível" na página. |

Vale registrar de onde veio a regra da Terabyte: a observação de que **loja
indisponível sempre exibe "Esgotado" ou "Indisponível" na página** se confirmou lá com
correlação perfeita. Na Kabum ela não se aplica porque a página de busca é montada no
cliente a partir do JSON — o HTML servido não contém nenhum desses textos, e o `quantity`
é o único sinal disponível.

Consequência para os testes: as fixtures da seção 13 precisam incluir, por loja, **um
item disponível e um esgotado**. É o único jeito de detectar em regressão que o sinal de
estoque inverteu ou parou de ser preenchido.

### Digest

Todos os alertas de uma rodada são agrupados em **uma única mensagem** no Telegram.
Se 6 placas baixarem de preço na mesma coleta, chega uma mensagem com 6 itens — não 6
mensagens.

---

## 8. Notificação Telegram

### Setup

Bot criado via @BotFather: **@FoscoH_PriceWatcherBot** (id `8826548305`). Token e
destinos vêm de variáveis de ambiente carregadas de um `.env` que **nunca** entra no
git. Comunicação direta com a Bot API via HTTP — só precisamos de `sendMessage`.

✅ **Validado em 2026-09-11:** token ativo, sem webhook configurado, e `sendMessage`
entregue com sucesso no chat privado. O caminho ponta a ponta funciona.

✅ **Grupo pronto:** *Price Watcher*, com o bot já adicionado e o id capturado em
`TELEGRAM_GROUP_CHAT_ID`. Ele **já nasceu como supergroup**, o que encerra o risco de
migração de id
descrito abaixo — o tratamento de `migrate_to_chat_id` continua no plano por ser barato,
mas deixou de ser uma ameaça concreta para este grupo.

> ⚠️ **Encoding.** O primeiro `sendMessage` de teste falhou com
> `Bad Request: strings must be encoded in UTF-8`, porque o shell do Windows corrompeu
> acentos e emoji ao montar a requisição. O notificador **deve** serializar o corpo em
> UTF-8 explicitamente, sem depender do encoding do ambiente. Não é detalhe: toda
> mensagem de alerta contém "mínimo histórico" e "R$", e quebraria exatamente assim em
> produção.

### Destinos

Os destinos são uma **lista**. O notificador itera sobre ela e cada envio é isolado:
falha em um destino é logada e **não** impede os demais.

Um destino filtra por duas dimensões independentes:

- `categories` — categoria de produto (`gpu`, `console`). Omitir = todas.
- `kinds` — tipo de alerta: `price` (mínimo histórico e volta ao estoque) ou
  `operational` (coletor quebrado). Omitir = todos.

**Todos os alertas de preço vão para o grupo**, GPU e console, sem divisão por
categoria. O chat privado fica reservado aos alertas operacionais — se um parser
quebrar, quem precisa saber é quem mantém o sistema, não o grupo inteiro. Assim o
privado continua tendo função sem duplicar cada alerta para quem já está no grupo:

```yaml
notify:
  destinations:
    - id: grupo
      chat_id: ${TELEGRAM_GROUP_CHAT_ID}
      categories: [console]        # PS5 para o grupo
    - id: brenno
      chat_id: ${TELEGRAM_CHAT_ID}
      categories: [gpu, console]   # você recebe tudo
```

Nada impede voltar a segmentar por categoria depois — o mecanismo continua lá, só não
está em uso.

### Como funciona um grupo (decisão da seção 17)

Para adicionar o grupo: crie o grupo, adicione **@FoscoH_PriceWatcherBot** como membro,
mande qualquer mensagem lá e leia o `chat.id` no `getUpdates` — igual fizemos no
privado. O id de grupo é **negativo** (supergrupo começa com `-100`).

Três armadilhas que valem estar escritas:

1. **Bot não inicia conversa.** Para destino individual, a pessoa precisa dar `/start`
   no bot antes — não há como cadastrar o contato de alguém pelo servidor. Em grupo
   isso não se aplica: basta o bot ser membro.
2. **O id muda quando o grupo vira supergrupo.** Se um grupo comum for promovido
   (acontece ao adicionar gente ou mudar configuração), o `chat_id` antigo para de
   funcionar e a API responde com `migrate_to_chat_id`. O notificador deve tratar esse
   erro logando o id novo de forma bem visível, em vez de só falhar.
3. **Privacy mode não atrapalha.** Por padrão o bot só *lê* mensagens dirigidas a ele
   em grupos — mas isso afeta apenas leitura. Enviar funciona normalmente, e no v1 o
   bot só envia.

### Formato da mensagem

```
🟢 4 oportunidades — 11/09 08:00

📉 NOVO MÍNIMO
RX 9070 XT Pulse · Sapphire
R$ 4.299,00 à vista  (antes: R$ 4.690,00 · -8,3%)
Terabyteshop → [link]

📉 NOVO MÍNIMO
RTX 5070 Ti Ventus 3X · MSI
R$ 5.150,00 à vista  (antes: R$ 5.399,00 · -4,6%)
Kabum → [link]

📦 VOLTOU AO ESTOQUE
RX 9070 XT Hellhound · PowerColor
R$ 4.780,00 à vista
Pichau → [link]

📉 NOVO MÍNIMO
PlayStation 5 Slim Digital 1TB
🎁 bundle: EA Sports FC 26
R$ 3.499,00 à vista  (antes: R$ 3.699,00 · -5,4%)
Magalu · vendido pela loja → [link]
```

Itens de console trazem duas informações a mais: o `bundle_note` quando `is_bundle`,
e a confirmação de vendedor 1P — as duas coisas que você precisa para julgar a oferta
sem abrir o link.

### Alerta operacional

Mensagem separada (e rara) quando:

- Um adapter falha em **2 rodadas consecutivas** → "⚠️ Coletor Kabum falhando há 2
  rodadas: &lt;erro&gt;".
- Nenhuma oferta encontrada para uma GPU em **todas** as lojas → sinal forte de
  parsing quebrado.

Sem mensagem de "tudo ok" por padrão — silêncio significa que está funcionando.
Heartbeat semanal opcional via config.

---

## 9. Agendamento

- **APScheduler** dentro do próprio processo (o container é um serviço de longa
  duração, não um one-shot de cron externo).
- Padrão: `08:00` e `20:00` no fuso `America/Sao_Paulo`.
- Jitter de ±15 min para não bater sempre no mesmo minuto exato.
- Flag `--run-once` para execução manual/teste sem esperar o schedule.

---

## 10. Configuração

### Variáveis de ambiente (segredos)

| Var | Obrigatória | Descrição |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | sim | Token do bot |
| `TELEGRAM_CHAT_ID` | sim | Seu chat privado |
| `TELEGRAM_GROUP_CHAT_ID` | sim | Grupo de alertas — destino principal. Negativo; supergrupo começa com `-100` |

> Os valores reais vivem só no `.env`. **Nenhum `chat_id` entra em arquivo versionado** —
> o repositório é público, e não há motivo para publicar identificadores de chat.
| `DB_PATH` | não | Default `/data/prices.db` |
| `LOG_LEVEL` | não | Default `INFO` |
| `TZ` | não | Default `America/Sao_Paulo` |

### `config.yaml` (comportamento)

```yaml
schedule:
  times: ["08:00", "20:00"]
  jitter_minutes: 15

notify:
  destinations:
    - id: grupo
      chat_id: ${TELEGRAM_GROUP_CHAT_ID}
      kinds: [price]                     # sem filtro de categoria: GPU e console
    - id: brenno
      chat_id: ${TELEGRAM_CHAT_ID}
      kinds: [operational]               # só coletor quebrado; não duplica alerta

stores:
  # v1 — hardware
  kabum:      { enabled: true,  categories: [gpu] }
  pichau:     { enabled: true,  categories: [gpu] }
  terabyte:   { enabled: true,  categories: [gpu] }
  # Fase 5 — varejo generalista (ver seção 18)
  casasbahia: { enabled: false, categories: [console], platform: gcb }
  ponto:      { enabled: false, categories: [console], platform: gcb }
  magalu:     { enabled: false, categories: [console], platform: magalu }
  americanas: { enabled: false, categories: [console], platform: americanas }
  casaevideo: { enabled: false, categories: [console], platform: vtex,
                base_url: "https://www.casaevideo.com.br" }
  # zoom:     { enabled: false }         # experimento da Fase 4 — ver seção 4.1

targets:
  - id: RX_9070_XT
    category: gpu
    label: "Radeon RX 9070 XT"
    search_terms: ["rx 9070 xt", "radeon 9070 xt"]
    match_regex: '(?i)\b(rx\s*)?9070\s*xt\b'
    sanity_price_range_brl: [2000, 15000]

  - id: RTX_5070_TI
    category: gpu
    label: "GeForce RTX 5070 Ti"
    search_terms: ["rtx 5070 ti", "geforce 5070 ti"]
    match_regex: '(?i)\b(rtx\s*)?5070\s*ti\b'
    sanity_price_range_brl: [2500, 18000]

  # Fase 5
  - id: PS5
    category: console
    label: "PlayStation 5"
    search_terms: ["playstation 5", "console ps5"]
    include_bundles: true
    require_all:  ['(?i)\b(playstation\s*5|ps5)\b']
    require_any:  ['(?i)\bconsole\b', '(?i)\b[12]\s*tb\b', '(?i)\b825\s*gb\b']
    exclude:      ['(?i)\b(capa|skin|suporte|headset|cart[ãa]o|psn|gift\s*card|
                    pel[íi]cula|adesivo)\b']
    # Ordem importa: primeiro match vence. 'Pro' antes de 'Digital'.
    variants:
      - { key: PS5_PRO,           match_regex: '(?i)\bpro\b' }
      - { key: PS5_SLIM_DIGITAL,  match_regex: '(?i)\bdigital\b' }
      - { key: PS5_SLIM_DISC,     default: true }
    sanity_price_range_brl: [2000, 9000]
    # Sem override: as três variantes usam o default global de R$ 100 (seção 17).
    # O mecanismo de override por alvo fica disponível caso mude de ideia.

alerts:                                  # defaults globais
  new_low:
    enabled: true
    min_observations: 3
    min_drop_percent: 1.0
    min_drop_brl: 100       # nos preços atuais, este é o limiar que efetivamente manda
    cooldown_hours: 12
  back_in_stock:
    enabled: true
    cooldown_hours: 24
```

**Sobre os filtros do PS5.** `require_all` é o sinal do produto; `require_any` é o sinal
de que aquilo é um **console**, e não um acessório; `exclude` é a rede de segurança.

A lista de exclusão **não** contém "controle" nem "jogo", de propósito: um bundle
legítimo se chama *"PS5 + 2º controle DualSense"*, e excluir por essas palavras mataria
exatamente o que decidimos rastrear.

> ⚠️ **Correção de uma afirmação anterior.** Esta seção dizia que "quem separa console de
> acessório é o `require_any`, não a lista negra". **Estava errado**, e a revisão manual
> dos títulos reais provou: o `require_any` original aceitava a palavra `console`, e
> acessório em português se anuncia como *"para console PS5"*. Passaram por console uma
> mochila, um cabo flat, uma **chave Torx**, tampas e uma dúzia de *PlayStation Portal* —
> que é outro aparelho. Ver seção 18.2.
>
> Os dois mecanismos são necessários e cobrem coisas diferentes: a **lista de exclusão**
> pega o acessório que já vimos; o **`require_any` apertado** (título começa com
> "console", ou declara capacidade) pega o que ainda não vimos. Há teste separado para
> cada papel — o do `require_any` usa acessórios propositalmente fora da lista negra,
> porque com os títulos reais os dois critérios se sobrepõem e mascaram a contribuição
> de cada um.

---

## 11. Stack técnica

| Camada | Escolha | Justificativa |
|---|---|---|
| Linguagem | Python 3.12 | Ecossistema de scraping maduro; performance não é gargalo aqui |
| HTTP | `curl_cffi` | **Obrigatório, não preferência.** Pichau e Terabyte bloqueiam por fingerprint de TLS — ver 11.2 |
| Parsing HTML | `selectolax` | Bem mais rápido e leve que BeautifulSoup para o mesmo trabalho |
| Agendamento | `APScheduler` | Cron interno, sem depender do host |
| Banco | SQLite (`sqlite3` stdlib) | Um único usuário, escritas raras. Postgres seria overkill |
| Validação | `pydantic` v2 | Contratos claros entre adapter → normalizador → repositório |
| Config | `PyYAML` + `pydantic-settings` | Config declarativa validada no boot |
| Telegram | Bot API via `httpx` | Framework completo seria peso morto |
| Testes | `pytest` + fixtures de HTML salvo | Ver seção 13 |

### 11.2 Por que `curl_cffi` e não `httpx`

Descoberto na Fase 1, depois que a coleta real falhou onde o spike tinha passado.

Pichau e Terabyte ficam atrás de um WAF que faz **fingerprint de TLS (JA3/JA4)**. O
handshake do módulo `ssl` do Python é reconhecível, e nenhuma combinação de cabeçalho
resolve. Medições:

| Cliente | Kabum | Pichau | Terabyte |
|---|---|---|---|
| `httpx` HTTP/1.1 | 200 | **403** | **403** |
| `httpx` HTTP/2 | 200 | **403** | **403** |
| `httpx` + cipher suite do Chrome | 200 | **403** | **403** |
| `urllib` (o que o spike usou) | 200 | **403** | **403** |
| `curl` do sistema | 200 | 200 | 200 |
| `curl_cffi` com `impersonate="chrome"` | 200 | 200 | 200 |

Dois detalhes que valem estar escritos:

- **Ajustar `SSLContext` não é suficiente.** O WAF olha ordem de extensões e GREASE,
  que o `ssl` do Python não produz. Testei; continua 403.
- **O spike passou e a Fase 1 falhou com o mesmo código.** O `urllib` funcionou no
  primeiro dia e passou a tomar 403 depois de algumas dezenas de requisições. Ou seja,
  o bloqueio **escala com o histórico do fingerprint** — um teste que passa hoje não
  prova que o cliente está adequado. Foi exatamente o tipo de armadilha que a seção 13
  existe para pegar.
- `impersonate="firefox"` passa na Terabyte mas toma 403 na Pichau. O perfil padrão é
  o do Chrome.

✅ **Risco encerrado em 2026-09-11, no servidor.** O `curl_cffi` 0.16.3 carregou e
buscou as três lojas a partir do Core 2 Duo E7500 (x86-64 baseline, sem SSE4.2, POPCNT
ou AVX). O `libcurl-impersonate` faz detecção de CPU em tempo de execução e não exige
x86-64-v2. O plano B (chamar o `curl` do sistema por subprocess) **não foi necessário**
e não será implementado — mas o `curl` continua na imagem, agora como ferramenta de
diagnóstico dentro do container.

Saída real do `--selftest` no servidor:

```
  python      : 3.12.14
  plataforma  : Linux-6.8.0-137-generic-x86_64-with-glibc2.41
  curl_cffi   : 0.16.3 carregou OK
    kabum      OK (364 KB)
    pichau     OK (1525 KB)
    terabyte   OK (682 KB)
```

### 11.1 Licenciamento — todo componente deve ser open source

Requisito do projeto. Vale para dependências, banco, imagem base e runtime. Nada de
biblioteca proprietária, serviço pago com SDK fechado ou componente de licença restritiva.

| Componente | Licença |
|---|---|
| Python | PSF License |
| curl_cffi | MIT (empacota libcurl-impersonate, licença curl) |
| cffi · pycparser | MIT-0 · BSD-3-Clause |
| certifi | MPL-2.0 |
| selectolax | MIT (empacota lexbor, Apache-2.0) |
| pydantic · pydantic-settings | MIT |
| PyYAML | MIT |
| APScheduler | MIT |
| pytest | MIT |
| SQLite | Domínio público |
| Debian (`python:3.12-slim`) | Livre (DFSG) |
| Docker Engine | Apache-2.0 |

Verificado com os metadados dos pacotes instalados, não por memória.

**Uma ressalva honesta sobre o Telegram.** Os aplicativos cliente do Telegram são open
source e a Bot API é aberta, documentada e gratuita — mas **o servidor deles não é**.
Não estamos embarcando nada fechado: é um serviço externo, acessado por HTTP, e o
notificador fica isolado atrás de uma interface própria (`notify/`). Se um dia isso
incomodar, trocar por ntfy, Matrix ou Gotify — todos open source e self-hostáveis — é
trocar um módulo, não reescrever o sistema.

**Sem navegador headless.** Playwright dobraria o tamanho da imagem e o consumo de RAM
— e, no CPU do servidor de destino (Core 2 Duo, sem SSE4.2/AVX), provavelmente nem
rodaria bem. Ver seção 12: isso deixou de ser preferência e virou restrição de
hardware. Loja que exigir JS é loja descartada.

---

## 12. Docker e deploy

### Host de destino

| | |
|---|---|
| SO | Ubuntu 24.04.4 LTS · kernel 6.8.0-137-generic |
| Arquitetura | x86_64 (**amd64**) |
| CPU | Intel Core 2 Duo **E7500** @ 2.93 GHz, 2 núcleos |
| RAM | 8 GB (7,7 GiB) |
| Docker | 28.1.1 · Compose 2.35.1 |

**Flags do CPU, verificadas no servidor em 2026-09-11:**

| Instrução | E7500 |
|---|---|
| SSE3 (`pni`) · SSSE3 · SSE4.1 | ✅ presentes |
| **SSE4.2 · POPCNT** | ❌ ausentes |
| **AVX · AVX2** | ❌ ausentes |

Confirmado: o processador é **x86-64 baseline (v1)**, não atinge x86-64-v2. O risco da
seção 11.2 deixou de ser hipótese sobre o modelo e passou a ser fato sobre esta máquina.

> ⚠️ Ao ler `/proc/cpuinfo`, lembre que o kernel chama SSE3 de **`pni`**. Procurar pela
> string `sse3` dá falso negativo — aconteceu na primeira execução do script de deploy.

Para a carga desta aplicação — meia dúzia de requisições HTTP duas vezes ao dia e um
SQLite de poucos MB — esse hardware é **folgado**. Memória sobra e o processo fica
ocioso 99,9% do tempo.

O ponto que merece atenção é a idade do CPU. Um Core 2 Duo é **x86-64 baseline
(v1)**: tem SSE até SSSE3 (e SSE4.1 nos modelos Penryn), mas **não tem SSE4.2, POPCNT
nem AVX**. Consequências práticas:

- **Nosso stack está seguro.** Debian/Ubuntu e os wheels manylinux do PyPI ainda são
  compilados para o baseline x86-64, então `python:3.12-slim`, `httpx`, `selectolax` e
  `pydantic` rodam normalmente. Fixar `platform: linux/amd64` no compose evita surpresa.
- **Navegador headless está efetivamente fora.** Chromium moderno em Core 2 Duo seria,
  na melhor hipótese, muito lento — e há risco real de simplesmente não iniciar por
  exigência de instruções mais novas. Isso **endurece** a decisão da seção 11: deixa de
  ser preferência de engenharia e vira limitação de hardware.
- **Desdobramento para a Fase 5:** se alguma loja do varejo generalista exigir execução
  de JavaScript, a resposta passa a ser **descartar a loja**, não adicionar Playwright.
  A pergunta 4 do spike da seção 18 já nasce com essa resposta.
- Sem AES-NI, o handshake TLS é mais lento — irrelevante no nosso volume.

### Imagem

- Base `python:3.12-slim`, multi-stage (build de deps → runtime enxuto).
- Executa como usuário não-root.
- `HEALTHCHECK` verificando que o scheduler está vivo e que a última rodada bem
  sucedida não é mais antiga que 24h.
- `restart: unless-stopped`.

### `compose.yaml`

```yaml
services:
  pricewatcher:
    build: .
    container_name: pricewatcher
    platform: linux/amd64
    restart: unless-stopped
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
      TELEGRAM_GROUP_CHAT_ID: ${TELEGRAM_GROUP_CHAT_ID}
      TZ: America/Sao_Paulo
    volumes:
      - ./data:/data                       # SQLite persistente
      - ./config.yaml:/app/config.yaml:ro
```

Segredos via arquivo `.env` fora do git. O `.gitignore` cobre `.env`, `data/` e `*.db`.

### Procedimento de deploy

A ordem importa: o autoteste vem **antes** de deixar o serviço no ar, porque ele
responde o risco aberto do `curl_cffi` (seção 11.2) em uma linha, em vez de a gente
descobrir por silêncio de alertas dias depois.

```bash
git clone https://github.com/BrennoBaptista/PriceWatcher.git && cd PriceWatcher
cp .env.example .env    # preencher token e os dois chat_id
mkdir -p data && sudo chown -R 10001:10001 data   # ver nota abaixo
docker compose build
docker compose run --rm pricewatcher --selftest     # 1. o ambiente aguenta?
docker compose run --rm pricewatcher --test-notify  # 2. os canais respondem?
docker compose run --rm pricewatcher --run-once     # 3. coleta de verdade
docker compose up -d                                # 4. agendador no ar
```

> **Por que o `chown`.** O container roda como uid **10001** (não-root), mas o bind
> mount `./data:/data` substitui o diretório da imagem pelo do host — e o Docker cria
> esse diretório como **root** quando ele não existe. O `chown` do Dockerfile é apagado
> pela montagem, e o processo não consegue escrever o SQLite. O `deploy-check.sh` faz
> isso automaticamente; aqui fica explícito porque o sintoma (`unable to open database
> file`) não sugere permissão em nada.

Se o passo 1 falhar ao carregar o `curl_cffi`, **pare**: o binário não roda naquele
CPU e o plano B da seção 11.2 precisa entrar antes de qualquer outra coisa. O `curl`
do sistema já está instalado na imagem justamente para isso.

✅ **Verificado em 2026-09-11:** a imagem construiu no servidor na primeira tentativa e
o `--selftest` passou. O primeiro deploy falhou por outro motivo — `curl_cffi` não
estava declarado no `pyproject.toml` (ver `tests/test_dependencias.py`), não por
problema de Docker ou de CPU.

### Backup

O banco é um único arquivo em `./data`. Backup = copiar o arquivo. Comando documentado
no README usando `sqlite3 .backup`, para cópia consistente com o processo rodando.

---

## 13. Testes e resiliência a mudanças de site

Este é o ponto que faz ou quebra o projeto: **sites mudam e scrapers quebram em
silêncio.**

1. **Fixtures de HTML real.** Para cada loja, um HTML/JSON de resposta salvo em
   `tests/fixtures/`. Testes de parsing rodam offline, sem rede.
1b. **Manifesto x código.** `tests/test_dependencias.py` compara o que `src/` importa
   com o que o `pyproject.toml` declara, nos dois sentidos. Existe por um erro real:
   ao trocar `httpx` por `curl_cffi`, o venv local foi atualizado e o manifesto não —
   tudo passava na máquina de desenvolvimento e o container subia sem o pacote. Venv
   que já tem tudo instalado nunca pega isso; comparar código com manifesto pega.
2. **Teste de contrato (manual).** `make check-live` bate nas lojas de verdade e falha
   se alguma retornar zero resultados — usado quando algo parece errado.
3. **Detecção em produção.** Zero ofertas para uma GPU em todas as lojas → alerta
   operacional no Telegram. Você fica sabendo que quebrou sem precisar olhar log.
4. **Nunca falhar silenciosamente.** Exceção em adapter vira `collection_run.status =
   'failed'` com o erro persistido, não um `except: pass`.

---

## 14. Estrutura do projeto

```
Pricelookup/
├── SPEC.md
├── README.md
├── spikes/
│   └── fase0_probe.py        # Fase 0 — descoberta, não é código de produção
├── Dockerfile
├── compose.yaml
├── config.yaml
├── .env.example
├── pyproject.toml
├── src/pricewatcher/
│   ├── __main__.py           # entrypoint: --run-once | --serve
│   ├── config.py             # carga e validação da config
│   ├── models.py             # RawOffer, NormalizedOffer, Target (pydantic)
│   ├── http.py               # Fetcher com curl_cffi — ver 11.2
│   ├── collector.py          # orquestrador; isola falha por loja
│   ├── scheduler.py
│   ├── stores/
│   │   ├── base.py           # StoreAdapter, HTTP client compartilhado
│   │   ├── kabum.py
│   │   ├── pichau.py
│   │   ├── terabyte.py
│   │   └── platforms/       # Fase 5 — adapter por família, não por marca
│   │       └── vtex.py      #   genérico: Casa e Vídeo + Americanas
│   ├── normalize.py          # título → gpu_model/brand/model_line + filtros
│   ├── db.py                 # schema, migrações, repositório
│   ├── alerts.py             # regras de mínimo histórico e volta ao estoque
│   └── notify/
│       ├── telegram.py       # sendMessage em UTF-8 explícito (ver seção 8)
│       ├── render.py         # montagem da mensagem, separada do transporte
│       └── router.py         # fan-out por destino, com filtro de categoria
└── tests/
    ├── fixtures/
    └── test_*.py
```

---

## 15. Roadmap

| Fase | Entrega | Definição de pronto |
|---|---|---|
| **0 — Spike** ✅ | Validar como extrair preço de cada loja | **Concluída em 2026-09-11.** `spikes/fase0_probe.py` imprime título + preço à vista das 3 lojas com 0 falhas. Resultados e correções na seção 4 |
| **1 — Núcleo** ✅ | Config, modelos, DB, adapters Kabum + Pichau + Terabyte, normalizador | **Concluída em 2026-09-11.** `--run-once` coletou 843 ofertas reais → 107 mantidas, persistidas em SQLite; 34 testes offline passando |
| **2 — Alertas** ✅ | Motor de alertas + guardrails + notificador com fan-out de destinos | **Concluída em 2026-09-11.** Motor com os 4 guardrails, digest único por rodada, roteamento por destino; 63 testes offline |
| **3 — Container** 🟡 | Dockerfile, compose, scheduler, healthcheck, alertas operacionais | **Em produção desde 2026-09-11.** Build, `--selftest`, `--test-notify` e `--run-once` aprovados no servidor (844 ofertas → 106 mantidas). Fecha ao completar 48h sem intervenção |
| **4 — Extras** | Experimento Zoom/Buscapé (seção 4.1) · comandos `/precos` e `/status` no bot · gráfico de histórico · export CSV | Sob demanda. O experimento do agregador só vira adapter definitivo se trouxer oferta melhor que as 3 lojas diretas |
| **5 — PS5** | Spike das plataformas · adapters do varejo generalista · variantes e bundles · regra 1P | Ver seção 18. **Independente da Fase 4** — pode vir antes |

A Fase 1 já entrega o esquema agnóstico de categoria e o notificador com lista de
destinos, mesmo sem uso imediato. São as duas coisas cuja ausência tornaria a Fase 5
uma refatoração em vez de uma adição.

---

## 16. Riscos e pontos em aberto

| # | Risco | Mitigação |
|---|---|---|
| 1 | Perder uma boa oferta que só existiu na Amazon | Aceito conscientemente no v1 (seção 4.1). Mitigação parcial: experimento com agregador BR na Fase 4 |
| 2 | Mudança de layout quebra um parser | Fixtures + alerta operacional de "zero resultados" |
| 3 | Alerta falso por erro de parsing | Faixa de sanidade + guardrail de queda > 50% |
| 4 | Ruído de alertas | Mínimo histórico + delta mínimo + cooldown + digest único |
| 5 | Preço à vista vs. parcelado inconsistente entre lojas | Sempre normalizar para à vista; guardar ambos para auditoria |
| 6 | Container morre silenciosamente | `restart: unless-stopped` + healthcheck por idade da última rodada |
| 7 | *(Fase 4)* Agregador muda a base do preço que publica e gera "mínimo" falso | Se o experimento virar adapter, marcar a fonte como `price_basis: aggregated` e exigir confirmação na loja de origem antes de alertar |
| 8 | *(Fase 5)* Bundle comparado com console avulso gera "mínimo" inexistente | Série histórica é por SKU; `is_bundle` obrigatório em qualquer agregação (seções 5 e 18) |
| 9 | *(Fase 5)* Filtro de acessório derruba bundles legítimos | "controle" e "jogo" ficam fora da lista de exclusão de propósito; a separação usa sinal positivo **e** lista negra, com teste para cada papel (seção 18.2) |
| 10 | *(Fase 5)* Falha de envio a um destino silencia os demais | Fan-out com erro isolado por destino no `router.py` |

### Perguntas em aberto

**Nenhuma.** As quatro pendências que existiam foram respondidas em 2026-09-11 e
viraram decisões na seção 17. Não há bloqueio para começar a Fase 0.

---

## 17. Decisões já tomadas

- ✅ Escopo do v1: apenas RX 9070 XT e RTX 5070 Ti, todos os fabricantes.
- ✅ Lojas no v1: Kabum, Pichau e Terabyteshop. Amazon fora, e **não** intermediada
  pelo Google Shopping — motivos registrados na seção 4.1. Reavaliação via agregador
  brasileiro (Zoom/Buscapé) fica para a Fase 4.
- ✅ Alertas: novo mínimo histórico **e** volta ao estoque. Sem alerta por % de queda
  nem preço-alvo.
- ✅ Preço de referência: à vista (PIX/boleto); parcelado guardado como secundário.
- ✅ Stack: Python 3.12, SQLite, sem navegador headless no v1. **Nenhuma das três lojas
  do v1 exige JavaScript** — confirmado no spike.
- ✅ Disponibilidade é resolvida por adapter, não por campo universal. Na Kabum, via
  `quantity`; o campo `available` é inútil (seção 7.1).
- ✅ Deploy: container único com volume para o banco.
- ✅ Esquema de dados agnóstico de categoria **já na Fase 1**, para evitar migração
  quando o PS5 entrar.
- ✅ **PS5 (Fase 5):** todas as variantes — Slim Digital, Slim com leitor e Pro —
  **incluindo bundles**, modelados como dimensão própria (`is_bundle`), nunca
  comparados com console avulso.
- ✅ **Somente vendedor 1P** no varejo generalista. Vendedor indeterminado é descartado.
- ✅ **Destino dos alertas:** **todos** os alertas de preço vão para o grupo *Price
  Watcher* — GPU e console, sem divisão por categoria. O chat privado recebe apenas
  alertas operacionais (coletor quebrado), para não duplicar cada aviso a quem já está
  no grupo. Grupo criado, bot dentro, envio validado em 2026-09-11.
- ✅ **Retenção:** histórico mantido indefinidamente, sem poda. O volume é irrisório em
  disco e série longa é justamente o que dá valor ao "mínimo histórico".
- ✅ **Host:** Ubuntu 24.04 LTS amd64, Core 2 Duo, 8 GB (seção 12). Confortável para a
  carga, mas inviabiliza navegador headless — restrição de hardware, não preferência.
- ✅ **Limiar do PS5:** as três variantes usam os mesmos R$ 100 do default global. Sem
  limiar próprio para o Pro; o mecanismo de override por alvo fica disponível se mudar.

---

## 18. Fase 5 — PlayStation 5 e o varejo generalista

### Por que é uma fase separada

O PS5 não é "mais um alvo no YAML". Ele traz três problemas que as lojas de hardware
não têm: **vendedor de marketplace**, **bundles** e **plataformas de e-commerce muito
mais defendidas**. Por isso vira fase própria, depois do v1 estar rodando e provado.

Essa fase **não depende da Fase 4** e pode ser feita antes dela.

### Lojas e plataformas

A observação que muda o custo desta fase: **as cinco lojas não são cinco problemas.**
Elas se agrupam em quatro famílias de plataforma, e adapter se escreve por família,
não por marca.

✅ **Spike executado em 2026-09-11.** Resultado abaixo — a hipótese acertou no
mecanismo e errou no elenco.

| Loja | Resultado | Fonte |
|---|---|---|
| **Casa e Vídeo** | ✅ coletando | API pública de catálogo VTEX |
| **Americanas** | ✅ coletando | API pública de catálogo VTEX |
| Casas Bahia | ❌ descartada | Akamai Bot Manager (exige JS) |
| Ponto | ❌ descartada | Akamai Bot Manager (exige JS) |
| Magalu | ❌ descartada | Akamai Bot Manager (exige JS) |

**O adapter VTEX genérico pagou, e melhor que o previsto.** A hipótese era que ele
cobriria a Casa e Vídeo. Na verdade a **Americanas também roda VTEX** — o HTML dela
declara `window.VTEX_METADATA = {account:'americanas', renderer:'faststore'}` — então
as duas lojas são atendidas por **um único adapter**, parametrizado por domínio. A spec
antes previa um adapter dedicado para a Americanas; não é preciso. Qualquer outra loja
VTEX passa a ser uma entrada de config, sem código.

**Três lojas caíram, e a decisão já estava tomada.** Casas Bahia, Ponto e Magalu ficam
atrás do **Akamai Bot Manager** — a resposta é uma casca de 2 a 6 KB com
`akam-sw.js` e "Powered and protected by Privacy", que só vira conteúdo depois de
executar JavaScript e resolver o desafio. Contornar isso exigiria navegador headless, o
que o Core 2 Duo do servidor não comporta (seção 12). Pela regra da própria seção 11,
**loja que exige JS é loja descartada** — então elas ficam desabilitadas na config, com
o motivo registrado ali.

Vale notar o que isso custa: Casas Bahia e Ponto compartilham dono e catálogo, então a
perda real é de dois grupos de varejo, não de três lojas independentes.

**O que a API VTEX entrega**, por item:

| Campo | Origem |
|---|---|
| Preço à vista | `Installments` → entrada com `PaymentSystemName == "Pix"` e `NumberOfInstallments == 1` |
| Preço cheio | `commertialOffer.Price` |
| Disponibilidade | `IsAvailable` **e** `AvailableQuantity > 0` |
| Vendedor | `sellerId == "1"` é a própria loja; qualquer outro é marketplace |
| SKU | `items[].itemId` |

O PIX não é detalhe: na Casa e Vídeo ele estava **17% abaixo** do `Price`. Usar o campo
óbvio daria o número errado em todo alerta.

### 18.1 Um bug de desenho que o PS5 revelou

O console trouxe à tona um erro que já estava no código e afetava também as GPUs:
**o normalizador descartava qualquer oferta sem preço à vista.**

Parecia razoável — até aparecer o caso real. Na VTEX, item esgotado simplesmente **não
traz `Installments`**, então não tem preço nenhum. Todos os 12 PS5 da Casa e Vídeo caíam
fora por isso. E o efeito não é perder uma linha na base: sem gravar a observação
"indisponível", **nunca existe a transição indisponível → disponível**, e o alerta de
volta ao estoque jamais dispararia. Silenciosamente, para sempre.

A regra corrigida separa os dois casos:

- **Indisponível e sem preço** → estado normal de item esgotado. Registra.
- **Disponível e sem preço** → aí sim é parser quebrado. Descarta e conta.

Nas GPUs o bug estava latente porque as três lojas mandam preço mesmo com estoque zero.
Há teste de regressão para os dois casos.

### Spike da Fase 5 — perguntas e respostas

As cinco perguntas do plano original, respondidas com evidência:

| # | Pergunta | Resposta |
|---|---|---|
| 1 | A API VTEX responde para a Casa e Vídeo, com preço à vista? | **Sim.** 473 KB de JSON; PIX separado em `Installments` |
| 2 | Casas Bahia e Ponto compartilham estrutura? | **Irrelevante:** as duas caem no mesmo bloqueio Akamai |
| 3 | Dá para saber quem é o vendedor? | **Sim**, e de forma limpa: `sellerId == "1"` é 1P |
| 4 | Alguma exige JavaScript? | **Três exigem** — e por isso foram descartadas |
| 5 | Os filtros separam console de acessório sem matar bundles? | **Sim**, depois de ajuste — ver abaixo |

Sobre a pergunta 5, a resposta inicial foi otimista demais e a revisão manual corrigiu —
ver **18.2** logo abaixo.

### 18.3 Revisão do código antes do merge

Passada de revisão sobre o diff inteiro da branch. Quatro defeitos encontrados, todos
da mesma família: **coisa declarada que não era usada**, e por isso invisível para os
testes existentes.

| Defeito | Efeito | Guarda criada |
|---|---|---|
| `--marcar-teste` declarada no argparse e **nunca lida** | flag documentada na ajuda que não fazia nada | Teste que extrai as flags do fonte e exige que cada uma seja consumida |
| `--serve` funcionava por **queda livre** no fim do `main()` | um modo novo esquecido viraria `--serve` por acidente | O mesmo teste acima pegou; agora há `if args.serve` explícito e erro no fim |
| `--selftest` sondava uma **lista fixa de URLs** | as duas lojas VTEX estavam habilitadas e eram ignoradas em silêncio — no comando que existe para validar o ambiente | Passou a sondar pelos adapters reais; teste compara lojas habilitadas com lojas sondadas |
| `--selftest` sondava **só o primeiro alvo** de cada loja | as lojas de GPU eram testadas só com a RX 9070 XT; a RTX 5070 Ti nunca era exercitada, mas a loja aparecia como OK | Sonda todas as combinações loja × alvo; teste exige que as 3 lojas de GPU cubram os 2 alvos |
| Paginação da VTEX comparava **ofertas com tamanho de página** | a janela `_from`/`_to` conta produtos, e um produto pode render várias ofertas | Teste com página cheia de 50 produtos gerando 25 ofertas |

Dois efeitos colaterais bons:

- O `--selftest` agora exercita o **parser**, não só a conectividade. Antes ele dizia
  "a loja respondeu"; agora diz "a loja respondeu e nós entendemos a resposta".
- `--status`, `--selftest` e `--healthcheck` deixaram de exigir os segredos do Telegram.
  Um relatório somente-leitura falhava com *"config.yaml referencia
  ${TELEGRAM_GROUP_CHAT_ID}"* — pedir credencial de notificação para responder "a última
  coleta rodou?" era errado. A validação estrita continua valendo para `--run-once` e
  `--serve`, onde falhar no boot é o comportamento desejado.

Também corrigi o texto do aviso de execução manual. Ele afirmava que *"a queda foi
simulada"*, o que era verdade no teste que eu tinha acabado de fazer e **mentira** numa
execução manual que detectasse queda real. Agora diz apenas de onde a mensagem veio.

### 18.2 Revisão manual dos títulos — o que ela pegou

A definição de pronto exigia "variantes classificadas corretamente numa amostra revisada
à mão". Fiz isso sobre os **88 títulos de console** coletados, e foi a etapa que mais
rendeu — dois problemas passavam despercebidos por todos os testes automatizados.

**1. Acessório virando console.** Entre os 88, estavam mochila, bolsa, cabo flat, **chave
Torx**, dock station, tampas, ventiladores e uma dúzia de *PlayStation Portal* — que é
outro aparelho, não um PS5. Causa: `require_any` aceitava a palavra `console`, e
acessório em português se anuncia como *"para console PS5"*. A palavra não discrimina
nada.

Correção em duas camadas, porque cada uma cobre um caso:

| Camada | Pega | Exemplo |
|---|---|---|
| `require_any` apertado — título **começa** com "console", ou declara capacidade | acessório que ninguém catalogou | *"Luminária decorativa formato console PS5"* |
| `exclude` com substantivos de acessório | acessório já conhecido | *"Chave Torx para abrir Console PS5"* |

Depois da correção, a Americanas caiu de 76 para **40** produtos de console, e o PS5 Pro
de 4 para **2** — os outros dois eram mochilas.

**2. Bundle não detectado.** *"Console Playstation 5 God Of War Ragnarok 825GB Sony"* é
um combo e não tem "+", nem "com", nem a palavra "bundle" — só o nome do jogo. A regra
passou a reconhecer qualquer menção a jogo e uma lista de franquias comuns no varejo
brasileiro.

E um detalhe que só título real ensina: **todo PS5 vem com um controle**. "1 Controle" é
conteúdo de caixa; "2 controles" ou "controle extra" é que indicam combo.

**Os testes não teriam pego nenhum dos dois.** Eles usavam títulos que eu mesmo inventei,
e eu inventei títulos bem-comportados. Pior: ao transformar os achados em teste, a
primeira versão passou **pelo motivo errado** — eu dava preço de acessório aos
acessórios, então eles caíam pela faixa de sanidade antes de o filtro de título ser
exercitado. Os testes agora usam preço de console e leem o alvo do `config.yaml` de
produção, para que teste e aplicação não possam divergir em silêncio.

### Texto original do spike (mantido para referência)

Responder, com evidência:

1. A API pública de catálogo da VTEX responde para a Casa e Vídeo, e traz preço à vista?
2. Casas Bahia e Ponto realmente compartilham a mesma estrutura de resposta?
3. Cada plataforma expõe **quem é o vendedor** de forma parseável? *(Se não expuser, a
   regra 1P da seção 5 descarta tudo daquela loja — e a loja sai da fase.)*
4. Alguma delas exige execução de JavaScript? **Se sim, a loja é descartada** — o CPU
   do servidor não comporta navegador headless (seção 12). Resposta já decidida.
5. Os filtros `require_any` / `exclude` do PS5 separam console de acessório em títulos
   reais das cinco lojas, **sem** derrubar bundles?

### Variantes e bundles

Uma única busca por "playstation 5" retorna todas as variantes, então a classificação
acontece no normalizador, não em buscas separadas. A ordem das regras importa: `Pro`
é testado antes de `Digital`, porque um "PS5 Pro Digital" existe e deve cair em `PRO`.

Cada SKU tem série histórica própria, então um bundle só compete com ele mesmo. O
`bundle_note` aparece na mensagem de alerta para você julgar o valor do combo — essa
avaliação é sua, não do bot.

### Impacto no volume

Cinco lojas × ~2 termos de busca = ~10 requisições por rodada, somadas às ~6 do v1.
Total de ~16 por rodada, 2x/dia. Continua irrisório.

### Riscos específicos da fase

| Risco | Mitigação |
|---|---|
| Anti-bot mais agressivo que nas lojas de hardware | Volume baixíssimo, delays, e a loja é desligável individualmente por config |
| Preço "com cupom" divergindo do preço real | Registrar apenas o preço à vista efetivamente exibido; não tentar aplicar cupom |
| Marketplace disfarçado de 1P | Falha fechada: vendedor indeterminado é descartado (seção 5) |
| Bundle poluindo análise agregada | `is_bundle` é filtro obrigatório em qualquer comparação entre SKUs |
| Grupo virar supergrupo e mudar o `chat_id` | Tratar `migrate_to_chat_id` e logar o id novo (seção 8) |

### Definição de pronto

- Spike respondeu às 5 perguntas acima, e lojas inviáveis foram cortadas explicitamente.
- Adapters das lojas viáveis coletando console avulso **e** bundle, só 1P.
- Variantes classificadas corretamente numa amostra revisada à mão.
- Grupo recebendo alertas de console junto com os de GPU, no mesmo digest.
- 48h rodando sem alerta falso.
