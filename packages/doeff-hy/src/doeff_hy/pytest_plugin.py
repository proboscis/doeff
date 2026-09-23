"""doeff-hy の pytest plugin(entry point `pytest11`)。

pytest は起動時に plugin と conftest を読み、fixture の注記を Python 3.14 では
`annotation_format=Format.STRING` で読む。Hy がそれより前に(他の plugin や conftest から)
import されていると、Hy の `ast.unparse` の差し替えがそこで止まらない再帰になる
(doeff_hy/ast_unparse.py)。`import doeff_hy` を待たず、pytest の起動の時点で置き換える。
"""

from doeff_hy.ast_unparse import install

install()
