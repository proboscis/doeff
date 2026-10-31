"""D8 dogfood: doeff-core-effects の語彙を domain 宣言し、検査 (a)(c) を配線する。

かつて MemoDeleteEffect / CacheDeleteEffect は語彙として定義・export されながら
どの handler も処理しない実ドリフトで、known_uncovered として申告されていた
(2026-07-17 調査)。その後、両 effect の handler が
実装され、stale-ratchet の強制どおり申告は除去された — 検査 (a) は申告なしで
green になる。ratchet 自体の発火は test_stale_known_uncovered_declaration_rejected
が固定する。

表は effect で読み、domain_registry() が process の表(dogfood の module が import の時に載せる所)から答える
(ADR-DOE-API-001 R29)。
"""

import doeff_domain._core_effects_domains as dogfood  # noqa: F401 — import 時に登録
import pytest
from doeff_core_effects import (
    Ask,
    CacheDeleteEffect,
    Get,
    HttpRequest,
    Local,
    MemoDeleteEffect,
    MemoExistsEffect,
    MemoGetEffect,
    MemoPutEffect,
    Put,
)
from doeff_domain import (
    AssertDomainCovered,
    AssertNoOrphanEffects,
    AssertRegisteredDomainsCovered,
    DomainCheckError,
    GetDomain,
    HandledEffects,
    Handles,
    domain_registry,
)
from doeff_domain._registry import introducing_domain

from doeff import run


def ask(effect):
    """effect 1 つを process の表の handler の下で走らせる。"""
    return run(domain_registry()(effect))


def get_domain(name):
    """process の表の domain を名で引く(effect GetDomain)。"""
    return ask(GetDomain(name))


def handled_effects(handler, *, vocabulary=()):
    """handler の処理集合を読む(effect HandledEffects)。"""
    return ask(HandledEffects(handler, vocabulary=list(vocabulary)))


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
    "doeff-outcome",
    "doeff-file",
    "doeff-http-server",
    "doeff-ws-client",
    "doeff-sql",
    "doeff-process",
    "doeff-warm-process",
    "doeff-process-exit",
    "doeff-channel",
    "doeff-compute",
    "doeff-stop-signal",
    "doeff-vm-probe",
]


def test_all_dogfood_domains_registered():
    for name in DOGFOOD_DOMAIN_NAMES:
        assert get_domain(name).title


@pytest.mark.parametrize("name", DOGFOOD_DOMAIN_NAMES)
def test_dogfood_coverage_green(name):
    # 検査 (a): 導入 effects ⊆ handlers の処理集合の和 — 申告なしで green
    ask(AssertDomainCovered(get_domain(name)))


def test_every_dogfood_domain_is_covered_in_a_table_of_their_own():
    # 検査 (a) を表の domain の全部へ — process の表にはほかの test の宣言も載るので、dogfood の domain だけの別の表で走らせる
    dogfood_domains = [get_domain(name) for name in DOGFOOD_DOMAIN_NAMES]
    run(domain_registry(domains=dogfood_domains)(AssertRegisteredDomainsCovered()))


def test_stale_known_uncovered_declaration_rejected():
    # ratchet 発火の実証: delete effects は被覆済みになったので、旧 known_uncovered
    # 申告を残すと stale として fail する(申告除去を強制する機構が働いている)
    for name, effect in (("doeff-memo", MemoDeleteEffect), ("doeff-cache", CacheDeleteEffect)):
        with pytest.raises(DomainCheckError, match="stale known_uncovered"):
            ask(AssertDomainCovered(name, known_uncovered=[effect]))


def test_no_orphan_effects_in_core_effects():
    # 検査 (c): doeff_core_effects 全体を import 走査 — 全 EffectBase 子孫が
    # いずれかの domain に導入されている
    ask(AssertNoOrphanEffects(["doeff_core_effects"]))


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
    assert derived == frozenset({MemoExistsEffect, MemoGetEffect, MemoPutEffect, MemoDeleteEffect})


def test_scope_domain_includes_reader_vocabulary():
    scope = get_domain("doeff-scope")
    assert get_domain("doeff-reader") in scope.includes
    # includes は導入ではない — Local の導入元は doeff-scope
    assert introducing_domain(Local).name == "doeff-scope"


def test_raw_handlers_are_declared_in_the_domain_without_touching_them():
    # D5: core-effects を編集せず、dogfood の domain の handlers に Handles で宣言する — handler に印を付けない
    from doeff_core_effects import lazy_ask, reader, state

    declared = {
        entry.handler: entry
        for name in ("doeff-reader", "doeff-scope", "doeff-state")
        for entry in get_domain(name).handlers
        if isinstance(entry, Handles)
    }
    assert handled_effects(declared[reader]) == frozenset({Ask})
    assert handled_effects(declared[lazy_ask]) == frozenset({Ask, Local})
    assert handled_effects(declared[state]) == frozenset({Get, Put})
    assert not hasattr(reader, "__doeff_handles__")
    assert not hasattr(lazy_ask, "__doeff_handles__")
