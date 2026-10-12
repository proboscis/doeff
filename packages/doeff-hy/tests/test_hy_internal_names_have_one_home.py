"""Hy の内部の名(macro の表の名・export の名の表の名・Hy が一時の名に振る先頭)の字は、定義元 1 か所だけが持つ(card ki-ffe72a1fb7d7)。

Hy が module の名前空間に置く表の名(``_hy_macros``・``_hy_reader_macros``・``_hy_export_macros``)と、一時の名の先頭(``_hy_``)の定義元は
``doeff_hy_bytecode_guard.records``(``MACRO_TABLE``・``READER_TABLE``・``EXPORT_NAMES``・``GENERATED_NAME_PREFIX``・
``ANON_VAR_NAME``)で、使い手は字を持たず、records の定数と関数(``macro_tables``・``exported_macro_names``・
``fold_anon_var_numbers``)で引く。字を他の file へ写すと、Hy が名を替えた時に直す所が 2 つ以上になり、uv.lock の版の Hy に当てる検
(test_bytecode_guard.py)が測るのは records の読みだけなので、写しは測られないまま古くなる(card ki-eafa369600cd の担い手が見つけた
3 か所 — doeff_effect_analyzer.result_cache・doeff_hy.static_baseline・macro_use の export の名の表 — の形)。

この検は、repo の production の source(.py と .hy — tests・docs・記録の dir は数えない)から、空白を含まない文字列の定数(名・regex の
綴り)に ``_hy_`` で始まる名を含む物を数え、records の外に 1 つでも在れば名指して赤にする。説明の文(空白を含む)・docstring・注釈は
名を書いてよい。``_could_be_hy_src`` のように語の途中に ``_hy_`` が入る名は数えない。

数えるのは綴られた名だけで、名の別の書き方(``_(?:hy|lazy)_`` のように先頭を分けた regex・名を継ぎ足して作る文字列)は数えない。
依りそのものは test_bytecode_guard.py の検が実物の Hy に当てて測るので、この走査は「字が 2 つ目の定義元になる」最も素直な形を
止める役で、測りの代わりではない(card ki-ffe72a1fb7d7 の 3 か所のうち、static_baseline の regex は先頭を分けた形だったので、
この走査では数えられなかった)。
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
#: 名の定義元(この file だけが字を持つ)。
HOME = ROOT / "packages" / "doeff-hy" / "src" / "doeff_hy_bytecode_guard" / "records.py"
#: 降りない dir(隠し dir・build の出力・検・文書・記録 — production の source を持たない)。
NOT_WALKED = re.compile(r"^(\..*|node_modules|target|build|dist|__pycache__|tests|docs|notes|sessions|publications)$")
#: 検の file(dir が tests でない置き場の物)。
TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest)\.(py|hy)$")
#: Hy が振る名の先頭(語の途中の ``_hy_`` — ``_could_be_hy_src`` など — は数えない)。
HY_NAME = re.compile(r"(?<![A-Za-z0-9])_hy_")
#: Hy の source の文字列の綴り(行をまたぐ物も)。
HY_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
HY_COMMENT_LINE = re.compile(r"^\s*;.*$", re.MULTILINE)


def _production_sources() -> Iterator[Path]:
    """repo の production の .py と .hy(HOME・検の file・NOT_WALKED の dir の下を除く)。"""
    for current, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(name for name in dirs if not NOT_WALKED.match(name))
        for name in sorted(files):
            path = Path(current, name)
            if path.suffix in {".py", ".hy"} and not TEST_FILE.match(name) and path != HOME:
                yield path


def _is_spelling(text: str) -> bool:
    """文字列の定数が Hy の内部の名の綴りか(名・regex の綴り — 空白を含む説明の文は含まない)。"""
    return not any(char.isspace() for char in text) and HY_NAME.search(text) is not None


def _spellings(path: Path) -> Iterator[tuple[int, str]]:
    """file の中の Hy の内部の名の綴りの文字列の定数(行・文字列)。docstring は説明の文なので、空白を含み数えられない。"""
    text = path.read_text(encoding="utf-8")
    if "_hy_" not in text:
        return
    if path.suffix == ".py":
        for node in ast.walk(ast.parse(text, filename=str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _is_spelling(node.value):
                yield node.lineno, node.value
        return
    code = HY_COMMENT_LINE.sub("", text)
    for found in HY_STRING.finditer(code):
        if _is_spelling(found.group(0)):
            yield code.count("\n", 0, found.start()) + 1, found.group(0)


def test_no_file_but_records_spells_a_name_hy_gives_its_tables_and_temporaries() -> None:
    """Hy の内部の名の綴りの文字列の定数は、定義元 records.py の外に 0 件。"""
    copies = [
        f"{path.relative_to(ROOT)}:{line}: {text}"
        for path in _production_sources()
        for line, text in _spellings(path)
    ]
    assert copies == [], (
        f"Hy の内部の名(_hy_ で始まる名)の綴りが records の外に在る: {copies} — 名は {HOME.relative_to(ROOT)} の定数"
        "(MACRO_TABLE・READER_TABLE・EXPORT_NAMES・GENERATED_NAME_PREFIX)と関数(macro_tables・exported_macro_names・"
        "fold_anon_var_numbers)で引く。足りない形なら records に足す(Hy の版上げで測る検は records の読みを測る)"
    )


def test_the_scan_sees_a_spelling_in_each_form_a_copy_takes(tmp_path: Path) -> None:
    """走査が見逃さない確かめ(失敗ケース): 名の字・regex の綴り・f-string の先頭・Hy の source の字が数えられ、説明の文・docstring・
    語の途中の ``_hy_``(``_could_be_hy_src``)は数えられない。"""
    python = tmp_path / "copy.py"
    python.write_text(
        '"""説明: _hy_macros を読む。"""\n'
        'A = "_hy_macros"\n'
        'B = r"(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)"\n'
        'C = f"_hy_{x}_{y}"\n'
        'D = "_could_be_hy_src"\n'
        'E = "error: _hy_macros is missing"\n',
        encoding="utf-8",
    )
    hy = tmp_path / "copy.hy"
    hy.write_text(
        '; "_hy_commented" is a comment\n'
        '(defn f []\n  "docstring: _hy_macros の説明"\n  (get (vars module) "_hy_macros"))\n',
        encoding="utf-8",
    )
    assert [text for _, text in _spellings(python)] == [
        "_hy_macros",
        "(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)",
        "_hy_",
    ]
    assert [text for _, text in _spellings(hy)] == ['"_hy_macros"']
