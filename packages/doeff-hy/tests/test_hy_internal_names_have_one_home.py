"""Hy の内部の名(macro の表の名・export の名の表の名・Hy が一時の名に振る先頭)の字は、定義元 1 か所だけが持つ(card ki-ffe72a1fb7d7)。

Hy が module の名前空間に置く表の名(``_hy_macros``・``_hy_reader_macros``・``_hy_export_macros``)と、一時の名の先頭(``_hy_``)の定義元は
``doeff_hy_bytecode_guard._records``(``MACRO_TABLE``・``READER_TABLE``・``EXPORT_NAMES``・``GENERATED_NAME_PREFIX``・
``ANON_VAR_NAME``)で、使い手は字を持たず、records の定数と関数(``macro_tables``・``exported_macro_names``・
``fold_anon_var_numbers``)で引く。字を他の file へ写すと、Hy が名を替えた時に直す所が 2 つ以上になり、uv.lock の版の Hy に当てる検
(test_bytecode_guard.py)が測るのは records の読みだけなので、写しは測られないまま古くなる(card ki-eafa369600cd の担い手が見つけた
3 か所 — doeff_effect_analyzer.result_cache・doeff_hy._static_baseline・macro_use の export の名の表 — の形)。

この検は、repo の production の source(.py と .hy — tests・docs・記録の dir は数えない)から、次の綴りを数え、records の外に 1 つでも在れば
名指して赤にする。
- 空白を含まない文字列の定数(名・regex の綴り — bytes・Hy の ``#[[…]]`` の文字列も)が ``_hy_`` で始まる名を含む物。
- 表の名 3 つ(records の定数の値)を名前・属性・import する名として読む形(Python の ``module._hy_macros``・``from m import _hy_macros``・
  Hy の symbol ``(. module _hy_macros)``)。
説明の文(空白を含む文字列)・docstring・注釈は名を書いてよい。``_could_be_hy_src`` のように語の途中に ``_hy_`` が入る名・``__hy_``
で始まる名は数えない(doeff 自身の ``_hy_symbols`` のような関数の名も、表の名でなければ数えない)。

数えるのは綴られた名だけで、名の別の書き方(``_(?:hy|lazy)_`` のように先頭を分けた regex・名を継ぎ足して作る文字列・計算した名の
``getattr``)は数えない。依りそのものは test_bytecode_guard.py の検が実物の Hy に当てて測るので、この走査は「字が 2 つ目の定義元に
なる」素直な形を止める役で、測りの代わりではない(card ki-ffe72a1fb7d7 の 3 か所のうち、static_baseline の regex は先頭を分けた形
だったので、この走査では数えられなかった)。
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from doeff_hy_bytecode_guard._records import EXPORT_NAMES, MACRO_TABLE, READER_TABLE

ROOT = Path(__file__).resolve().parents[3]
#: 名の定義元(この file だけが字を持つ)。
HOME = ROOT / "packages" / "doeff-hy" / "src" / "doeff_hy_bytecode_guard" / "_records.py"
#: 降りない dir(隠し dir・build の出力・検・文書・記録 — production の source を持たない)。
NOT_WALKED = re.compile(r"^(\..*|node_modules|target|build|dist|__pycache__|tests|docs|notes|sessions|publications)$")
#: 検の file(dir が tests でない置き場の物)。
TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest)\.(py|hy)$")
#: Hy が module の名前空間に置く表の名(値は records の定数 — ここに字を持たない)。
TABLE_NAMES = frozenset({MACRO_TABLE, READER_TABLE, EXPORT_NAMES})
#: Hy が振る名の先頭(語の途中・``__hy_`` の途中の ``_hy_`` — ``_could_be_hy_src``・``__hy_injected__`` など — は数えない)。
HY_NAME = re.compile(r"(?<![A-Za-z0-9_])_hy_")
#: Hy の source の文字列の綴り(行をまたぐ物も)と、括弧の文字列 ``#[[…]]``・``#[名[…]名]``。
HY_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
HY_BRACKET_STRING = re.compile(r"#\[([^\[\]]*)\[(.*?)\]\1\]", re.DOTALL)
HY_COMMENT = re.compile(r";[^\n]*")
#: Hy の symbol としての表の名(``-`` も symbol の字なので、前後が名の字でない時だけ)。
HY_TABLE_SYMBOL = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(name) for name in sorted(TABLE_NAMES)) + r")(?![A-Za-z0-9_-])"
)


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


class _Spelling(NamedTuple):
    """Hy の内部の名の綴り 1 つの在りか: 行と綴り。"""

    line: int
    text: str


def _python_spelling(node: ast.AST) -> _Spelling | None:
    """木の節 1 つが読む Hy の内部の名の綴り(行・綴り — 文字列の定数・bytes・表の名の名前・属性・import する名)。無ければ None。"""
    match node:
        case ast.Constant(value=str() as text) as found if _is_spelling(text):
            return _Spelling(found.lineno, text)
        case ast.Constant(value=bytes() as raw) as found if _is_spelling(raw.decode("latin-1")):
            return _Spelling(found.lineno, raw.decode("latin-1"))
        case (ast.Attribute(attr=name) | ast.Name(id=name) | ast.alias(name=name)) as found if name in TABLE_NAMES:
            return _Spelling(found.lineno, name)
        case _:
            return None


def _line_of(text: str, offset: int) -> int:
    """text の offset 文字目の行(1 始まり)。"""
    return text.count("\n", 0, offset) + 1


def _hy_spellings(text: str) -> Iterator[tuple[int, str]]:
    """Hy の source の中の Hy の内部の名の綴り(行・綴り)— 文字列・括弧の文字列・symbol の表の名。注釈と説明の文は数えない。"""
    code = "\n".join("" if line.lstrip().startswith(";") else line for line in text.split("\n"))
    for found in HY_BRACKET_STRING.finditer(code):
        if _is_spelling(found.group(2)):
            yield _line_of(code, found.start()), found.group(2)
    for found in HY_STRING.finditer(code):
        if _is_spelling(found.group(0)):
            yield _line_of(code, found.start()), found.group(0)
    # 文字列を空にして(行は保つ)から注釈を外すと、文字列の中の ``;`` に惑わされず、注釈と説明の文の名は数えない。
    bare = HY_BRACKET_STRING.sub(lambda found: "\n" * found.group(0).count("\n"), code)
    bare = HY_STRING.sub(lambda found: '"' + "\n" * found.group(0).count("\n") + '"', bare)
    uncommented = HY_COMMENT.sub("", bare)  # 注釈は行の中だけなので、行の数えは変わらない
    for found in HY_TABLE_SYMBOL.finditer(uncommented):
        yield _line_of(uncommented, found.start()), found.group(1)


def _spellings(path: Path) -> Iterator[tuple[int, str]]:
    """file の中の Hy の内部の名の綴り(行・綴り — 行の順)。docstring は説明の文なので、空白を含み数えられない。"""
    text = path.read_text(encoding="utf-8")
    if "_hy_" not in text:
        return
    if path.suffix == ".hy":
        yield from sorted(_hy_spellings(text), key=lambda spelled: spelled[0])
        return
    found = (_python_spelling(node) for node in ast.walk(ast.parse(text, filename=str(path))))
    yield from sorted((spelled for spelled in found if spelled is not None), key=lambda spelled: spelled[0])


def test_no_file_but_records_spells_a_name_hy_gives_its_tables_and_temporaries() -> None:
    """Hy の内部の名の綴りは、定義元 records.py の外に 0 件。"""
    copies = [
        f"{path.relative_to(ROOT)}:{line}: {text}"
        for path in _production_sources()
        for line, text in _spellings(path)
    ]
    assert copies == [], (
        f"Hy の内部の名(_hy_ で始まる名・表の名)の綴りが records の外に在る: {copies} — 名は {HOME.relative_to(ROOT)} の定数"
        "(MACRO_TABLE・READER_TABLE・EXPORT_NAMES・GENERATED_NAME_PREFIX)と関数(macro_tables・exported_macro_names・"
        "fold_anon_var_numbers)で引く。足りない形なら records に足す(Hy の版上げで測る検は records の読みを測る)"
    )


def test_the_scan_sees_a_spelling_in_each_form_a_copy_takes(tmp_path: Path) -> None:
    """走査が見逃さない確かめ(失敗ケース): 名の字・regex の綴り・f-string の先頭・bytes・属性の読み・import する名・Hy の source の字と
    symbol が数えられ、説明の文・docstring・注釈・語の途中や ``__hy_`` の途中の ``_hy_``・doeff 自身の ``_hy_`` で始まる関数の名は
    数えられない。"""
    python = tmp_path / "copy.py"
    python.write_text(
        '"""説明: _hy_macros を読む。"""\n'
        "from other import _hy_export_macros\n"
        'A = "_hy_macros"\n'
        'B = r"(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)"\n'
        'C = f"_hy_{x}_{y}"\n'
        'D = "_could_be_hy_src"\n'
        'E = "error: _hy_macros is missing"\n'
        "F = module._hy_reader_macros\n"
        'G = b"_hy_macros"\n'
        "H = _hy_macros\n"
        'I = "__hy_injected__"\n'
        'J = "foo__hy_bar"\n'
        "K = _hy_symbols(form)\n",
        encoding="utf-8",
    )
    hy = tmp_path / "copy.hy"
    hy.write_text(
        '; "_hy_commented" is a comment\n'
        '(defn f []\n  "docstring: _hy_macros の説明"\n'
        '  (get (vars module) "_hy_macros")  ; _hy_export_macros を読む\n'
        "  (. module _hy_reader_macros)\n"
        "  (setv c _hy_export_macros)\n"
        "  #[[_hy_macros]]\n"
        "  (_hy_symbols form))\n",
        encoding="utf-8",
    )
    assert [text for _, text in _spellings(python)] == [
        "_hy_export_macros",
        "_hy_macros",
        "(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)",
        "_hy_",
        "_hy_reader_macros",
        "_hy_macros",
        "_hy_macros",
    ]
    assert [text for _, text in _spellings(hy)] == [
        '"_hy_macros"',
        "_hy_reader_macros",
        "_hy_export_macros",
        "_hy_macros",
    ]
    assert [line for line, _ in _spellings(hy)] == [4, 5, 6, 7]
