"""Handles 宣言と二層の処理集合導出 (ADR-DOE-DOMAIN-001 D5/D6)。

導出は effect HandledEffects で読み、domain_registry が答える(ADR-DOE-API-001 R29・card ki-03a7f0af2b64)。生 Python
handler は domain の handlers に宣言の型 Handles(handler, effect …) で置く(前の関数 handles() は handler に印を後付けした)。

導出テストは実物の defhandler 産物(無引数形・引数形・lazy 節・:when ガード)
に対して行う。doeff-hy はテスト環境からのみ import する — doeff-domain 本体は
属性ダックタイピングで doeff-hy 非依存(D1/D6)。
"""

import doeff_hy  # noqa: F401 — .hy fixture module の import hook 登録(test-only)
import domain_defhandler_fixtures as fixtures
import pytest
from doeff_domain import (
    DomainCheckError,
    DomainDefinitionError,
    HandledEffects,
    Handles,
    domain_registry,
)
from domain_test_effects import (
    FixtureAlpha,
    FixtureBeta,
    FixtureDelta,
    FixtureGamma,
    plain_installer,
)

from doeff import run


def handled_effects(handler, *, vocabulary=()) -> frozenset[type]:
    """effect HandledEffects を domain_registry の下で走らせて、handler の処理集合を読む。"""
    return run(domain_registry(domains=[])(HandledEffects(handler, vocabulary=list(vocabulary))))


class TestHandlesDeclaration:
    def test_holds_the_handler_and_the_effects(self):
        def raw_handler(body):
            return body

        declared = Handles(raw_handler, FixtureAlpha, FixtureBeta)
        assert declared.handler is raw_handler
        assert declared.effects == (FixtureAlpha, FixtureBeta)
        assert handled_effects(declared) == frozenset({FixtureAlpha, FixtureBeta})

    def test_declares_a_foreign_function_without_touching_it(self):
        # D5: 他モジュールで定義済みの生 handler を、その handler に何も付けずに宣言できる
        declared = Handles(plain_installer, FixtureAlpha)
        assert handled_effects(declared) == frozenset({FixtureAlpha})
        assert not hasattr(plain_installer, "__doeff_handles__")

    def test_handles_requires_at_least_one_effect(self):
        with pytest.raises(DomainDefinitionError, match="Handles"):
            Handles(plain_installer)

    def test_handles_rejects_non_effect_classes(self):
        with pytest.raises(DomainDefinitionError, match="EffectBase"):
            Handles(plain_installer, "FixtureAlpha")  # type: ignore[arg-type]

    def test_handles_is_a_frozen_value(self):
        declared = Handles(plain_installer, FixtureAlpha)
        assert declared == Handles(plain_installer, FixtureAlpha)
        with pytest.raises(AttributeError):
            declared.effects = (FixtureBeta,)  # type: ignore[misc]


class TestDerivationLayers:
    def test_the_declaration_wins_over_defhandler_body(self):
        handler = fixtures.fixture_plain_handler
        assert handled_effects(Handles(handler, FixtureDelta)) == frozenset({FixtureDelta})
        assert handled_effects(handler) == frozenset({FixtureAlpha, FixtureBeta})

    def test_the_handlers_own_mark_is_read(self):
        # core の installer が付ける __doeff_handles__ の印は、宣言の無い handler の処理集合として読む
        def marked(body):
            return body

        marked.__doeff_handles__ = (FixtureGamma,)
        assert handled_effects(marked) == frozenset({FixtureGamma})

    def test_neither_declaration_nor_attribute_fails_loud(self):
        def anonymous(body):
            return body

        with pytest.raises(DomainCheckError, match="__doeff_handles__"):
            handled_effects(anonymous)


class TestDefhandlerDerivation:
    def test_no_arg_form(self):
        derived = handled_effects(fixtures.fixture_plain_handler)
        assert derived == frozenset({FixtureAlpha, FixtureBeta})

    def test_factory_form(self):
        derived = handled_effects(fixtures.fixture_factory_handler)
        assert derived == frozenset({FixtureAlpha, FixtureBeta})

    def test_lazy_clause_skipped(self):
        derived = handled_effects(fixtures.fixture_lazy_handler)
        assert derived == frozenset({FixtureGamma})

    def test_lazy_val_clause_skipped(self):
        # lazy 節 head は 3 種(lazy / lazy-val / lazy-var)— 全て処理宣言ではない
        derived = handled_effects(fixtures.fixture_lazy_val_handler)
        assert derived == frozenset({FixtureAlpha})

    def test_lazy_var_clause_skipped(self):
        derived = handled_effects(fixtures.fixture_lazy_var_handler)
        assert derived == frozenset({FixtureBeta})

    def test_session_clause_skipped(self):
        # session val / session var(lazy の後継の書き方)も処理宣言ではない
        derived = handled_effects(fixtures.fixture_session_handler)
        assert derived == frozenset({FixtureGamma})

    def test_when_guard_counts_as_participation(self):
        derived = handled_effects(fixtures.fixture_guarded_handler)
        assert derived == frozenset({FixtureAlpha, FixtureDelta})

    def test_vocabulary_fallback_when_module_attr_missing(self, monkeypatch):
        # D6: sys.modules 属性で解決できない節は照合先 domain の effect クラス名と
        # 文字列一致でフォールバックする
        monkeypatch.delattr(fixtures, "FixtureAlpha")
        derived = handled_effects(
            fixtures.fixture_plain_handler, vocabulary=[FixtureAlpha, FixtureBeta]
        )
        assert derived == frozenset({FixtureAlpha, FixtureBeta})

    def test_unresolvable_clause_fails_loud(self, monkeypatch):
        # D6: どちらの解決も失敗したら検査エラー(黙って落とさない)
        monkeypatch.delattr(fixtures, "FixtureAlpha")
        with pytest.raises(DomainCheckError) as excinfo:
            handled_effects(fixtures.fixture_plain_handler, vocabulary=[FixtureBeta])
        message = str(excinfo.value)
        assert "FixtureAlpha" in message
        assert "fixture-plain-handler" in message
