"""語彙のまとまり(domain)の表に置く・引く・check する effect と、表から答える handler を返す工場 domain_registry。

決め: 依頼書 lt-71XZ9Z3RP0W3V2WFN3R7TW6SSV の問い domain の答え lt-FP17P0A4CPBC1RJCF9BFBVF4WV(effect にする・card
ki-03a7f0af2b64・ADR-DOE-API-001 R24)。前の関数 8 個(register_domain・isolated_registry・get_domain・handles・handled_effects・
assert_domain_covered・assert_registered_domains_covered・assert_no_orphan_effects)は公開の名から消した — 本体は内側の
_registry・_checks・_introspect に在り、handler がそれで答える。isolated_registry は domain_registry(domains=[]) が代わり、
handles は宣言の型 Handles になった(_introspect の docstring)。

宣言 defdomain は今のまま import の時に process の表へ載せる。program は表を effect で読み、domain_registry() は process の表を
答える時に読み、domain_registry(domains=[domain, …]) は渡した domain だけを載せた別の表から答える(RegisterDomain もその表へ
載せ、process の表へ落ちない)。内側に据えた方が先に答えるので、test は program の一部だけを別の表で走らせられる。
check の失敗は DomainError の子(走査で読めない module も DomainCheckError に包む — 元の例外は __cause__)、表に無い名は
KeyError で、どちらも effect を出した所へ投げ返す。
"""

from dataclasses import dataclass

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_domain._checks import (
    assert_domain_covered,
    assert_no_orphan_effects,
    assert_registered_domains_covered,
)
from doeff_domain._introspect import handled_effects
from doeff_domain._registry import PROCESS_TABLE, Domain, DomainError, DomainTable


def _types(values: object, field: str) -> tuple[type, ...]:
    """effect の欄に渡した class の並びを tuple に揃える(list でも書けるように — 作る時に形を断る)。"""
    if not isinstance(values, list | tuple):
        raise TypeError(f"{field} takes a list of effect classes, got {type(values).__name__}")
    return tuple(values)


@dataclass(frozen=True)
class RegisterDomain(EffectBase[Domain]):
    """domain を表に載せる: yield RegisterDomain(domain) → 載せた Domain。同じ名・2 度目の導入は DomainRegistrationError の子。"""

    domain: Domain


@dataclass(frozen=True)
class GetDomain(EffectBase[Domain]):
    """名で domain を引く: yield GetDomain("doeff-reader") → Domain。載っていない名は KeyError。"""

    name: str


@dataclass(frozen=True)
class HandledEffects(EffectBase[frozenset[type]]):
    """domain の handlers の 1 つが答える effect の class を読む: yield HandledEffects(handler, vocabulary=(…)) → frozenset。

    handler は domain の handlers の 1 つ — Handles(handler, …) の宣言か、__doeff_handles__ の印か defhandler の節を持つ handler。読めなければ
    DomainCheckError。vocabulary は節の head の名を解く時の控え(普通は照らす domain の effects)。
    """

    handler: object
    vocabulary: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "vocabulary", _types(self.vocabulary, "HandledEffects.vocabulary"))


@dataclass(frozen=True)
class AssertDomainCovered(EffectBase[None]):
    """check (a): domain の handlers が、domain の導入する effect を全部答える。外れは DomainCoverageError。

    domain は Domain か、表から引く名。known_uncovered は申告した穴(もう穴でなければ stale の DomainCheckError)。
    """

    domain: Domain | str
    known_uncovered: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "known_uncovered",
            _types(self.known_uncovered, "AssertDomainCovered.known_uncovered"),
        )


@dataclass(frozen=True)
class AssertRegisteredDomainsCovered(EffectBase[None]):
    """check (a) を表の domain の全部に当てる。外れは DomainCoverageError。"""

    known_uncovered: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "known_uncovered",
            _types(self.known_uncovered, "AssertRegisteredDomainsCovered.known_uncovered"),
        )


@dataclass(frozen=True)
class AssertNoOrphanEffects(EffectBase[None]):
    """check (c): 名指した package の EffectBase の子が、どれも表のどれかの domain に導入されている。外れは OrphanEffectError。

    packages は module か package の import の名(package は下まで歩く)。known_orphans は申告した孤児(もう孤児でなければ
    stale の DomainCheckError)。import できない module も DomainCheckError(元の例外は __cause__)。
    """

    packages: tuple[str, ...]
    known_orphans: tuple[type, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.packages, list | tuple):
            raise TypeError(
                "AssertNoOrphanEffects.packages takes a list of import names, "
                f"got {type(self.packages).__name__}"
            )
        object.__setattr__(self, "packages", tuple(self.packages))
        object.__setattr__(
            self, "known_orphans", _types(self.known_orphans, "AssertNoOrphanEffects.known_orphans")
        )


#: handler が答える effect の全部(_answer の match が網羅する)。
DomainEffect = (
    RegisterDomain
    | GetDomain
    | HandledEffects
    | AssertDomainCovered
    | AssertRegisteredDomainsCovered
    | AssertNoOrphanEffects
)


def _table(domains: list[Domain] | tuple[Domain, ...]) -> DomainTable:
    """渡した domain を載せた別の表を作る(名の重なり・2 度目の導入・Domain でない値は作る時に断る)。"""
    if not isinstance(domains, list | tuple):
        raise TypeError(
            f"domain_registry(domains=…) takes a list of Domain, got {type(domains).__name__}"
        )
    table = DomainTable()
    for domain in domains:
        table.register(domain)
    return table


def _answer(table: DomainTable, effect: DomainEffect) -> object:
    """1 つの effect に表から答える(check の外れは例外のまま上げ、handler が出した所へ投げ返す)。"""
    match effect:
        case RegisterDomain(domain=domain):
            return table.register(domain)
        case GetDomain(name=name):
            return table.get(name)
        case HandledEffects(handler=entry_handler, vocabulary=vocabulary):
            return handled_effects(entry_handler, vocabulary=vocabulary)
        case AssertDomainCovered(domain=domain, known_uncovered=known):
            return assert_domain_covered(table, domain, known_uncovered=known)
        case AssertRegisteredDomainsCovered(known_uncovered=known):
            return assert_registered_domains_covered(table, known_uncovered=known)
        case AssertNoOrphanEffects(packages=packages, known_orphans=known):
            return assert_no_orphan_effects(table, packages, known_orphans=known)
        case _:
            # handler の注記が上の 6 つの外を飛ばすので来ない — 来たら黙って None を返さず断る。
            raise TypeError(f"domain_registry does not answer {type(effect).__name__}")


def domain_registry(*, domains: list[Domain] | tuple[Domain, ...] | None = None) -> ProgramHandler:
    """domain の表の effect(RegisterDomain・GetDomain・HandledEffects・check 3 つ)に答える handler。

    domains が無ければ process の表(defdomain が載せる所)を答える時に読み、domains を渡せばその domain だけを載せた別の表から
    答える(domains=[] は空の表で、process の表ではない — 前の isolated_registry の代わり)。

    別の表は工場を呼んだ時に 1 つ作る: 同じ handler の値を使い回す間は RegisterDomain で載せた domain が残るので、test ごとに
    新しい表が要るなら工場を呼び直す(pytest の fixture は function の scope)。domains の名の重なり・2 度目の導入・Domain で
    ない値は、program の中でなく工場を呼んだ時に例外になる(DomainRegistrationError の子か TypeError)。

    使い方: domain_registry()(program)・domain_registry(domains=[domain])(program)。
    """
    table = PROCESS_TABLE if domains is None else _table(domains)

    @handler
    def answer_domain_registry(
        effect: RegisterDomain
        | GetDomain
        | HandledEffects
        | AssertDomainCovered
        | AssertRegisteredDomainsCovered
        | AssertNoOrphanEffects,
        k: K,
    ):
        try:
            value = _answer(table, effect)
        except (DomainError, KeyError) as failure:
            return (yield TransferThrow(k, failure))
        return (yield Transfer(k, value))

    return answer_domain_registry
