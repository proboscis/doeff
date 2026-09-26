"""doeff-hy の pytest plugin(entry point `pytest11` の名 `doeff_hy`)。

pytest は起動時に plugin と conftest を読み、fixture の注記を Python 3.14 では
`annotation_format=Format.STRING` で読む。Hy がそれより前に(他の plugin や conftest から)
import されていると、Hy の `ast.unparse` の差し替えがそこで止まらない再帰になる
(doeff_hy/ast_unparse.py)。`import doeff_hy` を待たず、pytest の起動の時点で置き換えるための plugin。

この module が doeff-hy とは別の配布物にある理由は pyproject.toml の註(pytest の assert の書き換えの印が
配布物の全 module に付き、doeff_hy の .hy を壊す)。
"""

from doeff_hy.ast_unparse import install

install()
