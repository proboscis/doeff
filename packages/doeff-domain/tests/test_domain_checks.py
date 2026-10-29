"""opt-in 適合検査 — (a) 被覆と (c) 孤児禁止 (ADR-DOE-DOMAIN-001 D7)。

検査は effect AssertDomainCovered・AssertRegisteredDomainsCovered・AssertNoOrphanEffects で、domain_registry が表から
答える(ADR-DOE-API-001 R24・card ki-03a7f0af2b64)。ここでは domain_registry(domains=[…]) の別の表で走らせる。
"""

import doeff_hy  # noqa: F401 — .hy fixture module の import hook 登録(test-only)
import domain_defhandler_fixtures as fixtures
import domain_orphan_scan_fixture as scan_fixture
import pytest
from doeff_domain import (
    AssertDomainCovered,
    AssertNoOrphanEffects,
    AssertRegisteredDomainsCovered,
    Domain,
    DomainCheckError,
    DomainCoverageError,
    Handles,
    OrphanEffectError,
    domain_registry,
)
from domain_test_effects import FixtureAlpha, FixtureBeta, make_effect_class

from doeff import run


def check(effect, *domains: Domain) -> None:
    """検査の effect 1 つを、渡した domain だけの表で走らせる(外れは例外のまま上がる)。"""
    run(domain_registry(domains=list(domains))(effect))


class TestCoverage:
    def test_green_with_defhandler_and_declaration_mix(self):
        extra = make_effect_class("CoverageExtraEffect")

        def extra_handler(body):
            return body

        domain = Domain(
            name="c-green",
            title="covered domain",
            effects=[FixtureAlpha, FixtureBeta, extra],
            handlers=[fixtures.fixture_plain_handler, Handles(extra_handler, extra)],
        )
        check(AssertDomainCovered(domain), domain)
        assert not hasattr(extra_handler, "__doeff_handles__")

    def test_red_lists_missing_effects_and_domain(self):
        domain = Domain(
            name="c-red",
            title="uncovered domain",
            effects=[FixtureAlpha, FixtureBeta],
            handlers=[],
        )
        with pytest.raises(DomainCoverageError) as excinfo:
            check(AssertDomainCovered(domain), domain)
        message = str(excinfo.value)
        assert "c-red" in message
        assert "FixtureAlpha" in message
        assert "FixtureBeta" in message

    def test_accepts_domain_name_string(self):
        domain = Domain(name="c-by-name", title="t", effects=[FixtureAlpha], handlers=[])
        with pytest.raises(DomainCoverageError, match="c-by-name"):
            check(AssertDomainCovered("c-by-name"), domain)

    def test_a_name_outside_the_table_is_a_key_error(self):
        with pytest.raises(KeyError, match="c-missing"):
            check(AssertDomainCovered("c-missing"))

    def test_included_vocabulary_not_required(self):
        # (a) は「導入する」effects のみ対象 — 包含語彙の被覆責務は導入元にある
        home = Domain(
            name="c-home",
            title="t",
            effects=[FixtureAlpha, FixtureBeta],
            handlers=[fixtures.fixture_plain_handler],
        )
        including = Domain(name="c-inc", title="t", includes=[home], handlers=[])
        check(AssertDomainCovered(including), home, including)

    def test_superset_coverage_allowed(self):
        # handler が domain 導入外の effect を余分に処理していても green(⊇ 検査)
        domain = Domain(
            name="c-super",
            title="t",
            effects=[FixtureAlpha],
            handlers=[fixtures.fixture_plain_handler],
        )
        check(AssertDomainCovered(domain), domain)

    def test_known_uncovered_allows_declared_gap(self):
        domain = Domain(
            name="c-gap",
            title="t",
            effects=[FixtureAlpha, FixtureBeta],
            handlers=[],
        )
        check(AssertDomainCovered(domain, known_uncovered=[FixtureAlpha, FixtureBeta]), domain)

    def test_known_uncovered_stale_entry_fails(self):
        # ratchet: 実際には被覆済みの known_uncovered は stale として fail
        domain = Domain(
            name="c-stale",
            title="t",
            effects=[FixtureAlpha],
            handlers=[fixtures.fixture_plain_handler],
        )
        with pytest.raises(DomainCheckError, match="stale"):
            check(AssertDomainCovered(domain, known_uncovered=[FixtureAlpha]), domain)

    def test_underivable_handler_fails_loud(self):
        def anonymous(body):
            return body

        domain = Domain(
            name="c-underivable",
            title="t",
            effects=[FixtureAlpha],
            handlers=[anonymous],
        )
        with pytest.raises(DomainCheckError, match="Handles"):
            check(AssertDomainCovered(domain), domain)

    def test_all_registered_domains_helper(self):
        covered = Domain(
            name="c-all-covered",
            title="t",
            effects=[FixtureAlpha],
            handlers=[fixtures.fixture_plain_handler],
        )
        uncovered = Domain(
            name="c-all-uncovered",
            title="t",
            effects=[make_effect_class("AllHelperEffect")],
            handlers=[],
        )
        with pytest.raises(DomainCoverageError, match="c-all-uncovered"):
            check(AssertRegisteredDomainsCovered(), covered, uncovered)
        check(AssertRegisteredDomainsCovered(), covered)

    def test_known_uncovered_must_be_a_list(self):
        with pytest.raises(TypeError, match="known_uncovered"):
            AssertDomainCovered("c", known_uncovered=FixtureAlpha)  # type: ignore[arg-type]


class TestOrphans:
    def test_red_names_class_and_module(self):
        with pytest.raises(OrphanEffectError) as excinfo:
            check(AssertNoOrphanEffects(["domain_orphan_scan_fixture"]))
        message = str(excinfo.value)
        assert "OwnedFixtureEffect" in message
        assert "StrayFixtureEffect" in message
        assert "domain_orphan_scan_fixture" in message

    def test_green_when_all_introduced(self):
        home = Domain(
            name="o-home",
            title="t",
            effects=[
                scan_fixture.OwnedFixtureEffect,
                scan_fixture.StrayFixtureEffect,
            ],
        )
        check(AssertNoOrphanEffects(["domain_orphan_scan_fixture"]), home)

    def test_reexports_not_counted_as_definitions(self):
        # FixtureAlpha は re-export — 定義モジュールでない走査対象では数えない
        own = Domain(
            name="o-own",
            title="t",
            effects=[
                scan_fixture.OwnedFixtureEffect,
                scan_fixture.StrayFixtureEffect,
            ],
        )
        check(AssertNoOrphanEffects(["domain_orphan_scan_fixture"]), own)

    def test_package_walk_includes_submodules(self):
        with pytest.raises(OrphanEffectError) as excinfo:
            check(AssertNoOrphanEffects(["domain_orphan_pkg"]))
        message = str(excinfo.value)
        assert "PkgRootEffect" in message
        assert "PkgSubEffect" in message

    def test_known_orphans_allowlist(self):
        part = Domain(name="o-part", title="t", effects=[scan_fixture.OwnedFixtureEffect])
        check(
            AssertNoOrphanEffects(
                ["domain_orphan_scan_fixture"],
                known_orphans=[scan_fixture.StrayFixtureEffect],
            ),
            part,
        )

    def test_known_orphans_stale_entry_fails(self):
        full = Domain(
            name="o-full",
            title="t",
            effects=[
                scan_fixture.OwnedFixtureEffect,
                scan_fixture.StrayFixtureEffect,
            ],
        )
        with pytest.raises(DomainCheckError, match="stale"):
            check(
                AssertNoOrphanEffects(
                    ["domain_orphan_scan_fixture"],
                    known_orphans=[scan_fixture.StrayFixtureEffect],
                ),
                full,
            )

    def test_missing_package_fails_loud(self):
        # 読めない module も check の失敗の型 DomainCheckError で届き、元の例外は __cause__ に残る
        with pytest.raises(DomainCheckError, match="no_such_package_doeff_domain_xyz") as excinfo:
            check(AssertNoOrphanEffects(["no_such_package_doeff_domain_xyz"]))
        assert isinstance(excinfo.value.__cause__, ModuleNotFoundError)

    def test_a_module_that_fails_on_import_is_a_check_error(self, tmp_path, monkeypatch):
        (tmp_path / "domain_broken_scan_fixture.py").write_text(
            "raise RuntimeError('broken at import')\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(DomainCheckError, match="domain_broken_scan_fixture") as excinfo:
            check(AssertNoOrphanEffects(["domain_broken_scan_fixture"]))
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_the_failure_is_thrown_where_the_effect_was_performed(self):
        from doeff import do

        @do
        def body():
            try:
                yield AssertNoOrphanEffects(["domain_orphan_scan_fixture"])
            except OrphanEffectError as error:
                return "caught", "StrayFixtureEffect" in str(error)
            return "passed", False

        assert run(domain_registry(domains=[])(body())) == ("caught", True)

    def test_packages_must_be_a_list(self):
        with pytest.raises(TypeError, match="packages"):
            AssertNoOrphanEffects("domain_orphan_scan_fixture")  # type: ignore[arg-type]
