"""今の compile が型検査のための展開かの印 — 持ち主はこの module 1 つ(doeff_hy.static_view が立て、包みの compile の口が読む)。

型検査のための展開(doeff-hy-check・:func:`doeff_hy.static_view.static_view`)は、:pre の isinstance と実行の時の import を
出さない、実行できない code を作る。その最中に require で初めて読み込まれた macro の提供元の module に macro の依存の記録を
付けると、その code は .pyc と共有の code の置き場(``DOEFF_HY_CODE_STORE``)に普通の展開と同じ鍵で残り、後の普通の import が
それを読んで ``NameError: name '_doeff_do' is not defined`` で落ちる(milestone I の I-3 の再現 2026-10-03)。包みの compile の口は
この印が立っている間の compile に記録を付けない — 記録の無い code は置き場に入らず、.pyc に書かれても次の普通の import が
compile し直す。

起動時に読まれる包みから import されるので、標準 library しか import しない。
"""

from contextvars import ContextVar

#: 型検査のための展開の最中か(既定 = 普通の展開)。context ごとの値なので、同じ process の別の thread の import には効かない。
TYPE_CHECK_EXPANSION: ContextVar[bool] = ContextVar("doeff_hy_static_view", default=False)
