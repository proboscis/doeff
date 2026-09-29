"""macro が変わった Hy の module の古い bytecode を使わない(agora-redesign #1292・親 #1290)。

Hy の module の bytecode は source だけでなく、展開に使った macro にも依る。Python の .pyc の有効判定は
source の更新時刻と大きさ(または source の hash)しか見ないので、macro の定義(doeff-hy の macros.hy など)を
変えても、使う側の module は古い展開のまま動く(agora-redesign #1003 の赤)。

この package は Hy に手を入れずに(利用者の決定 2026-09-29「Hy には一切手を入れない。回避は doeff の側に置く」)、
Python 標準の ``importlib.machinery.SourceFileLoader`` の 2 つの口を包んで同じ正しさを出す:

- ``source_to_code``(compile の口): Hy の module を compile した直後に、その module の macro の提供元の file と
  sha256 の一覧を、code object の定数の末尾に 1 つ足す。.pyc は標準の形のまま(隣に別の file を書かない)。
- ``get_code``(読みの口): .pyc から読んだ Hy の module の code に載った一覧を今の file と突き合わせ、1 つでも
  変わっていれば source から compile し直して .pyc を書き直す。

入れる所は venv の起動時(doeff-hy が配る ``doeff_hy_bytecode_guard.pth``)と ``import doeff_hy`` の 2 か所。
どちらも :func:`install` を呼ぶだけで、何度呼んでも 1 度しか包まない。Hy の import の前でも後でも効く
(前なら Hy の ``source_to_code`` の下に、後なら上に入る — どちらでも compile の直後に一覧を足せる)。

この package は venv の全ての Python の起動時に読まれるので、ここと :mod:`doeff_hy_bytecode_guard.loader_hooks` は
標準 library の軽い module しか import しない。記録の計算と照合(hashlib・marshal)は
:mod:`doeff_hy_bytecode_guard.records` にあり、Hy の module を初めて読む時にだけ import する。
"""

from doeff_hy_bytecode_guard.loader_hooks import file_sha256 as file_sha256
from doeff_hy_bytecode_guard.loader_hooks import install as install
from doeff_hy_bytecode_guard.loader_hooks import installed as installed
from doeff_hy_bytecode_guard.loader_hooks import macro_dependencies as macro_dependencies
