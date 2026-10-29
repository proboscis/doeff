"""設計の記録(ADR)を読む effect GetAdr・AdrIds と、ADR の形を確かめる effect AssertAdrContract・AssertAllAdrContracts と、
deftest の検を表に載せる effect RegisterDeftest と、登録簿から答える handler を返す工場 adr_registry。

決め: GetAdr は依頼書 lt-TNNNQAXTY6XY4RAKWDDFX62NSM の問い get-adr の答え lt-82V6XBGCCR214RCF3SRKSZGTP5(effect にする・card
ki-24a2bca4339f・ADR-DOE-API-001 R20)。AdrIds は依頼書 lt-SNXDP773DP61WQ8S3D5HSHW2B1 の答え lt-NJMPGF51C0FBF49SC430JWEQ82
(effect にする・card ki-2d28425e0d40・ADR-DOE-API-001 R21)。前の関数 get_adr は外の repo の呼び手を書き換えた後に
消した(本体は内側の registry._get_adr・card ki-c46e83ebd6ac)。前の関数 adr_ids は外の repo の呼び手を書き換えた後に消した(本体は内側の registry._adr_ids)。
AssertAdrContract・AssertAllAdrContracts・RegisterDeftest は依頼書 lt-6SE07X4256P323ZGZVNSEF9M95 の問い adr-scope の答え
lt-RA4FZ731JNZQ6JEGVAYD35XCAZ(doeff-adr の残りの名を同じ向きで直す・card ki-54e559d3c0e7・ADR-DOE-API-001 R30)。前の関数
assert_adr_contract・assert_all_adr_contracts・register_deftest_enforcement は外の repo に呼び手が無いので、移し替えの層を残さずに
消した(本体は内側の registry._assert_adr_contract・registry._register_deftest_enforcement)。

確かめと載せは GetAdr と同じ ADR の表と、検(enforcement)の表を読むので、同じ handler が答える(外の process を起こさない —
semgrep を起こす Semgrep の検は別の工場 semgrep_enforcements)。macro defadr の展開は、deftest の載せを import の時に、ADR の形の
確かめを生まれる test の中で、この handler の下で走らせる(project の doeff_interpreter fixture に頼らない)。

deftest で使うには、project の doeff_interpreter fixture が adr_registry() を据える(doeff-adr は handler の組を選ばない —
README の「doeff_interpreter」の節)。test の中で別の表を使う時は、program の一部に adr_registry(specs=[spec, …], enforcements={…})
を据える: 内側の handler が先に答え、GetAdr は渡した列に無い id を外の登録簿へ落とさずに KeyError にし、AdrIds は渡した列の id だけを返す。
"""

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr._registry import (
    AdrSpec,
    EnforcementMode,
    EnforcementRef,
    SemgrepSpec,
    _adr_ids,
    _assert_adr_contract,
    _get_adr,
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


def _table(specs: list[AdrSpec] | tuple[AdrSpec, ...]) -> dict[str, AdrSpec]:
    """渡した ADR の列を id で引く表にする(id の重なりと ADR でない値は作る時に断る)。"""
    if not isinstance(specs, list | tuple):
        raise TypeError(f"adr_registry(specs=…) takes a list of AdrSpec, got {type(specs).__name__}")
    by_id: dict[str, AdrSpec] = {}
    for spec in specs:
        if not isinstance(spec, AdrSpec):
            raise TypeError(f"adr_registry(specs=…) takes AdrSpec values, got {type(spec).__name__}")
        if spec.id in by_id:
            raise ValueError(f"duplicate ADR id in the table: {spec.id}")
        by_id[spec.id] = spec
    return by_id


def adr_registry(
    *,
    specs: list[AdrSpec] | tuple[AdrSpec, ...] | None = None,
    enforcements: dict[str, EnforcementRef | SemgrepSpec] | None = None,
) -> ProgramHandler:
    """GetAdr・AdrIds・AssertAdrContract・AssertAllAdrContracts・RegisterDeftest に答える handler。

    specs も enforcements も無ければ process の登録簿(defadr・defsemgrep が載せる所)を答える時に読む。どちらかを渡せば別の
    登録簿で、ADR は specs の列だけ・検は enforcements の辞書だけから読む(渡さない方は空 — specs=[] も空の表で、登録簿では
    ない)。RegisterDeftest は渡した辞書に載せ、enforcements を渡さない時は工場を呼んだ時に作る空の辞書に載せる(同じ handler の
    値を使い回す間は載せが残る)。

    使い方: adr_registry()(program)・adr_registry(specs=[spec], enforcements={})(program)。
    """
    if enforcements is not None and not isinstance(enforcements, dict):
        raise TypeError(
            "adr_registry(enforcements=…) takes a dict from id to enforcement, "
            f"got {type(enforcements).__name__}"
        )
    lookup: Callable[[str], AdrSpec]
    ids: Callable[[], list[str]]
    table: dict[str, EnforcementRef | SemgrepSpec] | None
    if specs is None and enforcements is None:
        lookup, ids, table = _get_adr, _adr_ids, None
    else:
        by_id = _table(() if specs is None else specs)
        lookup, ids = by_id.__getitem__, lambda: sorted(by_id)
        table = {} if enforcements is None else enforcements

    def answer(
        effect: GetAdr | AdrIds | AssertAdrContract | AssertAllAdrContracts | RegisterDeftest,
    ) -> AdrSpec | list[str] | EnforcementRef | None:
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

    @handler
    def answer_adr_registry(
        effect: GetAdr | AdrIds | AssertAdrContract | AssertAllAdrContracts | RegisterDeftest,
        k: K,
    ):
        # 引きと確かめと載せの決まった失敗は、effect を出した所へ投げ返す(先例 _semgrep_effects と同じ形)。
        try:
            value = answer(effect)
        except (AssertionError, KeyError, ValueError) as failed:
            return (yield TransferThrow(k, failed))
        return (yield Transfer(k, value))

    return answer_adr_registry
