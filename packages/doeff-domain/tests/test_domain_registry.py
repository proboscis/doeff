"""Domain データ構造と表の意味論 (ADR-DOE-DOMAIN-001 D2/D3)。

表は effect RegisterDomain・GetDomain で読み書きし、handler を返す工場 domain_registry が答える(ADR-DOE-API-001 R29・
card ki-03a7f0af2b64)。domain_registry() は process の表(defdomain が載せる所)、domain_registry(domains=[…]) は渡した
domain だけの別の表から答える。
"""

import pytest
from doeff_domain import (
    Domain,
    DomainDefinitionError,
    DomainLaw,
    DomainTerm,
    DuplicateDomainNameError,
    DuplicateEffectIntroductionError,
    GetDomain,
    RegisterDomain,
    domain_registry,
)
from doeff_domain._registry import isolated_registry, register_domain
from domain_test_effects import FixtureAlpha, FixtureBeta, make_effect_class

from doeff import do, run


def make_domain(name="d-fixture", **kwargs):
    defaults = {"title": "fixture domain"}
    defaults.update(kwargs)
    return Domain(name=name, **defaults)


class TestDomainData:
    def test_domain_is_frozen(self):
        domain = make_domain()
        with pytest.raises(AttributeError):
            domain.title = "mutated"  # type: ignore[misc]

    def test_sequences_coerced_to_tuples(self):
        domain = make_domain(effects=[FixtureAlpha], adrs=["ADR-X"])
        assert domain.effects == (FixtureAlpha,)
        assert isinstance(domain.effects, tuple)
        assert domain.adrs == ("ADR-X",)

    def test_effects_must_be_classes_not_strings(self):
        # D2: 実クラス参照のみ — 文字列は import が壊れても fail しないので禁止
        with pytest.raises(DomainDefinitionError, match="FixtureAlpha"):
            make_domain(effects=["FixtureAlpha"])

    def test_effects_must_be_effectbase_subclasses(self):
        with pytest.raises(DomainDefinitionError, match="EffectBase"):
            make_domain(effects=[int])

    def test_duplicate_effect_within_domain_rejected(self):
        with pytest.raises(DomainDefinitionError, match="FixtureAlpha"):
            make_domain(effects=[FixtureAlpha, FixtureAlpha])

    def test_includes_must_be_domain_instances(self):
        with pytest.raises(DomainDefinitionError, match="includes"):
            make_domain(includes=["other-domain"])

    def test_terms_and_laws_types_validated(self):
        with pytest.raises(DomainDefinitionError, match="terms"):
            make_domain(terms=["is-terminal"])
        with pytest.raises(DomainDefinitionError, match="laws"):
            make_domain(laws=["single-home"])

    def test_term_and_law_payloads(self):
        term = DomainTerm(
            name="is-terminal", home="pkg.predicates", description="canonical predicate"
        )
        law = DomainLaw(
            name="single-home",
            statement="for_all v: canonical_declaration_count(v) == 1",
            counterexamples=("ACP core#8",),
        )
        domain = make_domain(terms=[term], laws=[law])
        assert domain.terms[0].home == "pkg.predicates"
        assert domain.laws[0].counterexamples == ("ACP core#8",)


@do
def _introducing(table_domains, effect):
    """effect を導入する domain の名を、表の domain を GetDomain で読んで探す(表の外の関数 introducing_domain の代わり)。"""
    homes = []
    for domain in table_domains:
        read = yield GetDomain(domain.name)
        if effect in read.effects:
            homes = [*homes, read.name]
    return homes


class TestRegistration:
    def test_register_and_lookup(self):
        domain = make_domain(name="d-reg", effects=[FixtureAlpha])

        @do
        def body():
            registered = yield RegisterDomain(domain)
            found = yield GetDomain("d-reg")
            return registered, found

        registered, found = run(domain_registry(domains=[])(body()))
        assert registered is domain
        assert found is domain

    def test_same_name_reregistration_raises(self):
        # D2: 同名 domain の再登録は即例外 — RegisterDomain を出した所で受けられる
        @do
        def body():
            yield RegisterDomain(make_domain(name="d-dup"))
            try:
                yield RegisterDomain(make_domain(name="d-dup"))
            except DuplicateDomainNameError as error:
                return str(error)
            return "registered twice"

        assert "d-dup" in run(domain_registry(domains=[])(body()))

    def test_second_introduction_raises_with_both_domain_names(self):
        # D3: 導入 1 / 包含 ∞ — 2 つ目の導入は両 domain 名入りの例外
        @do
        def body():
            yield RegisterDomain(make_domain(name="d-first", effects=[FixtureAlpha]))
            yield RegisterDomain(make_domain(name="d-second", effects=[FixtureAlpha]))

        with pytest.raises(DuplicateEffectIntroductionError) as excinfo:
            run(domain_registry(domains=[])(body()))
        message = str(excinfo.value)
        assert "d-first" in message
        assert "d-second" in message
        assert "FixtureAlpha" in message

    def test_includes_do_not_introduce(self):
        # 包含はいくつでも可 — 導入にはならない
        home = make_domain(name="d-home", effects=[FixtureAlpha])
        inc1 = make_domain(name="d-inc1", includes=[home])
        inc2 = make_domain(name="d-inc2", includes=[home])
        homes = run(
            domain_registry(domains=[home, inc1, inc2])(
                _introducing([home, inc1, inc2], FixtureAlpha)
            )
        )
        assert homes == ["d-home"]

    def test_introduction_keyed_by_class_identity_not_name(self):
        # D3: キーはクラス同一性 — 同名の別クラスは両方導入できる
        first = make_effect_class("SameNameEffect")
        second = make_effect_class("SameNameEffect")
        id1 = make_domain(name="d-id1", effects=[first])
        id2 = make_domain(name="d-id2", effects=[second])
        answer = domain_registry(domains=[id1, id2])
        assert run(answer(_introducing([id1, id2], first))) == ["d-id1"]
        assert run(answer(_introducing([id1, id2], second))) == ["d-id2"]

    def test_failed_registration_is_atomic(self):
        @do
        def body():
            yield RegisterDomain(make_domain(name="d-atomic", effects=[FixtureAlpha]))
            try:
                yield RegisterDomain(
                    make_domain(name="d-atomic2", effects=[FixtureBeta, FixtureAlpha])
                )
            except DuplicateEffectIntroductionError:
                refused = True
            else:
                refused = False
            assert refused
            try:
                yield GetDomain("d-atomic2")
            except KeyError:
                pass
            else:
                return "the failed domain was left behind"
            # FixtureBeta の導入も残らない — もう一度導入できる
            again = yield RegisterDomain(make_domain(name="d-beta", effects=[FixtureBeta]))
            return again.name

        assert run(domain_registry(domains=[])(body())) == "d-beta"

    def test_get_domain_unknown_name_fails_loud(self):
        with pytest.raises(KeyError, match="no-such-domain"):
            run(domain_registry(domains=[])(GetDomain("no-such-domain")))

    def test_a_given_table_with_a_double_introduction_is_refused_when_made(self):
        with pytest.raises(DuplicateEffectIntroductionError):
            domain_registry(
                domains=[
                    make_domain(name="d-a", effects=[FixtureAlpha]),
                    make_domain(name="d-b", effects=[FixtureAlpha]),
                ]
            )

    def test_domains_must_be_a_list(self):
        with pytest.raises(TypeError, match="list of Domain"):
            domain_registry(domains=make_domain())  # type: ignore[arg-type]


class TestTableSwap:
    """test の中で表を差し替えられる(受入の条 cc54652e3 — 別の表を据えると effect がその表から答える)。"""

    def test_the_process_table_answers_by_default(self):
        with isolated_registry():
            register_domain(make_domain(name="d-process", title="from the process table"))
            assert run(domain_registry()(GetDomain("d-process"))).title == "from the process table"

    def test_the_process_table_is_read_when_the_effect_is_answered(self):
        """handler を作った後に載った domain も答える(fixture が handler を作るのは宣言の import の前でもよい)。"""
        answer = domain_registry()
        with isolated_registry():
            register_domain(make_domain(name="d-later", title="registered later"))
            assert run(answer(GetDomain("d-later"))).title == "registered later"

    def test_a_given_table_replaces_the_process_table(self):
        table = [
            make_domain(name="d-process", title="from the table"),
            make_domain(name="d-table-only", title="only in the table"),
        ]
        with isolated_registry():
            register_domain(make_domain(name="d-process", title="from the process table"))
            answer = domain_registry(domains=table)
            assert run(answer(GetDomain("d-process"))).title == "from the table"
            assert run(answer(GetDomain("d-table-only"))).title == "only in the table"
            assert run(domain_registry()(GetDomain("d-process"))).title == "from the process table"

    def test_an_inner_table_answers_before_the_outer_table(self):
        """外に既定の domain_registry() を据えたまま、program の一部だけを別の表で走らせられる。"""
        table = [make_domain(name="d-process", title="from the table")]

        @do
        def body():
            outer = yield GetDomain("d-process")
            inner = yield domain_registry(domains=table)(GetDomain("d-process"))
            return outer.title, inner.title

        with isolated_registry():
            register_domain(make_domain(name="d-process", title="from the process table"))
            assert run(domain_registry()(body())) == ("from the process table", "from the table")

    def test_a_name_outside_the_table_is_a_key_error_not_the_outer_answer(self):
        @do
        def body():
            try:
                yield GetDomain("d-process")
            except KeyError as error:
                return "KeyError", str(error)
            return "answered", ""

        with isolated_registry():
            register_domain(make_domain(name="d-process"))
            kind, message = run(domain_registry()(domain_registry(domains=[])(body())))
        assert kind == "KeyError"
        assert "d-process" in message

    def test_register_domain_into_a_given_table_does_not_reach_the_process_table(self):
        with isolated_registry():
            run(domain_registry(domains=[])(RegisterDomain(make_domain(name="d-scratch"))))
            with pytest.raises(KeyError, match="d-scratch"):
                run(domain_registry()(GetDomain("d-scratch")))
