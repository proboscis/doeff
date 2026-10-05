"""doeff-records の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(#2826)。

main・admission・laws・wire は .pyi が無く、使い手の repo がこれらの名を使う行を足すと、strict の型の門が書き手に直せない
Unknown の赤で止まった(使い手が import する doeff の module のうち .pyi の無い 63 個の 4 個)。.pyi は道具が .hy から作る
(http_client は手で書いた .pyi — #2810 — のまま。probe は同じ使い手の名として一緒に束ねる)。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物(.hy を変えたら `python -m doeff_hy.static_stub --write <.hy>` で作り直す)。
"""

import shutil
from pathlib import Path

import pytest

from doeff_hy.static_stub import UsedModule, stale_in, strict_errors, unknown_in_users

SOURCE = Path(__file__).resolve().parents[1] / "src"

# 使い手の repo の main が import する名(module ごと・2026-10-02 の数え)。
USED = (
    UsedModule("http_client", ("RecordsEndpoint", "http-records-handler", "http-table-records-handler")),
    UsedModule(
        "main",
        ("RecordsSettings", "MaintenancePlan", "records-settings", "records-connected", "pg-handlers-of", "records-serving", "PG-STORE"),
    ),
    UsedModule("admission", ("key-text", "row-matches?", "retention-group-of", "key-from-text")),
    UsedModule(
        "laws",
        (
            "LAW-SCHEMA",
            "LawHarness",
            "law-committed-changes-appear-once-in-order",
            "law-stale-put-conflicts",
            "law-undeclared-writes-are-refused",
            "law-put-rows-is-all-or-nothing",
        ),
    ),
    UsedModule(
        "wire",
        (
            "PATH-PREFIX",
            "ANSWER-KINDS",
            "OPERATIONS",
            "STATUS-OF-ERROR",
            "WireAnswer",
            "WireRefusal",
            "encode-request",
            "answer-from",
            "refusal-from",
        ),
    ),
)


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    assert unknown_in_users(tmp_path, "doeff_records", USED) == ()


# records-connected の答えは本体の答え(契約の :tp [T] — #2893)。ANSWER = 呼び手が答えを受ける型。
FLOW = """\
(require doeff-hy.macros [defk <-])
(import doeff [Pure])
(import doeff_records.main [RecordsSettings records-connected])

(defk answer-of [settings]
  {:pre [(: settings RecordsSettings)] :post [(: % ANSWER)]}
  "本体の答えを呼び手の型で受けるため。"
  (<- answer ANSWER (records-connected settings (Pure 1)))
  answer)
"""


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_the_body_answer_type_reaches_the_caller(tmp_path: Path) -> None:
    assert strict_errors(tmp_path, FLOW.replace("ANSWER", "int")) == ()


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_taking_the_body_answer_as_another_type_is_red(tmp_path: Path) -> None:
    # 失敗ケース: 答えが Incomplete だった時(#2893 の前)は、int の答えを str で受けても赤にならなかった。
    errors = strict_errors(tmp_path, FLOW.replace("ANSWER", "str"))
    assert [e for e in errors if '"int"' in e and '"str"' in e]


# 記録の合図の源を使い手の形で並べる(#3104): 工場 records-signal-handler を購読者の列と同じ with-handlers の列に呼びの字面で置く形。
# BINDINGS = 工場に渡す bindings の式。
SIGNAL_SOURCES = """\
(require doeff-hy.macros [defk <-])
(import doeff [Pure with-handlers])
(import doeff_events [EventBus subscribed-event-handler])
(import doeff_records.event_source [ChangedRow SignalTables records-signal-handler])

(defclass Moved []
  "合図の型(検の的)。"
  (setv #^ (get tuple #(ChangedRow ...)) keys #()))

(defk by-factory [bus]
  {:pre [(: bus EventBus)] :post [(: % int)]}
  "工場を購読者の列の内側に呼びの字面で置くため。"
  (<- answer int (with-handlers [(subscribed-event-handler bus "worker" #(Moved)) (records-signal-handler BINDINGS "worker")]
                                (Pure 1)))
  answer)
"""
BOUND = '#((SignalTables :signal Moved :tables #("jobs")))'


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_the_signal_source_in_the_users_shape_passes_strict(tmp_path: Path) -> None:
    # 失敗ケース: 引数 tuple と答え Callable に型の引数が無かった .pyi では、使い手の strict の門が「一部分からない」で止めた。
    assert strict_errors(tmp_path, SIGNAL_SOURCES.replace("BINDINGS", BOUND)) == ()


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_bindings_of_another_type_are_red_at_the_factory(tmp_path: Path) -> None:
    # 工場の引数の型(tuple[SignalTables, ...])が使い手に届く — 表の名前を直に渡すと赤。
    errors = strict_errors(tmp_path, SIGNAL_SOURCES.replace("BINDINGS", '"jobs"'))
    assert [e for e in errors if "records_signal_handler" in e and "SignalTables" in e], errors


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
