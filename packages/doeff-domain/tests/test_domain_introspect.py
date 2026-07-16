"""handles 注釈と二層の処理集合導出 (ADR-DOE-DOMAIN-001 D5/D6)。

導出テストは実物の defhandler 産物(無引数形・引数形・lazy 節・:when ガード)
に対して行う。doeff-hy はテスト環境からのみ import する — doeff-domain 本体は
属性ダックタイピングで doeff-hy 非依存(D1/D6)。
"""

import doeff_hy  # noqa: F401 — .hy fixture module の import hook 登録(test-only)
import domain_defhandler_fixtures as fixtures
import pytest
from domain_test_effects import (
    FixtureAlpha,
    FixtureBeta,
    FixtureDelta,
    FixtureGamma,
    plain_installer,
)

from doeff_domain import DomainCheckError, DomainDefinitionError, handled_effects, handles


class TestHandlesAnnotation:
    def test_sets_attribute_and_returns_object(self):
        def raw_handler(body):
            return body

        result = handles(FixtureAlpha, FixtureBeta)(raw_handler)
        assert result is raw_handler
        assert raw_handler.__doeff_handles__ == (FixtureAlpha, FixtureBeta)

    def test_applies_post_hoc_to_foreign_function(self):
        # D5: 他モジュールで定義済みの生 handler へ後付け適用できる
        handles(FixtureAlpha)(plain_installer)
        assert handled_effects(plain_installer) == frozenset({FixtureAlpha})

    def test_handles_requires_at_least_one_effect(self):
        with pytest.raises(DomainDefinitionError, match="handles"):
            handles()

    def test_handles_rejects_non_effect_classes(self):
        with pytest.raises(DomainDefinitionError, match="EffectBase"):
            handles("FixtureAlpha")  # type: ignore[arg-type]


class TestDerivationLayers:
    def test_annotation_layer_wins_over_defhandler_body(self):
        handler = fixtures.fixture_plain_handler
        try:
            handles(FixtureDelta)(handler)
            assert handled_effects(handler) == frozenset({FixtureDelta})
        finally:
            del handler.__doeff_handles__
        assert handled_effects(handler) == frozenset({FixtureAlpha, FixtureBeta})

    def test_neither_attribute_fails_loud(self):
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
