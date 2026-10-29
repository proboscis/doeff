"""program に handler を被せていた 8 名が、ほかの handler と同じく h1(h2(p)) で当てる工場か handler の値になった事(agora card ki-c72b3906869b)。

決まり: handler を返す工場は、最小の引数で呼ぶと印 _doeff_is_handler_fn の付いた handler を返す関数(ADR-DOE-API-001 R3)。
program を包む関数は工場の形へ寄せ(R6)、program には h1(h2(p)) で当てる(R16)。設定も呼びごとの状態も無い handler は、工場でなく
印の付いた値(ADR-DOE-API-002 R1)。名ごとの行き先:

- doeff-conductor の make_blocking_scheduled_handler・make_async_scheduled_handler — 名のまま工場(fn を受け、全部の effect に
  fn(effect) で答える handler を返す。async の方は DeprecationWarning を出す)。
- doeff-notify の testing_handler・build_testing_handler(同じ関数の 2 つの名)— 名のまま工場(記録の先 capture を受ける)。
- doeff-core-effects の run_apart — 設定が無いので、名のまま印の付いた handler の値: (run-apart program)。
- doeff-agentic の with_agentic_effectful_handlers(p, …) — 消して、もう在る工場 agentic_effectful_handlers(…)(p) へ寄せた。
- doeff-agentic の with_handlers(p, handlers)(doeff_agentic.runtime)— 消して、doeff の stacked_handlers(*handlers)(p) へ寄せた
  (submodule doeff_agentic.runtime も消した)。
- doeff-records の ready_handlers — handler を被せる関数でなく、検の殻の用意の program(書き手の名 → handler をそのまま返す)
  なので、工場にせず内側の _ready_handlers へ移した。

1. 目録(tools/public_api_inventory.py)が、工場の名を「呼ぶと handler を返す」handler と、run_apart を印を持つ handler と読み、
   消した名と内側へ移した名を公開の名に数えない。
2. 工場を呼ぶと印の付いた handler を返し、その handler(と run_apart)を program に当てると元の関数と同じ答えになる(doeff-notify は root の
   環境に入らないので、packages/doeff-notify/tests/test_handlers.py が同じ事を確かめる)。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from doeff import Tell, do, run, stacked_handlers

ROOT = Path(__file__).resolve().parents[1]
#: 目録の子の process が 1 package を読む秒の上限(tests/test_public_api_known_violations.py と同じ)。
_PROBE_SECONDS = 100


def _load_tool() -> ModuleType:
    """tools/ の目録の道具を読む(既知の表の test と同じ名で 1 つを共にする)。"""
    loaded = sys.modules.get("public_api_inventory")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "public_api_inventory", ROOT / "tools" / "public_api_inventory.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_api_inventory"] = module
    spec.loader.exec_module(module)
    return module


#: 名のまま工場になった名と、もう在る工場へ寄せた行き先: (package の dir, 入口の module, 名)。
FACTORIES = (
    ("doeff-conductor", "doeff_conductor", "make_blocking_scheduled_handler"),
    ("doeff-conductor", "doeff_conductor", "make_async_scheduled_handler"),
    ("doeff-notify", "doeff_notify", "testing_handler"),
    ("doeff-notify", "doeff_notify", "build_testing_handler"),
    ("doeff-agentic", "doeff_agentic", "agentic_effectful_handlers"),
)
#: 公開の名から消えた名: (package の dir, 名)。
GONE = (
    ("doeff-agentic", "with_agentic_effectful_handlers"),
    ("doeff-agentic", "with_handlers"),
    ("doeff-agentic", "doeff_agentic.runtime"),
    ("doeff-records", "ready_handlers"),
)
#: 設定が無いので工場でなく印の付いた handler の値になった名: (package の dir, 入口の module, 名)。
VALUES = (("doeff-core-effects", "doeff_core_effects", "run_apart"),)
PACKAGES = sorted(
    {package for package, _module, _name in (*FACTORIES, *VALUES)} | {p for p, _n in GONE}
)


@pytest.fixture(scope="module")
def inventory_rows() -> dict[str, list[dict[str, str]]]:
    """5 package の目録の行(package の dir ごと)。"""
    tool = _load_tool()
    packages = tool.inventory(ROOT, PACKAGES, jobs=len(PACKAGES), timeout=_PROBE_SECONDS)
    for package in packages:
        assert package["error"] is None, f"{package['dir']} を読めなかった: {package['error']}"
    return {package["dir"]: package["exports"] for package in packages}


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("package", "module", "name"), FACTORIES)
def test_the_inventory_reads_each_factory_as_a_handler_it_called(
    inventory_rows: dict[str, list[dict[str, str]]], package: str, module: str, name: str
) -> None:
    """目録が入口の名を handler と読み、最小の引数で呼んで印の付いた handler が返ったと書く(R3 の確かめ)。"""
    [row] = [
        row
        for row in inventory_rows[package]
        if row["module"] == module and row["name"] == name
    ]
    assert row["kind"] == "handler", row
    assert row["reason"].endswith("呼ぶと handler を返す"), row


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("package", "module", "name"), VALUES)
def test_the_inventory_reads_each_value_as_a_marked_handler(
    inventory_rows: dict[str, list[dict[str, str]]], package: str, module: str, name: str
) -> None:
    """目録が入口の名を、印を持つ handler の値と読む(ADR-DOE-API-002 R1)。"""
    [row] = [
        row
        for row in inventory_rows[package]
        if row["module"] == module and row["name"] == name
    ]
    assert (row["kind"], row["form"]) == ("handler", "handler"), row


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("package", "name"), GONE)
def test_the_inventory_counts_no_removed_name(
    inventory_rows: dict[str, list[dict[str, str]]], package: str, name: str
) -> None:
    """消した名と内側へ移した名は、どの公開の module の行にも無い(submodule の行も)。"""
    shown = [row for row in inventory_rows[package] if row["name"] == name]
    assert not shown, shown


@do
def _tells():
    return (yield Tell("asked"))


def _marked(value: object) -> bool:
    return getattr(value, "_doeff_is_handler_fn", False) is True


def test_make_blocking_scheduled_handler_returns_a_handler_that_answers_with_the_function() -> None:
    from doeff_conductor import make_blocking_scheduled_handler

    answering = make_blocking_scheduled_handler(lambda effect: f"answered {effect.msg}")

    assert _marked(answering)
    assert run(answering(_tells())) == "answered asked"


def test_make_async_scheduled_handler_warns_and_returns_the_same_handler() -> None:
    from doeff_conductor import make_async_scheduled_handler

    with pytest.warns(DeprecationWarning, match="make_async_scheduled_handler is deprecated"):
        answering = make_async_scheduled_handler(lambda effect: "answered")

    assert _marked(answering)
    assert run(answering(_tells())) == "answered"


def test_run_apart_is_a_handler_that_runs_the_program_apart() -> None:
    from doeff_core_effects import run_apart, scheduled

    @do
    def computed():
        return 41 + 1

    assert _marked(run_apart)
    assert run(scheduled()(run_apart(computed()))) == 42


def test_agentic_wraps_through_its_factory_and_doeff_stacked_handlers(tmp_path: Path) -> None:
    import doeff_agentic

    assert not hasattr(doeff_agentic, "with_agentic_effectful_handlers")
    assert not hasattr(doeff_agentic, "with_handlers")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("doeff_agentic.runtime")
    agentic = doeff_agentic.agentic_effectful_handlers(
        workflow_name="factory", state_dir=tmp_path, agent_runtime=stacked_handlers()
    )
    assert _marked(agentic)


def test_ready_handlers_is_inside_records_http_server() -> None:
    http_server = importlib.import_module("doeff_records._http_server")

    assert not hasattr(http_server, "ready_handlers")
    assert hasattr(http_server, "_ready_handlers")
