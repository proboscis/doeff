"""契約 `(: x FrozenJson)` と型付き束縛 `(<- x FrozenJson e)` が実行時に通ること(card acp:kanban-issue:ki-e540d658e378)。

契約と束縛は書いた型を `doeff_hy._type_forms.runtime_type_form` で isinstance に渡せる形へ写すが、写すのは字面の総称型
(`(get …)`・`(| …)`)だけで、名(型の別名)はそのまま isinstance に渡る。doeff_hy._frozen の実行時の名 FrozenJson が
`FrozenMap[object] | tuple[object, ...] | …`(総称型を含む和)だと、どの値でも
`TypeError: isinstance() argument 2 cannot be a parameterized generic` になった(2026-10-11 agora の受付が 500 を返した —
使い手は字面に展開して避けていた)。実行時の名は外側の型の和にする(実行時に確かめるのは外側の型だけ — #1790 の決め)。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk <-])
(import doeff_hy._frozen [FrozenJson FrozenMap])

(defk accepted [value]
  {:pre [(: value FrozenJson)] :post [(: % bool)]}
  True)

(defk field-of [event key]
  {:pre [(: event (get FrozenMap FrozenJson)) (: key str)] :post [(: % FrozenJson)]}
  (.get event key))

(defk bound-field [event key]
  {:pre [(: event (get FrozenMap FrozenJson)) (: key str)] :post [(: % FrozenJson)]}
  (<- got FrozenJson (field-of event key))
  got)
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - Hy の import hook を登録する

    root: Path = tmp_path_factory.mktemp("doeff_hy_frozen_json_contract")
    (root / "frozen_json_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("frozen_json_contract_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write_before


def _run(program: object) -> object:
    """読み込んだ Hy の module の Program を走らせる(module の属性は型の上では object なので Program に絞る)。"""
    from doeff import Program, run

    match program:
        case Program():
            return run(program)
        case _:
            raise TypeError(f"Program でない値を走らせようとした: {program!r}")


def test_a_contract_naming_frozen_json_accepts_every_json_value(mod: ModuleType) -> None:
    from doeff_hy._frozen import freeze_json_text

    for value in (freeze_json_text('{"a": [1, "x", null]}'), freeze_json_text("[1, 2]"), "x", 1, 1.5, True, None):
        assert _run(mod.accepted(value)) is True, value


def test_a_typed_bind_and_a_mapping_contract_of_frozen_json_pass(mod: ModuleType) -> None:
    from doeff_hy._frozen import freeze_json_text

    event = freeze_json_text('{"start_ms": 3, "items": [1]}')
    assert _run(mod.bound_field(event, "start_ms")) == 3
    assert _run(mod.bound_field(event, "items")) == (1,)
    assert _run(mod.bound_field(event, "absent")) is None


def test_a_contract_naming_frozen_json_still_refuses_values_outside_frozen_json(mod: ModuleType) -> None:
    # 凍らせていない写像と列は契約の断り(TypeError でなく、名指しの AssertionError)。
    for value in ({"a": 1}, [1], object()):
        with pytest.raises(AssertionError, match="expected FrozenJson"):
            _run(mod.accepted(value))


def test_the_runtime_name_still_parameterizes_a_frozen_map_field() -> None:
    # 実行時の名は欄の注記 `(get FrozenMap FrozenJson)` のための名(#4257)— 外側の型の和でも型引数に置ける。
    from doeff_hy._frozen import FrozenJson, FrozenMap

    assert FrozenMap[FrozenJson].__origin__ is FrozenMap
