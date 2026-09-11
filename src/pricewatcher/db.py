"""Esquema e repositorio SQLite.

Duas regras que o esquema impoe e o codigo nunca deve contornar:

* preco e **inteiro em centavos**, nunca float;
* timestamp e ISO-8601 em **UTC**.

O esquema e agnostico de categoria desde a primeira versao (secao 5 da SPEC):
`category` + `model_key` no lugar de qualquer coisa especifica de GPU.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import NormalizedOffer, RunStatus

log = logging.getLogger(__name__)

VERSAO_ESQUEMA = 1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS product (
    id            INTEGER PRIMARY KEY,
    store         TEXT NOT NULL,
    store_sku     TEXT NOT NULL,
    url           TEXT NOT NULL,
    title_raw     TEXT NOT NULL,

    category      TEXT NOT NULL,
    model_key     TEXT NOT NULL,
    brand         TEXT,
    model_line    TEXT,
    is_bundle     INTEGER NOT NULL DEFAULT 0,
    bundle_note   TEXT,

    seller_type   TEXT NOT NULL,
    seller_name   TEXT,

    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    UNIQUE (store, store_sku)
);
CREATE INDEX IF NOT EXISTS idx_product_model
    ON product(category, model_key, is_bundle);

CREATE TABLE IF NOT EXISTS collection_run (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    store         TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    status        TEXT NOT NULL,
    offers_found  INTEGER,
    offers_kept   INTEGER,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_tempo ON collection_run(started_at DESC);

CREATE TABLE IF NOT EXISTS price_point (
    id                INTEGER PRIMARY KEY,
    product_id        INTEGER NOT NULL REFERENCES product(id),
    observed_at       TEXT NOT NULL,
    price_cash        INTEGER,
    price_installment INTEGER,
    installments      INTEGER,
    available         INTEGER NOT NULL,
    run_id            INTEGER NOT NULL REFERENCES collection_run(id)
);
CREATE INDEX IF NOT EXISTS idx_pp_product_time
    ON price_point(product_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS alert (
    id            INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES product(id),
    kind          TEXT NOT NULL,
    price_cash    INTEGER,
    previous_best INTEGER,
    sent_at       TEXT NOT NULL,
    delivered     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alert_produto ON alert(product_id, kind, sent_at DESC);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repo:
    def __init__(self, caminho: Path) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(caminho)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA foreign_keys=ON")
        self._migra()

    def __enter__(self) -> Repo:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self.con.close()

    def _migra(self) -> None:
        self.con.executescript(ESQUEMA)
        atual = self.con.execute("PRAGMA user_version").fetchone()[0]
        if atual < VERSAO_ESQUEMA:
            self.con.execute(f"PRAGMA user_version={VERSAO_ESQUEMA}")
        self.con.commit()

    # ---------------------------------------------------------------- runs
    def inicia_run(self, store: str, target_id: str) -> int:
        cur = self.con.execute(
            "INSERT INTO collection_run (started_at, store, target_id, status) "
            "VALUES (?,?,?,?)",
            (agora(), store, target_id, RunStatus.OK),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def encerra_run(
        self,
        run_id: int,
        status: RunStatus,
        encontradas: int = 0,
        mantidas: int = 0,
        erro: str | None = None,
    ) -> None:
        self.con.execute(
            "UPDATE collection_run SET finished_at=?, status=?, offers_found=?, "
            "offers_kept=?, error=? WHERE id=?",
            (agora(), status, encontradas, mantidas, erro, run_id),
        )
        self.con.commit()

    # ------------------------------------------------------------ produtos
    def registra(self, oferta: NormalizedOffer, run_id: int) -> int:
        """Insere ou atualiza o produto e grava uma observacao de preco."""
        r = oferta.raw
        ts = agora()
        self.con.execute(
            """
            INSERT INTO product (store, store_sku, url, title_raw, category,
                model_key, brand, model_line, is_bundle, bundle_note,
                seller_type, seller_name, first_seen_at, last_seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(store, store_sku) DO UPDATE SET
                url=excluded.url,
                title_raw=excluded.title_raw,
                category=excluded.category,
                model_key=excluded.model_key,
                brand=excluded.brand,
                model_line=excluded.model_line,
                is_bundle=excluded.is_bundle,
                bundle_note=excluded.bundle_note,
                seller_type=excluded.seller_type,
                seller_name=excluded.seller_name,
                last_seen_at=excluded.last_seen_at
            """,
            (
                r.store, r.store_sku, r.url, r.title_raw, str(oferta.category),
                oferta.model_key, oferta.brand, oferta.model_line,
                int(oferta.is_bundle), oferta.bundle_note,
                str(r.seller_type), r.seller_name, ts, ts,
            ),
        )
        produto_id = int(
            self.con.execute(
                "SELECT id FROM product WHERE store=? AND store_sku=?",
                (r.store, r.store_sku),
            ).fetchone()[0]
        )
        self.con.execute(
            "INSERT INTO price_point (product_id, observed_at, price_cash, "
            "price_installment, installments, available, run_id) VALUES (?,?,?,?,?,?,?)",
            (
                produto_id, ts, r.price_cash, r.price_installment,
                r.installments, int(r.available), run_id,
            ),
        )
        return produto_id

    def commit(self) -> None:
        self.con.commit()

    # -------------------------------------------------------------- alertas
    def produto(self, produto_id: int) -> sqlite3.Row:
        return self.con.execute(
            "SELECT * FROM product WHERE id=?", (produto_id,)
        ).fetchone()

    def historico(self, produto_id: int) -> list[sqlite3.Row]:
        """Observacoes em ordem cronologica. A ultima e a coleta atual."""
        return self.con.execute(
            "SELECT price_cash, available, observed_at FROM price_point "
            "WHERE product_id=? ORDER BY observed_at ASC, id ASC",
            (produto_id,),
        ).fetchall()

    def ultimo_alerta(self, produto_id: int, kind: str) -> str | None:
        linha = self.con.execute(
            "SELECT sent_at FROM alert WHERE product_id=? AND kind=? "
            "ORDER BY sent_at DESC LIMIT 1",
            (produto_id, kind),
        ).fetchone()
        return linha[0] if linha else None

    def registra_alerta(
        self,
        produto_id: int,
        kind: str,
        price_cash: int | None,
        previous_best: int | None,
        entregue: bool,
    ) -> None:
        self.con.execute(
            "INSERT INTO alert (product_id, kind, price_cash, previous_best, "
            "sent_at, delivered) VALUES (?,?,?,?,?,?)",
            (produto_id, kind, price_cash, previous_best, agora(), int(entregue)),
        )
        self.con.commit()

    def falhas_consecutivas(self, minimo: int = 2) -> list[tuple[str, str, str]]:
        """(loja, alvo, erro) que falharam nas N ultimas rodadas seguidas."""
        pares = self.con.execute(
            "SELECT DISTINCT store, target_id FROM collection_run"
        ).fetchall()
        ruins = []
        for store, target_id in pares:
            ult = self.con.execute(
                "SELECT status, error FROM collection_run WHERE store=? AND target_id=? "
                "ORDER BY started_at DESC, id DESC LIMIT ?",
                (store, target_id, minimo),
            ).fetchall()
            if len(ult) == minimo and all(r["status"] == RunStatus.FAILED for r in ult):
                ruins.append((store, target_id, ult[0]["error"] or "sem detalhe"))
        return ruins

    # -------------------------------------------------------------- leitura
    def resumo(self) -> list[sqlite3.Row]:
        return self.con.execute(
            """
            SELECT p.category, p.model_key, p.store, COUNT(*) AS anuncios,
                   MIN(CASE WHEN pp.available THEN pp.price_cash END) AS menor_disponivel
            FROM product p
            JOIN price_point pp ON pp.product_id = p.id
            WHERE pp.observed_at = (
                SELECT MAX(observed_at) FROM price_point WHERE product_id = p.id
            )
            GROUP BY p.category, p.model_key, p.store
            ORDER BY p.category, p.model_key, menor_disponivel
            """
        ).fetchall()

    def contagens(self) -> dict[str, int]:
        t = {}
        for tabela in ("product", "price_point", "collection_run"):
            t[tabela] = self.con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
        return t
