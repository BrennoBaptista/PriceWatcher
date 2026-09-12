"""Contratos entre adapter -> normalizador -> repositorio.

Regra que atravessa o modulo inteiro: **preco e sempre inteiro em centavos**.
Nunca float. Ver secao 6 da SPEC.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Category(StrEnum):
    GPU = "gpu"
    CONSOLE = "console"


class SellerType(StrEnum):
    FIRST_PARTY = "first_party"
    MARKETPLACE = "marketplace"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


# --------------------------------------------------------------------------
# O que um adapter devolve
# --------------------------------------------------------------------------
class RawOffer(BaseModel):
    """Anuncio como a loja o expoe, antes de qualquer interpretacao."""

    store: str
    store_sku: str
    url: str
    title_raw: str

    price_cash: int | None = None
    """A vista (PIX/boleto), em centavos. None quando indisponivel."""

    price_installment: int | None = None
    installments: int | None = None

    available: bool
    """Resolvido pelo adapter. Cada loja tem uma regra diferente -- secao 7.1."""

    seller_type: SellerType = SellerType.UNKNOWN
    seller_name: str | None = None


# --------------------------------------------------------------------------
# O que o normalizador produz
# --------------------------------------------------------------------------
class NormalizedOffer(BaseModel):
    raw: RawOffer

    category: Category
    model_key: str
    brand: str | None = None
    model_line: str | None = None
    is_bundle: bool = False
    bundle_note: str | None = None


# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------
class VariantRule(BaseModel):
    key: str
    match_regex: str | None = None
    default: bool = False

    @field_validator("match_regex")
    @classmethod
    def _compila(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v


class AlertOverride(BaseModel):
    min_observations: int | None = None
    min_drop_percent: float | None = None
    min_drop_brl: int | None = None
    cooldown_hours: int | None = None


class Target(BaseModel):
    """Um produto a monitorar. Agnostico de categoria -- secao 5."""

    id: str
    category: Category
    label: str
    search_terms: list[str] = Field(min_length=1)

    match_regex: str | None = None
    require_all: list[str] = Field(default_factory=list)
    require_any: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    include_bundles: bool = False
    variants: list[VariantRule] = Field(default_factory=list)

    sanity_price_range_brl: tuple[int, int]
    alerts: dict[str, AlertOverride] = Field(default_factory=dict)

    @property
    def sanity_range_cents(self) -> tuple[int, int]:
        lo, hi = self.sanity_price_range_brl
        return lo * 100, hi * 100


class StoreSettings(BaseModel):
    enabled: bool = False
    categories: list[Category] = Field(default_factory=list)
    platform: str | None = None
    base_url: str | None = None

    merchants: list[str] = Field(default_factory=list)
    """Agregador: lojas cujas ofertas aceitamos.

    Existe para **nao contar a mesma loja duas vezes**. O agregador conhece
    KaBuM!, Pichau e Terabyte, que ja raspamos direto e com preco de primeira
    mao -- aceitar as duas fontes geraria alerta duplicado e, pior, deixaria o
    preco de segunda mao competir com o original.
    """

    merchants_auditoria: list[str] = Field(default_factory=list)
    """Agregador: lojas que coletamos direto e usamos para conferir o agregador.

    Nao entram na base nem geram alerta. Servem para o comando de auditoria
    comparar o preco que o agregador atribui a loja com o que buscamos nela.
    """


class NewLowRule(BaseModel):
    enabled: bool = True
    min_observations: int = 3
    min_drop_percent: float = 1.0
    min_drop_brl: int = 100
    cooldown_hours: int = 12


class BackInStockRule(BaseModel):
    enabled: bool = True
    cooldown_hours: int = 24


class AlertsConfig(BaseModel):
    new_low: NewLowRule = Field(default_factory=NewLowRule)
    back_in_stock: BackInStockRule = Field(default_factory=BackInStockRule)


class Destination(BaseModel):
    id: str
    chat_id: str
    categories: list[Category] | None = None
    kinds: list[str] | None = None


class NotifyConfig(BaseModel):
    destinations: list[Destination] = Field(default_factory=list)


class ScheduleConfig(BaseModel):
    times: list[str] = Field(default_factory=lambda: ["08:00", "20:00"])
    jitter_minutes: int = 15


class HttpConfig(BaseModel):
    timeout_seconds: int = 20
    delay_min_seconds: float = 2.0
    delay_max_seconds: float = 6.0
    retries: int = 3


class AppConfig(BaseModel):
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    stores: dict[str, StoreSettings] = Field(default_factory=dict)
    targets: list[Target] = Field(default_factory=list)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)

    def stores_for(self, category: Category) -> list[str]:
        """Lojas habilitadas que atendem esta categoria."""
        return [
            nome
            for nome, cfg in self.stores.items()
            if cfg.enabled and (not cfg.categories or category in cfg.categories)
        ]
