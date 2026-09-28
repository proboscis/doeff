"""doeff-hy の pytest plugin(entry point `pytest11` の名 `doeff_hy`)。

pytest は起動時に plugin と conftest を読み、fixture の注記を Python 3.14 では
`annotation_format=Format.STRING` で読む。Hy がそれより前に(他の plugin や conftest から)
import されていると、Hy の `ast.unparse` の差し替えがそこで止まらない再帰になる
(doeff_hy/ast_unparse.py)。`import doeff_hy` を待たず、pytest の起動の時点で置き換えるための plugin。

もう 1 つの役目は Hy の検の時間の上限(doeff_hy_pytest/budget.py — 設定が無ければ何もしない)。hook は
この module から輸出して、entry point の 1 つ(`doeff_hy`)のまま載せる。

この module が doeff-hy とは別の配布物にある理由は pyproject.toml の註(pytest の assert の書き換えの印が
配布物の全 module に付き、doeff_hy の .hy を壊す)。
"""

from doeff_hy.ast_unparse import install

from doeff_hy_pytest.budget import pytest_addoption as pytest_addoption
from doeff_hy_pytest.budget import pytest_configure as pytest_configure
from doeff_hy_pytest.budget import pytest_make_collect_report as pytest_make_collect_report
from doeff_hy_pytest.budget import pytest_runtest_call as pytest_runtest_call
from doeff_hy_pytest.budget import pytest_runtest_makereport as pytest_runtest_makereport
from doeff_hy_pytest.budget import pytest_terminal_summary as pytest_terminal_summary
from doeff_hy_pytest.budget import pytest_unconfigure as pytest_unconfigure

install()
