"""設計の記録(ADR)を読む effect GetAdr・AdrIds と、ADR の形を確かめる effect AssertAdrContract・AssertAllAdrContracts と、
deftest の検を表に載せる effect RegisterDeftest と、登録簿を読み書きする effect RegisterAdr・ClearRegistry・EnforcementIds・
GetEnforcement と、登録簿から答える handler を返す工場 adr_registry。

決め: GetAdr は effect にする(ADR-DOE-API-001 R20)。AdrIds も effect にする
(ADR-DOE-API-001 R21)。前の関数 get_adr は外の repo の呼び手を書き換えた後に
消した(本体は内側の registry._get_adr)。前の関数 adr_ids は外の repo の呼び手を書き換えた後に消した(本体は内側の registry._adr_ids)。
AssertAdrContract・AssertAllAdrContracts・RegisterDeftest も、doeff-adr の残りの名を同じ向きで直して
effect にした(ADR-DOE-API-001 R30)。前の関数
assert_adr_contract・assert_all_adr_contracts・register_deftest_enforcement は外の repo に呼び手が無いので、移し替えの層を残さずに
消した(本体は内側の registry._assert_adr_contract・registry._register_deftest_enforcement)。
RegisterAdr・ClearRegistry・EnforcementIds・GetEnforcement も同じ答え(ADR-DOE-API-001 R32)。前の関数
register_adr・clear_registry・enforcement_ids・get_enforcement も外の repo に呼び手が無いので、移し替えの層を残さずに消した(本体は
内側の registry._register_adr・_clear_registry・_enforcement_ids・_get_enforcement)。macro defadr の展開は ADR の載せも
RegisterAdr として import の時にこの handler の下で走らせる。

確かめと載せは GetAdr と同じ ADR の表と、検(enforcement)の表を読むので、同じ handler が答える(外の process を起こさない —
semgrep を起こす Semgrep の検は別の工場 semgrep_enforcements)。macro defadr の展開は、deftest の載せを import の時に、ADR の形の
確かめを生まれる test の中で、この handler の下で走らせる(project の doeff_interpreter fixture に頼らない)。

deftest で使うには、project の doeff_interpreter fixture が adr_registry() を据える(doeff-adr は handler の組を選ばない —
README の「doeff_interpreter」の節)。test の中で別の表を使う時は、program の一部に adr_registry(specs=[spec, …], enforcements={…})
を据える: 内側の handler が先に答え、GetAdr は渡した列に無い id を外の登録簿へ落とさずに KeyError にし、AdrIds は渡した列の id だけを返す。
"""

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import Any

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr._registry import (
    AdrSpec,
    EnforcementMode,
    EnforcementRef,
    SemgrepSpec,
    _adr_ids,
    _assert_adr_contract,
    _clear_registry,
    _enforcement_ids,
    _get_adr,
    _get_enforcement,
    _register_adr,
    _register_deftest_enforcement,
)


@dataclass(frozen=True)
class GetAdr(EffectBase[AdrSpec]):
    """id で ADR を引く: yield GetAdr("ADR-…") → AdrSpec。載っていない id は KeyError(id)。"""

    adr_id: str


@dataclass(frozen=True)
class AdrIds(EffectBase[list[str]]):
    """載っている ADR の id の一覧: yield AdrIds() → 並べた list[str](載っていなければ [])。"""


@dataclass(frozen=True)
class AssertAdrContract(EffectBase[None]):
    """ADR 1 つの形を確かめる: yield AssertAdrContract("ADR-…") → None。採用済みなのに検が無い・挙げた検が表に無い・
    expected-red の検が計画に結ばれていない時は AssertionError、載っていない id は KeyError(id)。"""

    adr_id: str


@dataclass(frozen=True)
class AssertAllAdrContracts(EffectBase[None]):
    """載っている ADR の全部の形を確かめる: yield AssertAllAdrContracts() → None。id の順に確かめ、最初の外れを
    AssertAdrContract と同じ AssertionError にする(載っていなければ何も確かめずに答える)。"""


@dataclass(frozen=True)
class RegisterDeftest(EffectBase[EnforcementRef]):
    """deftest の検を表に載せる: yield RegisterDeftest("test_…") → EnforcementRef(kind="deftest")。同じ id が表に在る時と、
    mode が green・expected-red の外の時は ValueError。"""

    enforcement_id: str
    _: KW_ONLY
    mode: EnforcementMode = "green"


@dataclass(frozen=True)
class RegisterAdr(EffectBase[AdrSpec]):
    """ADR を登録簿に載せる: yield RegisterAdr("ADR-…", title=…, status=…, …) → 載せた AdrSpec。

    欄は macro defadr の展開と同じ(status = proposed・accepted・superseded・rejected の語、enforcement = EnforcementRef か
    SemgrepSpec か検の id の列 — id は同じ登録簿の検から種類を読む)。同じ id が載っている時と、status が決まった語でない時は
    ValueError、enforcement の項が読めない型の時は TypeError。
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


def adr_registry(
    *,
    specs: list[AdrSpec] | tuple[AdrSpec, ...] | None = None,
    enforcements: dict[str, EnforcementRef | SemgrepSpec] | None = None,
) -> ProgramHandler:
    """GetAdr・AdrIds・AssertAdrContract・AssertAllAdrContracts・RegisterDeftest・RegisterAdr・ClearRegistry・EnforcementIds・
    GetEnforcement に答える handler。

    specs も enforcements も無ければ process の登録簿(defadr・defsemgrep が載せる所)を答える時に読み書きする。どちらかを渡せば
    別の登録簿で、ADR は specs の列だけ・検は enforcements の辞書だけから読む(渡さない方は空 — specs=[] も空の表で、登録簿では
    ない)。ADR の表は specs の写しで工場を呼んだ時に 1 つ作り、RegisterAdr はそこへ載せる。RegisterDeftest は渡した辞書に載せ、
    enforcements を渡さない時は工場を呼んだ時に作る空の辞書に載せる(同じ handler の値を使い回す間は載せが残る)。
    semgrep_enforcements(table=…) に同じ辞書を渡せば、RegisterSemgrep の載せを GetEnforcement と RegisterAdr の検の id が読む。
    別の登録簿に無い id は外の登録簿へ落ちずに KeyError、ClearRegistry は別の登録簿の 2 つの表だけを空にする。

    使い方: adr_registry()(program)・adr_registry(specs=[spec], enforcements={})(program)。
    """
    if enforcements is not None and not isinstance(enforcements, dict):
        raise TypeError(
            "adr_registry(enforcements=…) takes a dict from id to enforcement, "
            f"got {type(enforcements).__name__}"
        )
    lookup: Callable[[str], AdrSpec]
    ids: Callable[[], list[str]]
    adrs: dict[str, AdrSpec] | None
    table: dict[str, EnforcementRef | SemgrepSpec] | None
    if specs is None and enforcements is None:
        lookup, ids, adrs, table = _get_adr, _adr_ids, None, None
    else:
        by_id = _table(() if specs is None else specs)
        lookup, ids, adrs = by_id.__getitem__, lambda: sorted(by_id), by_id
        table = {} if enforcements is None else enforcements

    def write_or_read_entries(
        effect: RegisterAdr | ClearRegistry | EnforcementIds | GetEnforcement,
    ) -> AdrSpec | list[str] | EnforcementRef | SemgrepSpec | None:
        """登録簿の載せ・空け・検の引きの答えを、この handler の 2 つの表から作る(RegisterAdr の検の id はこの handler の検の表から
        種類を読む)。"""
        match effect:
            case RegisterAdr():
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
                    table=table,
                )
            case ClearRegistry():
                return _clear_registry(adrs=adrs, table=table)
            case EnforcementIds():
                return _enforcement_ids(table)
            case GetEnforcement(enforcement_id=enforcement_id):
                return _get_enforcement(enforcement_id, table=table)

    def answer(
        effect: GetAdr
        | AdrIds
        | AssertAdrContract
        | AssertAllAdrContracts
        | RegisterDeftest
        | RegisterAdr
        | ClearRegistry
        | EnforcementIds
        | GetEnforcement,
    ) -> AdrSpec | list[str] | EnforcementRef | SemgrepSpec | None:
        """effect 1 つの答えを、この handler の表(ADR の列と検の辞書)から作る — 失敗は例外のまま handler へ返す。"""
        match effect:
            case GetAdr(adr_id=adr_id):
                return lookup(adr_id)
            case AdrIds():
                return ids()
            case AssertAdrContract(adr_id=adr_id):
                return _assert_adr_contract(lookup(adr_id), table=table)
            case AssertAllAdrContracts():
                for adr_id in ids():
                    _assert_adr_contract(lookup(adr_id), table=table)
                return None
            case RegisterDeftest(enforcement_id=enforcement_id, mode=mode):
                return _register_deftest_enforcement(enforcement_id, table=table, mode=mode)
            case RegisterAdr() | ClearRegistry() | EnforcementIds() | GetEnforcement():
                return write_or_read_entries(effect)

    @handler
    def answer_adr_registry(
        effect: GetAdr
        | AdrIds
        | AssertAdrContract
        | AssertAllAdrContracts
        | RegisterDeftest
        | RegisterAdr
        | ClearRegistry
        | EnforcementIds
        | GetEnforcement,
        k: K,
    ):
        # 引きと確かめと載せの決まった失敗は、effect を出した所へ投げ返す(先例 _semgrep_effects と同じ形)。
        try:
            value = answer(effect)
        except (AssertionError, KeyError, TypeError, ValueError) as failed:
            return (yield TransferThrow(k, failed))
        return (yield Transfer(k, value))

    return answer_adr_registry
