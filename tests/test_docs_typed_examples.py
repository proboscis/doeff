"""README と入門の章の、型を書いた例が一番厳しい型の check で error 0 のままである事の検(取り組み doeff-publish・
card ki-92c3133da46d)。

型の報告(2026-10-17-doeff-user-typecheck.html)の抜け 10・11: README と入門の例は `yield` と注記なしで書いてあり、pyright の
strict では入門の 76 例のうち 75 例が error、mypy は注記の無い @do の関数を `def (*Any, **Any) -> Any` と読んで中身を見ない。
型の書き方は型の章(docs/23)にだけ在った。README の節 Type checking と入門の節 Adding Types に、`yield from` と
`Generator[effects, Any, result]` の注記で書いた例を 1 つずつ置き、mypy では注記が要る事を書いた。

例を走らせて印字を注記と比べるのは tests/test_readme_examples.py と tests/test_docs_entry_path_examples.py。この検は、同じ囲みが
pyright の strict と mypy --strict で error 0 である事と、例が注記で言う結果の型を 2 つの道具が答える事を守る — 型の宣言を
変えて例が赤になれば、写した利用者の editor も赤になる。結果の型を確かめないと、doeff の型が Any に落ちても strict は error を
出さずに緑になる(Any は strict でも誤りではない)。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, examples
from tests.test_static_typing import _pyright_python


@dataclass(frozen=True)
class TypedExample:
    """型を書いた例を置く節 1 つ。"""

    # repo の根からの path
    page: str
    # 例の在る節の見出し(`## ` の後)
    section: str
    # 例が最後に作る値の名と、その行の注記で言う型(`summary = run(...)  # summary: str`)
    result: str
    result_type: str


TYPED_EXAMPLES = (
    TypedExample("README.md", "Type checking", "summary", "str"),
    TypedExample("docs/01-getting-started.md", "Adding Types", "result", "str"),
)
# 節が書く事(card の守り方 — mypy では注記が要る)。
MYPY_NEEDS_ANNOTATIONS = "mypy needs the annotations"
# 子 1 つの制限時間(秒)。pyright も mypy も 2 file で 約 5〜15 秒。
CHILD_DEADLINE_SECONDS = 180


def _section_text(typed: TypedExample) -> str:
    """節の本文(見出しの次の行から、次の `## ` の見出しの前まで)。"""
    text = (ROOT / typed.page).read_text(encoding="utf-8")
    heading = re.search(rf"^## {re.escape(typed.section)}[ \t]*$", text, re.MULTILINE)
    assert heading is not None, f"{typed.page} に節 {typed.section} が無い"
    following = re.search(r"^## ", text[heading.end() :], re.MULTILINE)
    return (
        text[heading.end() :]
        if following is None
        else text[heading.end() : heading.end() + following.start()]
    )


def _typed_fence(typed: TypedExample) -> Example:
    """節の、走らせる python の囲み(ちょうど 1 つ)。"""
    found = [
        example
        for example in examples.fences((ROOT / typed.page).read_text(encoding="utf-8"), typed.page)
        if example.section == typed.section and example.language == "python" and example.runs()
    ]
    assert len(found) == 1, (
        f"{typed.page} の節 {typed.section} の走らせる python の囲みが {len(found)} 個"
    )
    return found[0]


def _write_checked(tmp_path: Path, number: int, typed: TypedExample) -> Path:
    """例の後に、注記で言う結果の型の assert_type を足した file を書く — 型が Any に落ちれば 2 つの道具とも赤にする。"""
    source = tmp_path / f"typed_example_{number}.py"
    source.write_text(
        f"{_typed_fence(typed).code}\nfrom typing import assert_type\n\n"
        f"assert_type({typed.result}, {typed.result_type})\n",
        encoding="utf-8",
    )
    return source


def _checked_sources(tmp_path: Path) -> list[Path]:
    """TYPED_EXAMPLES の全部を、型の check にかける file に書く。"""
    return [_write_checked(tmp_path, number, typed) for number, typed in enumerate(TYPED_EXAMPLES)]


@pytest.mark.parametrize("typed", TYPED_EXAMPLES, ids=lambda typed: typed.page)
def test_typed_example_uses_yield_from_and_the_generator_annotation(typed: TypedExample) -> None:
    code = _typed_fence(typed).code
    assert "yield from " in code, (
        f"{typed.page} の型の例が yield from を使わない — yield の答えは Any"
    )
    assert re.search(r"\) -> Generator\[", code), (
        f"{typed.page} の型の例が @do の関数に Generator の注記を書かない"
    )
    # 下の 2 つの検が足す assert_type は、例が言う結果の型を確かめる — 注記と表がずれれば、ここで名指す
    assert f"# {typed.result}: {typed.result_type}" in code, (
        f"{typed.page} の型の例が `# {typed.result}: {typed.result_type}` と言わない — 表 TYPED_EXAMPLES を例に合わせる"
    )
    assert MYPY_NEEDS_ANNOTATIONS in _section_text(typed), (
        f"{typed.page} の節が mypy では注記が要る事を書かない"
    )


def test_typed_examples_have_no_error_under_pyright_strict(tmp_path: Path) -> None:
    sources = _checked_sources(tmp_path)
    # repo の設定を継がない — 継いだ設定が規則を切っていれば、strict に戻らない
    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        json.dumps({"typeCheckingMode": "strict", "pythonVersion": "3.11"}), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pyright",
            "--project",
            str(config),
            "--pythonpath",
            str(_pyright_python(tmp_path)),
            "--outputjson",
            *map(str, sources),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS,
        check=False,
    )
    report = json.loads(result.stdout)
    assert report["summary"]["filesAnalyzed"] == len(sources), report["summary"]
    errors = [
        f"{Path(item['file']).name}:{item['range']['start']['line'] + 1}: {item['message']}"
        for item in report["generalDiagnostics"]
        if item["severity"] == "error"
    ]
    assert errors == [], errors


def test_typed_examples_have_no_error_under_mypy_strict(tmp_path: Path) -> None:
    pytest.importorskip(
        "mypy",
        reason="mypy が無い venv(Python Compatibility の CI は dev の group の maturin と pyright だけを入れる)",
    )
    sources = _checked_sources(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "mypy",
            "--strict",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--show-error-codes",
            "--no-color-output",
            "--no-pretty",
            *map(str, sources),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS,
        check=False,
    )
    # 起動できない時も終了コードは 1 なので、stderr が空である事で「走って赤を出した」と「走れなかった」を分ける
    assert result.stderr == "", result.stderr
    assert result.returncode == 0, result.stdout
    assert f"no issues found in {len(sources)} source files" in result.stdout, result.stdout
