"""D8 dogfood: doeff-core-effects の語彙を domain 宣言し、検査 (a)(c) を配線する。

known_uncovered は実在するドリフトの明示申告である: MemoDeleteEffect /
CacheDeleteEffect は語彙として定義・export されているが、doeff-core-effects の
どの handler も処理していない(2026-07-17 調査)。被覆されたら stale 検査が
fail し、この申告の除去を強制する(ratchet)。処置は maintainer 裁定待ち。
"""

import pytest
from doeff_core_effects.cache_effects import CacheDeleteEffect
from doeff_core_effects.effects import Ask, Get, Local, Put
from doeff_core_effects.http_effects import HttpRequest
from doeff_core_effects.memo_effects import (
    MemoDeleteEffect,
    MemoExistsEffect,
    MemoGetEffect,
    MemoPutEffect,
)

import doeff_domain.core_effects_domains as dogfood  # noqa: F401 — import 時に登録
from doeff_domain import (
    DomainCoverageError,
    assert_domain_covered,
    assert_no_orphan_effects,
    get_domain,
    handled_effects,
    introducing_domain,
)

KNOWN_UNCOVERED = {MemoDeleteEffect, CacheDeleteEffect}

DOGFOOD_DOMAIN_NAMES = [
    "doeff-reader",
    "doeff-state",
    "doeff-writer",
    "doeff-slog",
    "doeff-error",
    "doeff-scope",
    "doeff-listen",
    "doeff-await",
    "doeff-scheduler",
    "doeff-http",
    "doeff-memo",
    "doeff-cache",
]


def test_all_dogfood_domains_registered():
    for name in DOGFOOD_DOMAIN_NAMES:
        assert get_domain(name).title


@pytest.mark.parametrize("name", DOGFOOD_DOMAIN_NAMES)
def test_dogfood_coverage_green(name):
    # 検査 (a): 導入 effects ⊆ handlers の処理集合の和(既知ギャップは明示申告)
    domain = get_domain(name)
    gaps = tuple(cls for cls in domain.effects if cls in KNOWN_UNCOVERED)
    assert_domain_covered(domain, known_uncovered=gaps)


def test_known_uncovered_gaps_are_real():
    # 申告なしだと (a) は red — ドリフトが実在する間だけこのピンは立つ
    for name in ("doeff-memo", "doeff-cache"):
        with pytest.raises(DomainCoverageError):
            assert_domain_covered(get_domain(name))


def test_no_orphan_effects_in_core_effects():
    # 検査 (c): doeff_core_effects 全体を import 走査 — 全 EffectBase 子孫が
    # いずれかの domain に導入されている(delete 系も語彙としては導入済み)
    assert_no_orphan_effects(["doeff_core_effects"])


def test_http_and_memo_domains_use_defhandler_derivation():
    # 二層導出の実 dogfood: defhandler 製 factory は注釈なしで __doeff_body__ から導出
    http = get_domain("doeff-http")
    assert len(http.handlers) == 3
    for handler in http.handlers:
        assert not hasattr(handler, "__doeff_handles__")
        derived = handled_effects(handler, vocabulary=http.effects)
        assert derived == frozenset({HttpRequest})

    memo = get_domain("doeff-memo")
    (memo_handler,) = memo.handlers
    assert not hasattr(memo_handler, "__doeff_handles__")
    derived = handled_effects(memo_handler, vocabulary=memo.effects)
    assert derived == frozenset({MemoExistsEffect, MemoGetEffect, MemoPutEffect})


def test_scope_domain_includes_reader_vocabulary():
    scope = get_domain("doeff-scope")
    assert get_domain("doeff-reader") in scope.includes
    # includes は導入ではない — Local の導入元は doeff-scope
    assert introducing_domain(Local).name == "doeff-scope"


def test_raw_handler_annotations_applied_post_hoc():
    # D5: core-effects を編集せず dogfood モジュールから handles() を後付け注釈
    from doeff_core_effects.handlers import lazy_ask, reader, state

    assert handled_effects(reader) == frozenset({Ask})
    assert handled_effects(lazy_ask) == frozenset({Ask, Local})
    assert handled_effects(state) == frozenset({Get, Put})
