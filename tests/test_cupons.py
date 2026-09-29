"""Filtro de relevancia de cupom e persistencia (secao 19 da SPEC)."""

from __future__ import annotations

from pricewatcher.cupons import avalia_cupons, relevancia
from pricewatcher.db import Repo
from pricewatcher.models import Category, CouponCategoryRule, CouponsConfig, RawCoupon

GPU_RULE = CouponCategoryRule(
    require_any=[r"(?i)\bplacas?\s+de\s+v[ií]deo\b", r"(?i)\bgpus?\b", r"(?i)\brtx\b"],
    exclude=[r"(?i)\bplacas?\s+de\s+v[ií]deo\b", r"(?i)\bgpus?\b", r"(?i)\brtx\b"],
)
CONSOLE_RULE = CouponCategoryRule(
    require_any=[r"(?i)\bplaystation\b", r"(?i)\bps5\b", r"(?i)\bconsole\b"],
    exclude=[r"(?i)\bplaystation\b", r"(?i)\bps5\b", r"(?i)\bconsole\b"],
)
CFG = CouponsConfig(categories={Category.GPU: GPU_RULE, Category.CONSOLE: CONSOLE_RULE})


def _cupom(scope="", discount="", terms=None) -> RawCoupon:
    return RawCoupon(
        store="kabum", code="X", discount_text=discount, scope_text=scope,
        terms_text=terms, url="https://x",
    )


def test_relevancia_bate_categoria_pela_descricao():
    cupom = _cupom(scope="em Placas de Vídeo selecionadas")
    assert relevancia(cupom, CFG) == [Category.GPU]


def test_relevancia_ignora_cupom_generico():
    cupom = _cupom(scope="em produtos selecionados")
    assert relevancia(cupom, CFG) == []


def test_relevancia_pode_bater_duas_categorias():
    cupom = _cupom(scope="Placas de Vídeo e Console selecionados")
    assert set(relevancia(cupom, CFG)) == {Category.GPU, Category.CONSOLE}


def test_relevancia_ignora_cupom_que_exclui_categoria_no_termos():
    """Regressao: a Kabum tem cupons genericos cujo texto legal exclui
    Placas de Video explicitamente. Um filtro que so olhasse `scope_text`
    teria falso positivo aqui -- e por isso o `exclude` roda contra
    `terms_text`, nao contra o mesmo texto do `require_any`."""
    cupom = _cupom(
        scope="em Hardware Gamer selecionado, incluindo Placas de Vídeo",
        terms=(
            "IMPORTANTE: cupom nao e valido para Servicos Digitais, "
            "Processadores, Placas de Video, Kit Hardware, Nintendo, "
            "Playstation, Notebooks e toda a categoria de Celular."
        ),
    )
    assert relevancia(cupom, CFG) == []


def test_relevancia_sem_terms_text_nao_se_autocancela():
    """Pichau/Terabyte nao expoem termos por cupom. `exclude` usa a MESMA
    lista de palavras-chave do `require_any` -- se caisse para o proprio
    `scope_text` na ausencia de termos, todo cupom relevante se
    autocancelaria (a descricao que bateu no require_any bateria de novo no
    exclude). Sem `terms_text`, simplesmente nao ha exclusao a aplicar."""
    cupom = _cupom(scope="em Placas de Vídeo selecionadas", terms=None)
    assert relevancia(cupom, CFG) == [Category.GPU]


def test_registra_cupom_marca_novo_na_primeira_vez(tmp_path):
    with Repo(tmp_path / "db.sqlite3") as repo:
        cupom = _cupom(scope="em Placas de Vídeo selecionadas", discount="10% OFF")
        id1, novo1 = repo.registra_cupom(cupom, [Category.GPU])
        id2, novo2 = repo.registra_cupom(cupom, [Category.GPU])
        assert novo1 is True
        assert novo2 is False
        assert id1 == id2


def test_registra_cupom_atualiza_dados_sem_duplicar(tmp_path):
    with Repo(tmp_path / "db.sqlite3") as repo:
        cupom = _cupom(scope="em Placas de Vídeo selecionadas", discount="10% OFF")
        id1, _ = repo.registra_cupom(cupom, [Category.GPU])
        atualizado = _cupom(scope="em Placas de Vídeo selecionadas", discount="15% OFF")
        id2, _ = repo.registra_cupom(atualizado, [Category.GPU])
        assert id1 == id2
        assert repo.cupom(id1)["discount_text"] == "15% OFF"
        assert repo.contagens()["coupon"] == 1


def test_avalia_cupons_aplica_cooldown(tmp_path):
    with Repo(tmp_path / "db.sqlite3") as repo:
        cupom = _cupom(scope="em Placas de Vídeo selecionadas")
        coupon_id, _ = repo.registra_cupom(cupom, [Category.GPU])

        achados = avalia_cupons(repo, [coupon_id], CFG)
        assert len(achados) == 1
        assert achados[0].categorias == [Category.GPU]

        repo.registra_alerta_cupom(coupon_id, entregue=True)
        achados_de_novo = avalia_cupons(repo, [coupon_id], CFG)
        assert achados_de_novo == []
