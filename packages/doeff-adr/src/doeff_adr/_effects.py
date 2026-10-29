"""設計の記録(ADR)を読む effect GetAdr・AdrIds と、登録簿から答える handler を返す工場 adr_registry。

決め: GetAdr は依頼書 lt-TNNNQAXTY6XY4RAKWDDFX62NSM の問い get-adr の答え lt-82V6XBGCCR214RCF3SRKSZGTP5(effect にする・card
ki-24a2bca4339f・ADR-DOE-API-001 R20)。AdrIds は依頼書 lt-SNXDP773DP61WQ8S3D5HSHW2B1 の答え lt-NJMPGF51C0FBF49SC430JWEQ82
(effect にする・card ki-2d28425e0d40・ADR-DOE-API-001 R21)。前の関数 get_adr は外の repo の呼び手を書き換えた後に
消した(本体は内側の registry._get_adr・card ki-c46e83ebd6ac)。前の関数 adr_ids は外の repo の呼び手を書き換えた後に消した(本体は内側の registry._adr_ids)。

deftest で使うには、project の doeff_interpreter fixture が adr_registry() を据える(doeff-adr は handler の組を選ばない —
README の「doeff_interpreter」の節)。test の中で別の表を使う時は、program の一部に adr_registry(specs=[spec, …]) を据える:
内側の handler が先に答え、GetAdr は渡した列に無い id を外の登録簿へ落とさずに KeyError にし、AdrIds は渡した列の id だけを返す。
"""

from collections.abc import Callable
from dataclasses import dataclass

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr.registry import AdrSpec, _adr_ids, _get_adr


@dataclass(frozen=True)
class GetAdr(EffectBase[AdrSpec]):
    """id で ADR を引く: yield GetAdr("ADR-…") → AdrSpec。載っていない id は KeyError(id)。"""

    adr_id: str


@dataclass(frozen=True)
class AdrIds(EffectBase[list[str]]):
    """載っている ADR の id の一覧: yield AdrIds() → 並べた list[str](載っていなければ [])。"""


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


def adr_registry(*, specs: list[AdrSpec] | tuple[AdrSpec, ...] | None = None) -> ProgramHandler:
    """GetAdr と AdrIds に答える handler。specs が無ければ process の登録簿(defadr が載せる所)を答える時に読み、specs を
    渡せばその ADR の列だけから答える(specs=[] は空の表で、登録簿ではない)。

    使い方: adr_registry()(program)・adr_registry(specs=[spec])(program)。
    """
    lookup: Callable[[str], AdrSpec]
    ids: Callable[[], list[str]]
    if specs is None:
        lookup, ids = _get_adr, _adr_ids
    else:
        by_id = _table(specs)
        lookup, ids = by_id.__getitem__, lambda: sorted(by_id)

    @handler
    def answer_adr_registry(effect: GetAdr | AdrIds, k: K):
        if isinstance(effect, AdrIds):
            return (yield Transfer(k, ids()))
        try:
            spec = lookup(effect.adr_id)
        except KeyError as missing:
            return (yield TransferThrow(k, missing))
        return (yield Transfer(k, spec))

    return answer_adr_registry
