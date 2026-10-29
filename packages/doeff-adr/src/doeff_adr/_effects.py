"""設計の記録(ADR)の登録簿を読み書きする effect GetAdr・AdrIds・RegisterAdr・ClearRegistry・EnforcementIds・GetEnforcement と、
登録簿から答える handler を返す工場 adr_registry。

決め: GetAdr は依頼書 lt-TNNNQAXTY6XY4RAKWDDFX62NSM の問い get-adr の答え lt-82V6XBGCCR214RCF3SRKSZGTP5(effect にする・card
ki-24a2bca4339f・ADR-DOE-API-001 R20)。AdrIds は依頼書 lt-SNXDP773DP61WQ8S3D5HSHW2B1 の答え lt-NJMPGF51C0FBF49SC430JWEQ82
(effect にする・card ki-2d28425e0d40・ADR-DOE-API-001 R21)。前の関数 get_adr は外の repo の呼び手を書き換えた後に
消した(本体は内側の registry._get_adr・card ki-c46e83ebd6ac)。前の関数 adr_ids は外の repo の呼び手を書き換えた後に消した(本体は内側の registry._adr_ids)。
RegisterAdr・ClearRegistry・EnforcementIds・GetEnforcement は依頼書 lt-6SE07X4256P323ZGZVNSEF9M95 の問い adr-scope の答え
lt-RA4FZ731JNZQ6JEGVAYD35XCAZ(doeff-adr を範囲に入れ、関数を GetAdr と同じ向きで直す・card ki-dab8943cf664・ADR-DOE-API-001 R30)。
前の関数 register_adr・clear_registry・enforcement_ids・get_enforcement は外の repo に呼び手が無かったので、移し替えの層を残さずに
消した(本体は内側の registry._register_adr・_clear_registry・_enforcement_ids・_get_enforcement)。

deftest で使うには、project の doeff_interpreter fixture が adr_registry() を据える(doeff-adr は handler の組を選ばない —
README の「doeff_interpreter」の節)。test の中で別の登録簿を使う時は、program の一部に adr_registry(specs=[spec, …],
enforcements={…}) を据える: 内側の handler が先に答え、6 つの effect はどれも外の登録簿へ落ちない。
"""

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import Any

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr.registry import (
    AdrSpec,
    EnforcementRef,
    SemgrepSpec,
    _adr_ids,
    _clear_registry,
    _enforcement_ids,
    _get_adr,
    _get_enforcement,
    _register_adr,
)


@dataclass(frozen=True)
class GetAdr(EffectBase[AdrSpec]):
    """id で ADR を引く: yield GetAdr("ADR-…") → AdrSpec。載っていない id は KeyError(id)。"""

    adr_id: str


@dataclass(frozen=True)
class AdrIds(EffectBase[list[str]]):
    """載っている ADR の id の一覧: yield AdrIds() → 並べた list[str](載っていなければ [])。"""


@dataclass(frozen=True)
class RegisterAdr(EffectBase[AdrSpec]):
    """ADR を登録簿に載せる: yield RegisterAdr("ADR-…", title=…, status=…, …) → 載せた AdrSpec。

    欄は macro defadr の展開と同じ(status = proposed・accepted・superseded・rejected の語、enforcement = EnforcementRef か
    SemgrepSpec か検の id の列 — id は同じ登録簿の検から種類を読む)。同じ id が載っている時と、status・検の mode が
    決まった語でない時は ValueError、enforcement の項が読めない型の時は TypeError。
    """

    adr_id: str
    _: KW_ONLY
    title: str
    status: str
    scope: list[Any] | tuple[Any, ...] | None = None
    problem: list[Any] | tuple[Any, ...] | None = None
    context: list[Any] | tuple[Any, ...] | None = None
    decision: list[Any] | tuple[Any, ...] | None = None
    laws: list[Any] | tuple[Any, ...] | None = None
    enforcement: list[Any] | tuple[Any, ...] | None = None
    plans: list[Any] | tuple[Any, ...] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ClearRegistry(EffectBase[None]):
    """登録簿を空にする: yield ClearRegistry() → None。載っている ADR と検の両方を消す。"""


@dataclass(frozen=True)
class EnforcementIds(EffectBase[list[str]]):
    """載っている検(defadr の deftest と defsemgrep)の id の一覧: yield EnforcementIds() → 並べた list[str]。"""


@dataclass(frozen=True)
class GetEnforcement(EffectBase[EnforcementRef | SemgrepSpec]):
    """id で検を引く: yield GetEnforcement("id") → EnforcementRef か SemgrepSpec。載っていない id は KeyError(id)。"""

    enforcement_id: str


def _table(specs: list[AdrSpec] | tuple[AdrSpec, ...]) -> dict[str, AdrSpec]:
    """渡した ADR の列を id で引く表にする(id の重なりと ADR でない値は作る時に断る)。"""
    if not isinstance(specs, list | tuple):
        raise TypeError(
            f"adr_registry(specs=…) takes a list of AdrSpec, got {type(specs).__name__}"
        )
    by_id: dict[str, AdrSpec] = {}
    for spec in specs:
        if not isinstance(spec, AdrSpec):
            raise TypeError(
                f"adr_registry(specs=…) takes AdrSpec values, got {type(spec).__name__}"
            )
        if spec.id in by_id:
            raise ValueError(f"duplicate ADR id in the table: {spec.id}")
        by_id[spec.id] = spec
    return by_id


def _register(
    effect: RegisterAdr,
    adrs: dict[str, AdrSpec] | None,
    enforcements: dict[str, EnforcementRef | SemgrepSpec] | None,
) -> AdrSpec:
    """RegisterAdr の欄で表(None = process の登録簿)に ADR を載せる。"""
    return _register_adr(
        effect.adr_id,
        title=effect.title,
        status=effect.status,
        scope=effect.scope,
        problem=effect.problem,
        context=effect.context,
        decision=effect.decision,
        laws=effect.laws,
        enforcement=effect.enforcement,
        plans=effect.plans,
        metadata=effect.metadata,
        adrs=adrs,
        enforcements=enforcements,
    )


def adr_registry(
    *,
    specs: list[AdrSpec] | tuple[AdrSpec, ...] | None = None,
    enforcements: dict[str, EnforcementRef | SemgrepSpec] | None = None,
) -> ProgramHandler:
    """GetAdr・AdrIds・RegisterAdr・ClearRegistry・EnforcementIds・GetEnforcement に答える handler。

    specs も enforcements も無ければ process の登録簿(defadr・defsemgrep が載せる所)を答える時に読み書きする。どちらかを
    渡せば別の登録簿から答える: ADR の表は specs の写し(無ければ空)で、工場を呼んだ時に 1 つ作り、同じ handler の値を
    使い回す間は RegisterAdr の載せが残る。検の表は渡した辞書をそのまま読み書きし(無ければ空の辞書)、
    semgrep_enforcements(table=…) に同じ辞書を渡せば RegisterSemgrep の載せを GetEnforcement と RegisterAdr が読む。
    別の登録簿に無い id は外の登録簿へ落ちずに KeyError、ClearRegistry は別の登録簿の 2 つの表だけを空にする。

    使い方: adr_registry()(program)・adr_registry(specs=[spec])(program)・adr_registry(enforcements={})(program)。
    """
    lookup: Callable[[str], AdrSpec]
    ids: Callable[[], list[str]]
    register: Callable[[RegisterAdr], AdrSpec]
    clear: Callable[[], None]
    lookup_enforcement: Callable[[str], EnforcementRef | SemgrepSpec]
    enforcement_ids: Callable[[], list[str]]
    if specs is None and enforcements is None:
        lookup, ids, clear = _get_adr, _adr_ids, _clear_registry
        lookup_enforcement, enforcement_ids = _get_enforcement, _enforcement_ids

        def register(effect: RegisterAdr) -> AdrSpec:
            return _register(effect, None, None)

    else:
        if enforcements is not None and not isinstance(enforcements, dict):
            raise TypeError(
                "adr_registry(enforcements=…) takes a dict from id to enforcement, "
                f"got {type(enforcements).__name__}"
            )
        by_id = _table([] if specs is None else specs)
        checks: dict[str, EnforcementRef | SemgrepSpec] = (
            {} if enforcements is None else enforcements
        )
        lookup, ids = by_id.__getitem__, lambda: sorted(by_id)
        lookup_enforcement, enforcement_ids = checks.__getitem__, lambda: sorted(checks)

        def register(effect: RegisterAdr) -> AdrSpec:
            return _register(effect, by_id, checks)

        def clear() -> None:
            by_id.clear()
            checks.clear()

    @handler
    def answer_adr_registry(
        effect: GetAdr | AdrIds | RegisterAdr | ClearRegistry | EnforcementIds | GetEnforcement,
        k: K,
    ):
        # 載せと引きの決まった失敗は、effect を出した所へ投げ返す(先例 _semgrep_effects と同じ形)。
        try:
            match effect:
                case GetAdr(adr_id=adr_id):
                    answer: object = lookup(adr_id)
                case AdrIds():
                    answer = ids()
                case RegisterAdr():
                    answer = register(effect)
                case ClearRegistry():
                    answer = clear()
                case EnforcementIds():
                    answer = enforcement_ids()
                case GetEnforcement(enforcement_id=enforcement_id):
                    answer = lookup_enforcement(enforcement_id)
        except (KeyError, TypeError, ValueError) as failed:
            return (yield TransferThrow(k, failed))
        return (yield Transfer(k, answer))

    return answer_adr_registry
