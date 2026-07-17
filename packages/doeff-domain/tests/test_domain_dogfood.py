"""D8 dogfood: doeff-core-effects の語彙を domain 宣言し、検査 (a)(c) を配線する。

かつて MemoDeleteEffect / CacheDeleteEffect は語彙として定義・export されながら
どの handler も処理しない実ドリフトで、known_uncovered として申告されていた
(2026-07-17 調査)。maintainer 裁定 A(2026-07-18)で両 effect の handler が
実装され、stale-ratchet の強制どおり申告は除去された — 検査 (a) は申告なしで
green になる。ratchet 自体の発火は test_stale_known_uncovered_declaration_rejected
が固定する。
"""

import doeff_domain.core_effects_domains as dogfood  # noqa: F401 — import 時に登録
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
from doeff_domain import (
    DomainCheckError,
    assert_domain_covered,
    assert_no_orphan_effects,
    get_domain,
    handled_effects,
    introducing_domain,
)

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
    # 検査 (a): 導入 effects ⊆ handlers の処理集合の和 — 申告なしで green
    assert_domain_covered(get_domain(name))


def test_stale_known_uncovered_declaration_rejected():
    # ratchet 発火の実証: delete effects は被覆済みになったので、旧 known_uncovered
    # 申告を残すと stale として fail する(申告除去を強制する機構が働いている)
    for name, effect in (("doeff-memo", MemoDeleteEffect), ("doeff-cache", CacheDeleteEffect)):
        with pytest.raises(DomainCheckError, match="stale known_uncovered"):
            assert_domain_covered(get_domain(name), known_uncovered=[effect])


def test_no_orphan_effects_in_core_effects():
    # 検査 (c): doeff_core_effects 全体を import 走査 — 全 EffectBase 子孫が
    # いずれかの domain に導入されている
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
    # MemoDeleteEffect 節の追加は構造導出に自動反映される(D6)
    assert derived == frozenset(
        {MemoExistsEffect, MemoGetEffect, MemoPutEffect, MemoDeleteEffect}
    )


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
