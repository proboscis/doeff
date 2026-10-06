"""macro が変わった Hy の module の古い bytecode を使わない(agora-redesign #1292・親 #1290)。

Hy の module の bytecode は source だけでなく、展開に使った macro にも依る。Python の .pyc の有効判定は
source の更新時刻と大きさ(または source の hash)しか見ないので、macro の定義(doeff-hy の macros.hy など)を
変えても、使う側の module は古い展開のまま動く(agora-redesign #1003 の赤)。

この package は Hy に手を入れずに(利用者の決定 2026-09-29「Hy には一切手を入れない。回避は doeff の側に置く」)、
Python 標準の ``importlib.machinery.SourceFileLoader`` の 2 つの口を包んで同じ正しさを出す:

- ``source_to_code``(compile の口): Hy の module を compile した直後に、その module の macro の提供元の file と
  sha256 の一覧を、code object の定数の末尾に 1 つ足す。.pyc は標準の形のまま(隣に別の file を書かない)。
  その前に code の Hy の gensym の名を module の中の順の通し番号へ振り直す(``records.canonical_gensyms`` —
  同じ source の code を compile の順・process・thread に依らず同じにする・agora-redesign #3667)。
- ``get_code``(読みの口): .pyc から読んだ Hy の module の code に載った一覧を、提供元の module 名から今の環境で
  引いた file と突き合わせ、1 つでも変わっていれば source から compile し直して .pyc を書き直す(記録の path は作った木の
  物なので、別の木から引き継いだ .pyc も今の木の macro で照らす — agora-redesign #2598)。

import の外で bytecode を前もって作る道具は :func:`source_to_code_as_import` で compile する(import と同じく module を
置いた中で compile し、記録を付ける — 記録の無い .pyc は読みの口が compile し直す)。前の木から引き継いだ .pyc を
焼き直すかは :func:`bytecode_is_current`(読みの口と同じ照らし方)で決める。

入れる所は venv の起動時(doeff-hy が配る ``doeff_hy_bytecode_guard.pth``)と ``import doeff_hy`` の 2 か所。
どちらも :func:`install` を呼ぶだけで、何度呼んでも 1 度しか包まない。Hy の import の前でも後でも効く
(前なら Hy の ``source_to_code`` の下に、後なら上に入る — どちらでも compile の直後に一覧を足せる)。

この package は venv の全ての Python の起動時に読まれるので、ここと :mod:`doeff_hy_bytecode_guard.loader_hooks` は
標準 library の軽い module しか import しない。記録の計算と照合(hashlib・marshal)は
:mod:`doeff_hy_bytecode_guard.records` にあり、Hy の module を初めて読む時にだけ import する。
"""

from doeff_hy_bytecode_guard.expansion import TYPE_CHECK_EXPANSION as TYPE_CHECK_EXPANSION
from doeff_hy_bytecode_guard.loader_hooks import bytecode_is_current as bytecode_is_current
from doeff_hy_bytecode_guard.loader_hooks import current_record as current_record
from doeff_hy_bytecode_guard.loader_hooks import file_sha256 as file_sha256
from doeff_hy_bytecode_guard.loader_hooks import gensym_renaming as gensym_renaming
from doeff_hy_bytecode_guard.loader_hooks import install as install
from doeff_hy_bytecode_guard.loader_hooks import installed as installed
from doeff_hy_bytecode_guard.loader_hooks import macro_dependencies as macro_dependencies
from doeff_hy_bytecode_guard.loader_hooks import record_from_rows as record_from_rows
from doeff_hy_bytecode_guard.loader_hooks import record_is_current_here as record_is_current_here
from doeff_hy_bytecode_guard.loader_hooks import (
    source_to_code_as_import as source_to_code_as_import,
)

TYPE_CHECKING = False  # typing と records を起動時に読まない — 型検査器はこの名の分岐を真として読む

if TYPE_CHECKING:
    # 記録の型(注記だけに使う公開の名 — 値は current_record・record_from_rows が作る)。
    from doeff_hy_bytecode_guard.records import MacroDependency as MacroDependency
    from doeff_hy_bytecode_guard.records import MacroRecord as MacroRecord
