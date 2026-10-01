"""defsystem の展開と doeff_cluster.shared.intent.service_model の型(service_model.pyi)の失敗ケース(agora-redesign #2291・#2279 の対)。

defsystem は `doeff_cluster.shared.intent.service_model.system_of` / `job` / `CallShape` を呼ぶ関数に展開する。service_model.hy は Hy の
module で、型の宣言(service_model.pyi)が無いと pyright はこれらを Unknown として読み、系の関数の戻りまで Unknown に
引きずられる(書き手に直せない赤)。また、引数に型の注記を持つ系の展開が、置いた後の関数の属性 `__doeff_system__` を
読み直していた所は reportFunctionMemberAccess になっていた。宣言と展開の直しが在れば:

- defsystem を書いた file に、service_model の名と系の関数の戻りの reportUnknown*・reportFunctionMemberAccess が出ない。
- 系の関数の答えは System で、欄の型の取り違え(str の欄 name に 1 を足す)は赤になる。
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk defsystem val])

(defclass Ping []
  "土台の型の見本(系の引数の型の注記 — 展開が __doeff_system__ の param_types に載せる)。")

(defk tally [step]
  {:pre [(: step int)] :post [(: % int)]}
  step)

(defsystem lab [#^ Ping foundation]
  "見本の系"
  (tally (tally 2) :needs #{"net"} :environ {"TALLY_BASE" "1"}))

(val declared (lab (Ping)))
(val first-name (. (get declared.jobs 0) name))
(val wrong (+ declared.name 1))
"""

#: 型が見えないと Unknown になる名(service_model の名・系の関数・その答え)。
WATCHED = ('"service_model"', '"system_of"', '"job"', '"CallShape"', '"declared"', '"first_name"')


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(JSON の診断)。"""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy.static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


@needs_pyright
def test_the_system_expansion_has_no_unknown_or_function_member_reads(probe: Path) -> None:
    errors = _check(probe).errors()
    # 展開が関数の属性 __doeff_system__ を読む文を出さない(型の注記つきの引数 = param_types の道)。
    assert not [e for e in errors if e[0] == "reportFunctionMemberAccess"], errors
    # service_model の名・系の関数の答えに「型が分からない」の赤が出ない(service_model.pyi が無いと Unknown)。
    unknown = [e for e in errors if e[0].startswith("reportUnknown")]
    assert not [e for e in unknown if any(n in e[2] for n in WATCHED)], unknown
    # 系の宣言の行(10〜12 行目)と答えの読み(14・15 行目)には赤が 1 つも無い。
    assert not [e for e in errors if e[1] in (10, 11, 12, 14, 15)], errors


@needs_pyright
def test_a_system_field_used_as_the_wrong_type_is_red(probe: Path) -> None:
    # 系の関数の答えが System と読めるので、str の欄 name に 1 を足す(16 行目)と型の取り違えで赤。
    errors = _check(probe).errors()
    assert [e for e in errors if e[1] == 16 and e[0] == "reportOperatorIssue"], errors


#: 空の :needs と空でない :needs の系(agora-redesign #2396)。空の #{} は展開で frozenset([]) になる。frozenset([]) を変数に
#: 置けば frozenset[Unknown] だが、展開は job の名の引数 needs に直に渡すので、service_model.pyi の注記
#: (frozenset[str] | set[str] | …)から双方向の推論で frozenset[str] と読まれる。注記が消えるか、展開が値を一度変数に置く形に
#: 変われば、書き手に直せない赤になる — その退行をここで止める。
EMPTY_NEEDS = """\
(require doeff-hy.macros [defk defsystem])

(defclass Ping []
  "土台の型の見本")

(defk tally [step]
  {:pre [(: step int)] :post [(: % int)]}
  step)

(defsystem lab [#^ Ping foundation]
  "空の :needs の系"
  (quiet (tally 1) :needs #{})
  (noisy (tally 2) :needs #{"net" "pg"}))
"""


@needs_pyright
def test_an_empty_needs_set_reads_as_a_set_of_names(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(EMPTY_NEEDS, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if "Unknown" in e[2] or e[0].startswith("reportUnknown")], errors
    # 系の宣言の行(10〜13 行目)には赤が 1 つも無い。
    assert not [e for e in errors if 10 <= e[1] <= 13], errors
