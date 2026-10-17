"""型の章(docs/23 と docs/24)の例を、読み手が写した形のまま pyright の strict と mypy の --strict にかけ、赤が出ない事の検
(取り組み doeff-publish・card ki-b370d8d59e8c)。

型の報告(2026-10-17-doeff-user-typecheck.html の抜け 9): 型の章の 4 例は import と定義の抜けた抜き書きで、写して型の check に
かけると名が 16 個解けなかった。例を走らせる検(tests/test_docs_page_examples.py)は、例が走って注記の通りに印字する事を見るが、
型の check は見ない。型の章は「写せば pyright の strict でも mypy の --strict でも赤が出ない」と言うので、それをここで確かめる。

page の囲みを、例の検と同じ約束(tools/readme_examples.py)で page ごとの dir の file に書く: continues の囲みは上の囲みに
書き足した script、file= の囲みはその名の file。読み手が上から写して途中で止めた形のどれもが型の check を通る。excerpt の囲み
(program の数行だけの断片・わざと型の誤りを見せる例)は書かない。読み手には excerpt の印が見えないので、page は「import の行を
持つ例が 1 つの file を作る」と言い、断片が import を持たない事もここで確かめる。かける page は pyproject.toml の契約の表の、
この検を選ぶ行から読む(tests/test_docs_page_examples.py と同じ — page を足す commit がこの file の一覧で衝突しない)。

README と入門の章の、型を書いた例の節 1 つずつは tests/test_docs_typed_examples.py が見る(節の例 1 つと、注記で言う結果の型)。
この検は型の章の page の例の全部を見る。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_docs_entry_path_examples import _fences
from tests.test_docs_page_examples import (
    DOCS_PREFIX,
    prefixes_selecting,
    prefixes_that_are_not_pages,
)
from tests.test_readme_examples import ROOT, examples
from tests.test_static_typing import _pyright_python

# この検の file(repo の根からの path — pyproject.toml の契約の表の書き方)。
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# 型の check にかける page(docs/ からの名)— 契約の表の、この検を選ぶ行が 1 点の定義。
TYPED_PAGES = tuple(prefix.removeprefix(DOCS_PREFIX) for prefix in prefixes_selecting(THIS_CHECK))
# 型の check にかける囲みの言葉。
PYTHON = "python"
# import の行(断片の囲みが持たない物)。
_IMPORT_LINE = re.compile(r"^(?:import|from)\s", re.MULTILINE)


def _write_page(page: str, directory: Path) -> list[Path]:
    """page の、file に書く Python の囲み(script と file=)を directory に書き、書いた file の列を返す。"""
    directory.mkdir()
    return [
        examples.write(example, directory)
        for example in _fences(page)
        if example.language == PYTHON and example.written_as() is not None
    ]


def _pyright_findings(directory: Path, files: list[Path], tmp_path: Path) -> list[str]:
    """files を、directory を根にした strict の pyright にかけ、error と warning を「file:行: 規則: 文」の列で返す。"""
    original = json.loads((ROOT / "pyrightconfig.json").read_text())
    config = directory / "pyrightconfig.json"
    config.write_text(
        json.dumps(
            {
                "extends": str(ROOT / "pyrightconfig.json"),
                "typeCheckingMode": "strict",
                "extraPaths": [str(ROOT), *(str(ROOT / path) for path in original["extraPaths"])],
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyright",
            "--project",
            str(config),
            "--pythonpath",
            str(_pyright_python(tmp_path)),
            "--outputjson",
            *map(str, files),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    report = json.loads(result.stdout)
    return [
        f"{Path(item['file']).name}:{item['range']['start']['line'] + 1}: {item.get('rule', '')}: {item['message']}"
        for item in report["generalDiagnostics"]
        if item["severity"] in {"error", "warning"}
    ]


@pytest.mark.parametrize("page", TYPED_PAGES)
def test_page_examples_are_clean_under_strict_pyright(page: str, tmp_path: Path) -> None:
    directory = tmp_path / Path(page).stem
    files = _write_page(page, directory)
    # 書く囲みが無い page は、何も確かめていない
    assert files, f"docs/{page} に、file に書く Python の囲みが無い"
    assert _pyright_findings(directory, files, tmp_path) == []


@pytest.mark.parametrize("page", TYPED_PAGES)
def test_page_examples_are_clean_under_strict_mypy(page: str, tmp_path: Path) -> None:
    # mypy は dev の group の道具(make sync で入る)。Python Compatibility の CI の venv は dev の group から maturin と pyright
    # だけを入れるので、そこでは訳を出して飛ばす(tests/test_handler_installer_typing.py と同じ)
    pytest.importorskip(
        "mypy",
        reason="mypy が無い venv(Python Compatibility の CI は dev の group の maturin と pyright だけを入れる)",
    )
    directory = tmp_path / Path(page).stem
    files = _write_page(page, directory)
    assert files, f"docs/{page} に、file に書く Python の囲みが無い"
    # cwd を例の dir にする — repo の根で走らせると、mypy は木の doeff を読む相手でなく検める相手として拾う
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
            "--hide-error-context",
            "--no-error-summary",
            "--no-color-output",
            "--no-pretty",
            *(path.name for path in files),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    # 空の stderr と出力と終了コード 0 = mypy が走り、所見が無い(走れない時は stderr に書いて 2 で終わる)
    assert result.stderr == ""
    assert result.stdout.splitlines() == []
    assert result.returncode == 0


@pytest.mark.parametrize("page", TYPED_PAGES)
def test_page_excerpts_have_no_imports(page: str) -> None:
    # import の行を持つ例を excerpt にすると、page が「1 つの file を作る」と言う例が型の check から黙って外れる
    excerpts_with_imports = [
        example.name
        for example in _fences(page)
        if example.language == PYTHON
        and examples.EXCERPT in example.words
        and _IMPORT_LINE.search(example.code)
    ]
    assert excerpts_with_imports == []


def test_rows_selecting_this_check_name_docs_pages() -> None:
    # 表の行が page を変えた commit の `make test-changed` にこの検を選ばせ、同じ行がかける page を決める。行の接頭辞が page で
    # ないなら、その page の例はどの型の check にもかからない。
    prefixes = prefixes_selecting(THIS_CHECK)
    assert prefixes, (
        f"pyproject.toml の [[tool.doeff.contract-tests]] に、{THIS_CHECK} を選ぶ行が無い"
    )
    assert prefixes_that_are_not_pages(prefixes) == []
