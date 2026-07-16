"""opt-in 適合検査 — (a) 被覆と (c) 孤児禁止 (ADR-DOE-DOMAIN-001 D7)。"""

import doeff_hy  # noqa: F401 — .hy fixture module の import hook 登録(test-only)
import domain_defhandler_fixtures as fixtures
import domain_orphan_scan_fixture as scan_fixture
import pytest
from domain_test_effects import FixtureAlpha, FixtureBeta, make_effect_class

from doeff_domain import (
    Domain,
    DomainCheckError,
    DomainCoverageError,
    OrphanEffectError,
    assert_domain_covered,
    assert_no_orphan_effects,
    assert_registered_domains_covered,
    handles,
    isolated_registry,
    register_domain,
)


class TestCoverage:
    def test_green_with_defhandler_and_annotation_mix(self):
        extra = make_effect_class("CoverageExtraEffect")

        def extra_handler(body):
            return body

        handles(extra)(extra_handler)
        with isolated_registry():
            domain = register_domain(
                Domain(
                    name="c-green",
                    title="covered domain",
                    effects=[FixtureAlpha, FixtureBeta, extra],
                    handlers=[fixtures.fixture_plain_handler, extra_handler],
                )
            )
            assert_domain_covered(domain)

    def test_red_lists_missing_effects_and_domain(self):
        with isolated_registry():
            domain = register_domain(
                Domain(
                    name="c-red",
                    title="uncovered domain",
                    effects=[FixtureAlpha, FixtureBeta],
                    handlers=[],
                )
            )
            with pytest.raises(DomainCoverageError) as excinfo:
                assert_domain_covered(domain)
            message = str(excinfo.value)
            assert "c-red" in message
            assert "FixtureAlpha" in message
            assert "FixtureBeta" in message

    def test_accepts_domain_name_string(self):
        with isolated_registry():
            register_domain(
                Domain(name="c-by-name", title="t", effects=[FixtureAlpha], handlers=[])
            )
            with pytest.raises(DomainCoverageError, match="c-by-name"):
                assert_domain_covered("c-by-name")

    def test_included_vocabulary_not_required(self):
        # (a) は「導入する」effects のみ対象 — 包含語彙の被覆責務は導入元にある
        with isolated_registry():
            home = register_domain(
                Domain(
                    name="c-home",
                    title="t",
                    effects=[FixtureAlpha, FixtureBeta],
                    handlers=[fixtures.fixture_plain_handler],
                )
            )
            including = register_domain(
                Domain(name="c-inc", title="t", includes=[home], handlers=[])
            )
            assert_domain_covered(including)

    def test_superset_coverage_allowed(self):
        # handler が domain 導入外の effect を余分に処理していても green(⊇ 検査)
        with isolated_registry():
            domain = register_domain(
                Domain(
                    name="c-super",
                    title="t",
                    effects=[FixtureAlpha],
                    handlers=[fixtures.fixture_plain_handler],
                )
            )
            assert_domain_covered(domain)

    def test_known_uncovered_allows_declared_gap(self):
        with isolated_registry():
            domain = register_domain(
                Domain(
                    name="c-gap",
                    title="t",
                    effects=[FixtureAlpha, FixtureBeta],
                    handlers=[],
                )
            )
            assert_domain_covered(domain, known_uncovered=[FixtureAlpha, FixtureBeta])

    def test_known_uncovered_stale_entry_fails(self):
        # ratchet: 実際には被覆済みの known_uncovered は stale として fail
        with isolated_registry():
            domain = register_domain(
                Domain(
                    name="c-stale",
                    title="t",
                    effects=[FixtureAlpha],
                    handlers=[fixtures.fixture_plain_handler],
                )
            )
            with pytest.raises(DomainCheckError, match="stale"):
                assert_domain_covered(domain, known_uncovered=[FixtureAlpha])

    def test_underivable_handler_fails_loud(self):
        def anonymous(body):
            return body

        with isolated_registry():
            domain = register_domain(
                Domain(
                    name="c-underivable",
                    title="t",
                    effects=[FixtureAlpha],
                    handlers=[anonymous],
                )
            )
            with pytest.raises(DomainCheckError, match="__doeff_handles__"):
                assert_domain_covered(domain)

    def test_all_registered_domains_helper(self):
        with isolated_registry():
            register_domain(
                Domain(
                    name="c-all-covered",
                    title="t",
                    effects=[FixtureAlpha],
                    handlers=[fixtures.fixture_plain_handler],
                )
            )
            register_domain(
                Domain(
                    name="c-all-uncovered",
                    title="t",
                    effects=[make_effect_class("AllHelperEffect")],
                    handlers=[],
                )
            )
            with pytest.raises(DomainCoverageError, match="c-all-uncovered"):
                assert_registered_domains_covered()


class TestOrphans:
    def test_red_names_class_and_module(self):
        with isolated_registry():
            with pytest.raises(OrphanEffectError) as excinfo:
                assert_no_orphan_effects(["domain_orphan_scan_fixture"])
            message = str(excinfo.value)
            assert "OwnedFixtureEffect" in message
            assert "StrayFixtureEffect" in message
            assert "domain_orphan_scan_fixture" in message

    def test_green_when_all_introduced(self):
        with isolated_registry():
            register_domain(
                Domain(
                    name="o-home",
                    title="t",
                    effects=[
                        scan_fixture.OwnedFixtureEffect,
                        scan_fixture.StrayFixtureEffect,
                    ],
                )
            )
            assert_no_orphan_effects(["domain_orphan_scan_fixture"])

    def test_reexports_not_counted_as_definitions(self):
        # FixtureAlpha は re-export — 定義モジュールでない走査対象では数えない
        with isolated_registry():
            register_domain(
                Domain(
                    name="o-own",
                    title="t",
                    effects=[
                        scan_fixture.OwnedFixtureEffect,
                        scan_fixture.StrayFixtureEffect,
                    ],
                )
            )
            assert_no_orphan_effects(["domain_orphan_scan_fixture"])

    def test_package_walk_includes_submodules(self):
        with isolated_registry():
            with pytest.raises(OrphanEffectError) as excinfo:
                assert_no_orphan_effects(["domain_orphan_pkg"])
            message = str(excinfo.value)
            assert "PkgRootEffect" in message
            assert "PkgSubEffect" in message

    def test_known_orphans_allowlist(self):
        with isolated_registry():
            register_domain(
                Domain(name="o-part", title="t", effects=[scan_fixture.OwnedFixtureEffect])
            )
            assert_no_orphan_effects(
                ["domain_orphan_scan_fixture"],
                known_orphans=[scan_fixture.StrayFixtureEffect],
            )

    def test_known_orphans_stale_entry_fails(self):
        with isolated_registry():
            register_domain(
                Domain(
                    name="o-full",
                    title="t",
                    effects=[
                        scan_fixture.OwnedFixtureEffect,
                        scan_fixture.StrayFixtureEffect,
                    ],
                )
            )
            with pytest.raises(DomainCheckError, match="stale"):
                assert_no_orphan_effects(
                    ["domain_orphan_scan_fixture"],
                    known_orphans=[scan_fixture.StrayFixtureEffect],
                )

    def test_missing_package_fails_loud(self):
        with isolated_registry():
            with pytest.raises(ModuleNotFoundError):
                assert_no_orphan_effects(["no_such_package_doeff_domain_xyz"])
