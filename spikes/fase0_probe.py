"""
Spike da Fase 0 -- valida a estrategia de extracao das tres lojas do v1.

Nao e codigo de producao: sem retry, sem persistencia, sem tratamento fino de
erro. O objetivo e provar que da para obter titulo, preco a vista, SKU e
disponibilidade de cada loja, e registrar COMO.

Uso:  python spikes/fase0_probe.py
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 25

TARGETS = {
    "RX_9070_XT": {
        "termo": "rx 9070 xt",
        "regex": re.compile(r"(?i)\b(rx\s*)?9070\s*xt\b"),
        "faixa": (2000, 15000),
    },
    "RTX_5070_TI": {
        "termo": "rtx 5070 ti",
        "regex": re.compile(r"(?i)\b(rtx\s*)?5070\s*ti\b"),
        "faixa": (2500, 18000),
    },
}

# Titulos que casam o regex mas nao sao uma placa avulsa.
EXCLUI = re.compile(r"(?i)\b(pc\s*gamer|computador|notebook|combo|kit\s*upgrade)\b")


# Pichau e Terabyte devolvem 403 para requisicao sem cara de navegador.
# Nao basta o User-Agent: o conjunto de cabecalhos precisa ser coerente.
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def baixar(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        dados = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            dados = gzip.decompress(dados)
        elif r.headers.get("Content-Encoding") == "deflate":
            dados = zlib.decompress(dados, -zlib.MAX_WBITS)
        return dados.decode("utf-8", errors="replace")


def centavos(v) -> int | None:
    return None if v is None else int(round(float(v) * 100))


# --------------------------------------------------------------------------
# Kabum -- JSON embutido em __NEXT_DATA__ (Next.js Pages Router)
#   a vista .... priceWithDiscount   ('price' e o preco cheio/parcelado)
#   1P ......... sellerId == 0
#   estoque .... quantity > 0        ('available' e True em 100% dos itens)
# --------------------------------------------------------------------------
def kabum(termo: str) -> list[dict]:
    url = "https://www.kabum.com.br/busca/" + urllib.parse.quote(termo)
    html = baixar(url)
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not m:
        raise RuntimeError("__NEXT_DATA__ ausente")
    data = json.loads(m.group(1))
    itens = data["props"]["pageProps"]["data"]["catalogServer"]["data"]
    out = []
    for p in itens:
        out.append(
            {
                "loja": "kabum",
                "sku": str(p["code"]),
                "titulo": p["name"],
                "avista": centavos(p.get("priceWithDiscount")),
                "cheio": centavos(p.get("price")),
                "disponivel": (p.get("quantity") or 0) > 0,
                "primeira_parte": p.get("sellerId") == 0,
                "vendedor": p.get("sellerName"),
                "url": f"https://www.kabum.com.br/produto/{p['code']}/{p.get('friendlyName','')}",
            }
        )
    return out


# --------------------------------------------------------------------------
# Pichau -- payload RSC do Next.js App Router (self.__next_f)
#   NAO existe GraphQL publico: /graphql cai em pagina de manutencao.
#   a vista .... pichau_prices.avista (metodo PIX)
#   estoque .... stock_status == 'IN_STOCK'
# --------------------------------------------------------------------------
def _objeto_ao_redor(s: str, pos: int) -> str | None:
    profundidade = 0
    ini = None
    for i in range(pos, -1, -1):
        if s[i] == "}":
            profundidade += 1
        elif s[i] == "{":
            if profundidade == 0:
                ini = i
                break
            profundidade -= 1
    if ini is None:
        return None
    profundidade = 0
    for j in range(ini, len(s)):
        if s[j] == "{":
            profundidade += 1
        elif s[j] == "}":
            profundidade -= 1
            if profundidade == 0:
                return s[ini : j + 1]
    return None


def pichau(termo: str) -> list[dict]:
    url = "https://www.pichau.com.br/search?q=" + urllib.parse.quote_plus(termo)
    html = baixar(url)
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
    blob = "".join(chunks)
    blob = blob.encode().decode("unicode_escape").encode("latin1", "ignore").decode("utf-8", "ignore")

    out, vistos = [], set()
    for m in re.finditer(r'"pichau_prices"', blob):
        bruto = _objeto_ao_redor(blob, m.start())
        if not bruto:
            continue
        try:
            o = json.loads(bruto)
        except json.JSONDecodeError:
            continue
        sku = o.get("sku")
        if not sku or sku in vistos:
            continue
        vistos.add(sku)
        precos = o.get("pichau_prices") or {}
        out.append(
            {
                "loja": "pichau",
                "sku": sku,
                "titulo": o.get("name") or "",
                "avista": centavos(precos.get("avista")),
                "cheio": centavos(precos.get("base_price")),
                "disponivel": o.get("stock_status") == "IN_STOCK",
                "primeira_parte": True,  # Pichau nao opera marketplace
                "vendedor": "Pichau",
                "url": f"https://www.pichau.com.br/{o.get('url_key','')}",
            }
        )
    return out


# --------------------------------------------------------------------------
# Terabyte -- HTML server-rendered, com atributos de dados no card
#   a vista .... data-tss-price   (confere com "R$ X a vista no Pix")
#   estoque .... data-tss-estoque == "1"  (bate 254/254 com "Esgotado")
#   sku ........ /produto/<id>/<slug>
# --------------------------------------------------------------------------
CARD = re.compile(r'<div class="product-item"(?P<attrs>[^>]*)>(?P<corpo>.*?)(?=<div class="product-item"|\Z)', re.S)


def terabyte(termo: str) -> list[dict]:
    url = "https://www.terabyteshop.com.br/busca?str=" + urllib.parse.quote_plus(termo)
    html = baixar(url)
    out = []
    for m in CARD.finditer(html):
        attrs, corpo = m.group("attrs"), m.group("corpo")
        preco = re.search(r'data-tss-price="([\d.]+)"', attrs)
        estoque = re.search(r'data-tss-estoque="(\d)"', attrs)
        link = re.search(r'href="(/produto/(\d+)/[^"]+)"', corpo)
        titulo = re.search(r'title="([^"]+)"', corpo)
        if not (preco and link and titulo):
            continue
        out.append(
            {
                "loja": "terabyte",
                "sku": link.group(2),
                "titulo": titulo.group(1),
                "avista": centavos(preco.group(1)),
                "cheio": None,
                "disponivel": bool(estoque and estoque.group(1) == "1"),
                "primeira_parte": True,  # Terabyte nao opera marketplace
                "vendedor": "Terabyte",
                "url": "https://www.terabyteshop.com.br" + link.group(1),
            }
        )
    return out


LOJAS = {"kabum": kabum, "pichau": pichau, "terabyte": terabyte}


def main() -> int:
    falhas = 0
    for alvo, cfg in TARGETS.items():
        print("=" * 78)
        print(f"ALVO: {alvo}   (busca: {cfg['termo']!r})")
        print("=" * 78)
        for nome, fn in LOJAS.items():
            try:
                brutos = fn(cfg["termo"])
            except Exception as e:
                print(f"\n  [{nome}] FALHOU: {type(e).__name__}: {e}")
                falhas += 1
                continue

            casam = [p for p in brutos if cfg["regex"].search(p["titulo"])]
            casam = [p for p in casam if not EXCLUI.search(p["titulo"])]
            validos = [
                p
                for p in casam
                if p["primeira_parte"]
                and p["avista"]
                and cfg["faixa"][0] * 100 <= p["avista"] <= cfg["faixa"][1] * 100
            ]
            disp = [p for p in validos if p["disponivel"]]

            print(
                f"\n  [{nome}] {len(brutos)} brutos -> {len(casam)} casam regex "
                f"-> {len(validos)} validos (1P + faixa) -> {len(disp)} disponiveis"
            )
            if not validos:
                print("      NENHUM VALIDO -- sinal de parser quebrado")
                falhas += 1
            for p in sorted(disp, key=lambda x: x["avista"])[:5]:
                print(f"      R$ {p['avista']/100:>9,.2f}  {p['titulo'][:56]}")
            time.sleep(3)
        print()

    print("=" * 78)
    print("FALHAS:", falhas)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
