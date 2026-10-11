"""defrecord と defeffect の実行の展開が読み込みの時に読む部品(card acp:kanban-issue:ki-a0cca3bdb9a0・defeffect は ki-e77d44aa61e5)。

defrecord は関数(__init__・__repr__・__eq__・__hash__・凍結の __setattr__ / __delattr__)を展開の時に書き、dataclass には欄の表だけを
作らせる(doeff_hy/record.hy の頭注)。展開が書いた class の本体と、class を作った直後の文がここを読む。

この module は標準の library だけを import する葉の module。doeff_hy の中の module(outcome_forms ほか)も defrecord を使い、
doeff_hy.record を読み込む途中で読み込まれるので、展開が class を作る時に読む物を doeff_hy.record に置くと循環の import になる。
"""

import dataclasses
import reprlib
import types

#: 展開が書く凍結の __setattr__ / __delattr__ が出す error(dataclass と同じ型 — 呼び手の except FrozenInstanceError が今までどおり効く)。
FrozenInstanceError = dataclasses.FrozenInstanceError


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

@dataclasses.dataclass(frozen=True)
class _ReferenceEffect:
    """defeffect の今までの展開(dataclass frozen・位置の引数)と同じ __dataclass_params__ を持つ見本(card ki-e77d44aa61e5)。"""


#: 展開が関数を書いた effect 型の __dataclass_params__ — 今までの展開(frozen・eq・init・repr が真・kw_only が偽)と同じ値に戻す。
EFFECT_PARAMS: object = getattr(_ReferenceEffect, "__dataclass_params__")

#: 展開が書く __repr__ の再帰の守り(dataclass の __repr__ と同じ — 自分を含む値は ``...``)。
recursive_repr = reprlib.recursive_repr


def seal_record(cls: type, plain: tuple[str, ...], calls_post_init: bool) -> None:
    """展開が関数を書いた record を仕上げるため: __dataclass_params__ を今までの展開と同じ値に戻し、展開が字面の値と読んだ欄 plain の
    既定値が field の印(工場・init・repr・compare・hash を持つ Field)でない事を確かめる(名を介した field は展開が読めず、関数が
    違う形になる — 欄に ``(field …)`` を直に書く)。型が __post_init__ を持つのに展開の __init__ がそれを呼ばない(calls_post_init が
    偽)時は断る — 展開が本体の自前の __post_init__ を見落とすと、入力の検査が黙って効かなくなる(doeff 424a87f58 の後退)。"""
    if "__post_init__" in vars(cls) and not calls_post_init:
        raise TypeError(f"{cls.__name__} の __post_init__ を展開の __init__ が呼ばない — defrecord の展開が本体の自前の関数を見落とした")
    fields: dict[str, dataclasses.Field[object]] = getattr(cls, "__dataclass_fields__")
    for name in plain:
        f = fields[name]
        if f.default_factory is not dataclasses.MISSING or not f.init or not f.repr or not f.compare or f.hash is not None:
            raise TypeError(f"{cls.__name__} の欄 {name} の既定値は field の印 — defrecord の欄には (field …) を直に書く")
    setattr(cls, "__dataclass_params__", RECORD_PARAMS)


def seal_effect(cls: type, names: tuple[str, ...], calls_post_init: bool) -> None:
    """展開が関数を書いた defeffect の effect 型を仕上げるため: __dataclass_params__ を今までの展開(frozen の dataclass・位置の
    引数)と同じ値に戻す(card ki-e77d44aa61e5)。展開は欄の型と既定値を字面で読むので、dataclass の欄の表が展開の読んだ形と
    違う時は読み込みで断る(424a87f58 の後退と同じ穴 — 展開の関数が黙って違う値を置かない・card ki-c22d8fd6237d):
    型が __post_init__ を持つのに展開の __init__ が呼ばない・欄の名の並び names が違う(ClassVar・InitVar・KW_ONLY の型)・
    既定値が名を介した field の印(工場・init・repr・compare・hash を持つか、展開の __init__ の既定値が欄の既定値と違う)。"""
    if "__post_init__" in vars(cls) and not calls_post_init:
        raise TypeError(f"{cls.__name__} の __post_init__ を展開の __init__ が呼ばない — defeffect の展開が :pre を見落とした")
    fields = dataclasses.fields(cls)
    if tuple(f.name for f in fields) != names:
        raise TypeError(
            f"{cls.__name__} の欄 {tuple(f.name for f in fields)} が展開の読んだ欄 {names} と違う — "
            ":fields の型に ClassVar・InitVar・KW_ONLY は書かない"
        )
    init = vars(cls)["__init__"]
    if not isinstance(init, types.FunctionType):
        raise TypeError(f"{cls.__name__} の __init__ が展開の書いた関数ではない: {init!r}")
    for f in fields:
        if f.default_factory is not dataclasses.MISSING or not f.init or not f.repr or not f.compare or f.hash is not None or f.kw_only:
            raise TypeError(f"{cls.__name__} の欄 {f.name} の既定値は field の印 — defeffect の欄には (field …) を直に書く")
    declared = tuple(f.default for f in fields if f.default is not dataclasses.MISSING)
    written = init.__defaults__ or ()
    if len(declared) != len(written) or any(d is not w for d, w in zip(declared, written)):
        raise TypeError(f"{cls.__name__} の欄の既定値に名を介した field の印がある — defeffect の欄には (field …) を直に書く")
    setattr(cls, "__dataclass_params__", EFFECT_PARAMS)
