"""計器・最新の値・file system の答え手と計器の effect の型の宣言(memory_meter.pyi・memory_latest.pyi・os_file.pyi・
meter_effects.pyi)の失敗ケース。

4 つの module は Hy なので、型の宣言が無いと pyright は import した答え手と計器の effect・設定を全部 Unknown として読み、
答え手を並べる使い手の strict に、書き手に直せない reportUnknown* が連なる。

- 失敗ケース: 同じ小さな .hy を、4 つの宣言を外した写しと置いた写しの 2 通りで doeff-hy-check --strict にかける。
  外すと import した名・答え手を被せた Program の答え・計器の断面が Unknown の赤になり、置くと消える。置いた側では
  型の取り違え(int の答えに文字列を足す)が赤になる(宣言が答え手の答えの型を本文から運んでいる)。
- 宣言と実装の名・引数の一致は test_hy_module_stubs.py が検める。
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "doeff_core_effects"

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

#: この検が見る宣言(外した写しでは無い)。
STUBS: tuple[str, ...] = ("memory_meter.pyi", "memory_latest.pyi", "os_file.pyi", "meter_effects.pyi")

#: 写しの package の名(入れた doeff_core_effects と別の名 — _copy_renamed)。
COPY_NAME = "copied_core_effects"

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import copied_core_effects.meter_effects [MeterSettings ReadMeter])
(import copied_core_effects.memory_meter [memory-meter-handler])
(import copied_core_effects.memory_latest [memory-latest-handler])
(import copied_core_effects.os_file [os-file-handler])

(val SETTINGS (MeterSettings))

(defk counter-count []
  {:pre [] :post [(: % int)]}
  (<- snapshot (ReadMeter))
  (len snapshot.counters))

(defk handled-count []
  {:pre [] :post [(: % int)]}
  (<- counted (os-file-handler (memory-latest-handler ((memory-meter-handler SETTINGS) (counter-count)))))
  counted)

(defk wrong-count []
  {:pre [] :post [(: % int)]}
  (<- counted (os-file-handler (memory-latest-handler ((memory-meter-handler SETTINGS) (counter-count)))))
  (+ counted "x"))
"""

#: 宣言を外すと Unknown になる名(import の行の名・答えを受けた変数)。
UNKNOWN_NAMES: tuple[str, ...] = (
    '"MeterSettings"',
    '"ReadMeter"',
    '"memory_meter_handler"',
    '"memory_latest_handler"',
    '"os_file_handler"',
    '"snapshot"',
    '"counted"',
)


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

    def unknown(self) -> list[tuple[str, int, str]]:
        return [e for e in self.errors() if e[0].startswith("reportUnknown")]


def _line_of(marker: str) -> int:
    """検体の中で marker を含む行の番号(1 から)。"""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


def _copy_renamed(target: Path, *, with_stubs: bool) -> None:
    """doeff_core_effects を別の名(COPY_NAME)の package として target へ写す(with_stubs でなければ STUBS を外す)。

    同じ名のまま写すと、入れた doeff_core_effects も __init__.py を持つ普通の package なので、pyright は写しで解けない
    module(宣言を外した .hy)を入れた側の宣言で解き、外したはずの宣言を読む。別の名にすると写しの中だけで解ける。
    """
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns("__pycache__"))
    if not with_stubs:
        for name in STUBS:
            (target / name).unlink()
    for path in target.rglob("*"):
        if path.suffix in {".py", ".pyi", ".hy"}:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("doeff_core_effects", COPY_NAME), encoding="utf-8")


def _check(tmp_path: Path, *, with_stubs: bool) -> Run:
    """写し(_copy_renamed)を import の根に置き、検体を doeff-hy-check --strict にかける。"""
    from doeff_hy.static_check import main

    copy = tmp_path / ("stubbed" if with_stubs else "bare") / COPY_NAME
    _copy_renamed(copy, with_stubs=with_stubs)
    root = tmp_path / "root"
    root.mkdir()
    (root / "probe.hy").write_text(MODULE, encoding="utf-8")
    (root / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [str(copy.parent)]}), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_without_the_stubs_the_handlers_are_unknown(tmp_path: Path) -> None:
    unknown = _check(tmp_path, with_stubs=False).unknown()
    missing = [name for name in UNKNOWN_NAMES if not any(name in e[2] for e in unknown)]
    assert missing == [], unknown


@needs_pyright
def test_with_the_stubs_nothing_is_unknown_and_a_wrong_type_is_red(tmp_path: Path) -> None:
    run = _check(tmp_path, with_stubs=True)
    # 正しい使い(検体の頭から wrong-count の前まで)には赤が 1 つも無い — Unknown も型の取り違えも。
    right = range(1, _line_of("(defk wrong-count"))
    assert [e for e in run.errors() if e[1] in right] == [], run.errors()
    # int の答えに文字列を足すと赤(宣言が答え手を被せた Program の答えの型 int を本文から運ぶ)。
    wrong = _line_of('(+ counted "x")')
    assert [e for e in run.errors() if e[1] == wrong and e[0] == "reportOperatorIssue"], run.errors()
