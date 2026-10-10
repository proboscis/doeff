"""defeffect の :pre と defwire / defrecord の :check が、型検査の展開で書き手の直せない赤を出さないことの失敗ケース
(agora-redesign #4196)。

- defeffect: `__post_init__` が :pre の検めのために全部の欄を `<欄> = self.<欄>` で束縛していたので、:pre が使わない欄が
  reportUnusedVariable の赤になっていた。:pre が参照する欄だけを束縛する(defrecord の :check と同じ形)。
- defwire / defrecord の :check: 検め式の答えを `verdict is not True` で比べるので、答えが `re.Match` の式は
  reportUnnecessaryComparison の赤になっていた(Match | None と True は重ならない)。型検査の展開では答えを object に広げる。
- 実行時の意味は変えない: :pre が使わない欄があっても、使う欄の検めは作る時に断る。:check の Match の答えは真偽で読む。
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defwire defrecord])
(import dataclasses [dataclass])
(import re)

(defeffect PutProfile
  "検体の effect(:pre は 2 つの欄のうち name だけを使う)。"
  {:fields [(: name str) (: size int)]
   :pre [(: name str) (> (len name) 0)]
   :answer None
   :tags {:context "probe" :role "intent"}})

(defwire ParkRow
  "検体の wire(:check は re.Match を返す)。"
  {:names :camel
   :check [(re.fullmatch "c-[0-9A-Z]+" conversation-id)]}
  (#^ str conversation-id))

(defrecord Span
  "検体の record(:check は re.Match を返す)。"
  {:check [(re.match "[a-z]+" label)]}
  (#^ str label))
"""


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
def test_pre_and_check_add_no_errors_the_author_cannot_fix(probe: Path) -> None:
    # 直す前は :pre の使わない欄 size の reportUnusedVariable と、:check の 2 つの reportUnnecessaryComparison が赤。
    assert _check(probe).errors() == []


def _namespace() -> dict[str, object]:
    namespace: dict[str, object] = {}
    hy.eval(hy.models.Expression([hy.models.Symbol("do"), *hy.read_many(MODULE)]), namespace)
    return namespace


def test_runtime_pre_still_refuses_the_used_field() -> None:
    put_profile = _namespace()["PutProfile"]
    assert callable(put_profile)
    assert put_profile(name="a", size=3).size == 3
    with pytest.raises(AssertionError, match="PutProfile"):
        put_profile(name="", size=3)


def test_runtime_check_reads_a_match_as_truth() -> None:
    namespace = _namespace()
    park_row = namespace["ParkRow"]
    span = namespace["Span"]
    assert callable(park_row)
    assert callable(span)
    assert park_row(conversation_id="c-9AB").conversation_id == "c-9AB"
    assert span(label="abc").label == "abc"
    with pytest.raises(ValueError, match="conversation-id"):
        park_row(conversation_id="x")
    with pytest.raises(ValueError, match="label"):
        span(label="9")
