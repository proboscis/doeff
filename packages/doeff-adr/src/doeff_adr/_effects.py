"""設計の記録(ADR)を id で引く effect GetAdr と、登録簿から答える handler を返す工場 adr_registry。

決め: 依頼書 lt-TNNNQAXTY6XY4RAKWDDFX62NSM の問い get-adr の答え lt-82V6XBGCCR214RCF3SRKSZGTP5(effect にする・card
ki-24a2bca4339f・ADR-DOE-API-001 R19)。前の関数 get_adr は、外の repo の呼び手を書き換えるまで移し替えの層として残る。

deftest で使うには、project の doeff_interpreter fixture が adr_registry() を据える(doeff-adr は handler の組を選ばない —
README の「doeff_interpreter」の節)。test の中で別の表を使う時は、program の一部に adr_registry(specs=[spec, …]) を据える:
内側の handler が先に答え、渡した列に無い id は外の登録簿へ落ちずに KeyError になる。
"""

from collections.abc import Callable
from dataclasses import dataclass

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr.registry import AdrSpec, _get_adr


@dataclass(frozen=True)
class GetAdr(EffectBase[AdrSpec]):
    """id で ADR を引く: yield GetAdr("ADR-…") → AdrSpec。載っていない id は KeyError(id)。"""

    adr_id: str


def _table_lookup(specs: list[AdrSpec] | tuple[AdrSpec, ...]) -> Callable[[str], AdrSpec]:
    """渡した ADR の列を id で引く関数にする(id の重なりと ADR でない値は作る時に断る)。"""
    if not isinstance(specs, list | tuple):
        raise TypeError(f"adr_registry(specs=…) takes a list of AdrSpec, got {type(specs).__name__}")
    by_id: dict[str, AdrSpec] = {}
    for spec in specs:
        if not isinstance(spec, AdrSpec):
            raise TypeError(f"adr_registry(specs=…) takes AdrSpec values, got {type(spec).__name__}")
        if spec.id in by_id:
            raise ValueError(f"duplicate ADR id in the table: {spec.id}")
        by_id[spec.id] = spec
    return by_id.__getitem__


def adr_registry(*, specs: list[AdrSpec] | tuple[AdrSpec, ...] | None = None) -> ProgramHandler:
    """GetAdr に答える handler。specs が無ければ process の登録簿(defadr が載せる所)を答える時に読み、specs を渡せば
    その ADR の列だけから答える(specs=[] は空の表で、登録簿ではない)。

    使い方: adr_registry()(program)・adr_registry(specs=[spec])(program)。
    """
    lookup = _get_adr if specs is None else _table_lookup(specs)

    @handler
    def answer_get_adr(effect: GetAdr, k: K):
        try:
            spec = lookup(effect.adr_id)
        except KeyError as missing:
            return (yield TransferThrow(k, missing))
        return (yield Transfer(k, spec))

    return answer_get_adr
