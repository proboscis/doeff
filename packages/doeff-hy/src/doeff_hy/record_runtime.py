"""defrecord の実行の展開が読み込みの時に読む部品(card acp:kanban-issue:ki-a0cca3bdb9a0)。

defrecord は関数(__init__・__repr__・__eq__・__hash__・凍結の __setattr__ / __delattr__)を展開の時に書き、dataclass には欄の表だけを
作らせる(doeff_hy/record.hy の頭注)。展開が書いた class の本体と、class を作った直後の文がここを読む。

この module は標準の library だけを import する葉の module。doeff_hy の中の module(outcome_forms ほか)も defrecord を使い、
doeff_hy.record を読み込む途中で読み込まれるので、展開が class を作る時に読む物を doeff_hy.record に置くと循環の import になる。
"""

import dataclasses
import reprlib
from dataclasses import FrozenInstanceError

__all__ = ["FACTORY_DEFAULT", "RECORD_PARAMS", "FrozenInstanceError", "recursive_repr", "seal_record"]


class _FactoryDefault:
    """既定の工場を持つ欄の引数を省いた印(展開が書く __init__ の既定値 — 署名の文は dataclass と同じ ``<factory>``)。"""

    def __repr__(self) -> str:
        return "<factory>"


#: 展開が書く __init__ の、default-factory の欄の引数の既定値(省いた時だけ工場を呼ぶ)。
FACTORY_DEFAULT = _FactoryDefault()


@dataclasses.dataclass(frozen=True, kw_only=True)
class _ReferenceRecord:
    """今までの展開(dataclass frozen・kw_only)と同じ __dataclass_params__ を持つ見本(値を写すためだけに作る)。"""


#: 展開が関数を書いた record の __dataclass_params__ — dataclass に関数を作らせない呼び方の値を、今までの展開
#: (frozen・eq・init・repr が真・kw_only)と同じ値に戻す。
RECORD_PARAMS: object = getattr(_ReferenceRecord, "__dataclass_params__")

#: 展開が書く __repr__ の再帰の守り(dataclass の __repr__ と同じ — 自分を含む値は ``...``)。
recursive_repr = reprlib.recursive_repr


def seal_record(cls: type, plain: tuple[str, ...]) -> None:
    """展開が関数を書いた record を仕上げるため: __dataclass_params__ を今までの展開と同じ値に戻し、展開が字面の値と読んだ欄 plain の
    既定値が field の印(工場・init・repr・compare・hash を持つ Field)でない事を確かめる(名を介した field は展開が読めず、関数が
    違う形になる — 欄に ``(field …)`` を直に書く)。"""
    fields: dict[str, dataclasses.Field[object]] = getattr(cls, "__dataclass_fields__")
    for name in plain:
        f = fields[name]
        if f.default_factory is not dataclasses.MISSING or not f.init or not f.repr or not f.compare or f.hash is not None:
            raise TypeError(f"{cls.__name__} の欄 {name} の既定値は field の印 — defrecord の欄には (field …) を直に書く")
    setattr(cls, "__dataclass_params__", RECORD_PARAMS)
