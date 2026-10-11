# 盲検 A・B に共通で渡す材料(設計検証 lt-D9C8NM324RSPKZZVKC9MHA9464)

## 対象

doeff の `packages/doeff-hy` の Hy の bytecode の見張り(`doeff_hy_bytecode_guard`)。Hy の module を compile する時、展開が使った
macro の閉包の digest を記録として .pyc の code の定数に載せ、次に .pyc を読む時に今の環境で digest を作り直して照らし、違えば
compile し直す。

読む木(読み取りだけ): `/home/ubuntu/.worktrees/doeff-wt-hy-env-design-check`(doeff main。対象の版 38629119b5729e7ec310b32c579c740f59ee6fb5
を含む)。読んでよい所:

- `packages/doeff-hy/src/doeff_hy_bytecode_guard/macro_use.py`(歩み・照らし — 「環境変数の読み」は module の docstring と
  `_reads_environment`・`_environment_module`・`_environment_object`・`_Walker._note_environment` と、その呼び手
  `_Walker.function`・`_Walker._lazy_module`・`_Walker._state_value`・`_Walker._bound`・`_remembered`・`_row_check`)
- `packages/doeff-hy/src/doeff_hy_bytecode_guard/records.py`(記録の形・印 `ENVIRONMENT_READ`)
- `packages/doeff-hy/src/doeff_hy_bytecode_guard/bound_source.py`(束縛の文の読み)
- `packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py`(compile と .pyc の読みの口)
- `packages/doeff-hy/src/doeff_hy_bytecode_guard/import_scope.py`
- `packages/doeff-hy/architecture.hy`(不変条件 B1 の条の文)
- `packages/doeff-hy/tests/test_macro_use_record.py`(特に末尾の節「環境変数で決まる値を読む macro」と、そこで使う `_scope_tree`・
  `SCOPE_CHILD`)

読まない所: `docs/design-checks/` の下(別の検証の記録と、この検証の記録)。

## 要件(固定)

1. 不変条件 B1(architecture.hy): 記録の digest が依った物(macro を定義した file の中身・macro が辿る値の module の file の中身・
   環境変数など file の外で決まる値)のどれかが記録を作った時と違えば、照らしは古いと判じて展開し直させる。
2. 環境変数から組んだ値を macro が読む時、環境変数だけを替えた新しい process は使い手の .pyc を古いと判じ、新しい値で展開し直す。
3. 今の本物の macro(doeff_hy などの閉包)の使い手を、照らせない形にしない(.pyc を使えなくする費用を今の利用者に出さない)。

## 利用の状況

- 照らしは venv の Python の起動の時点で `.pth` から入り、Hy の module の .pyc を読むたびに走る。開発の作業木は数十・worker は
  cluster の Pod。共有の code の置き場(環境変数 `DOEFF_HY_CODE_STORE`)を作業木・process がまたいで使う。
- Python 3.14 free-threaded・Hy 1.x。

## 責務と公開の契約

| id | 責務 | 公開の口 | 不変条件 |
| --- | --- | --- | --- |
| W(macro_use の歩み) | macro の閉包の digest を作る。何を digest に入れ、どの読みを照らせない読み(環境変数)と数えるかを決める | `macro_digest`・`value_digest` → `ClosureDigest(digest, modules)`。digest = 16 進の sha256・None(import の途中 — 比べずに file の行で比べる)・印 `records.ENVIRONMENT_READ`(環境変数の読み) | 環境変数の読みに当たった歩みの digest は印になり、process の中でも覚えない |
| C(macro_use の照らし) | 記録の行と今の digest を照らして、合う / 古いを答える | `record_is_current(record, hy_version, sha256_of) -> bool` | 記録か今の digest のどちらかが印なら古い |
| R(records) | 記録の形(.pyc の code の定数・JSON の境界)と印の定義点 | `MacroRecord`・`UsedMacro`・`UsedValue`・`ENVIRONMENT_READ` | 印は 16 進の sha256 とも None とも一致しない。記録の形の印 `RECORD_TAG` は変えない |
| B(bound_source) | 大域 1 つを束縛し書き換える source の文を読む(中身で決まる純な読み) | `binding_of(path, name)` | 生きた値を覚えない |
| L(loader_hooks) | compile の時に記録を作り、.pyc を読む時に照らし、古ければ compile し直す | `record_is_current_here` など | 照らしが偽なら compile し直す |

「環境変数の読み」と数える物(W): code か束縛の文の名の並びに `environ`・`environb`・`getenv`・`getenvb` が在り、並びの名のどれかが
大域で os(posix・nt)の module を指す・関数の中の import の先が os で、名の並びにそれらの名が在る・歩みが値として
`os.environ`・`os.environb`・`os.getenv`・`os.getenvb`・posix(nt)の `environ` に当たる。素の値の大域(文字列・数)は値で
digest に入るので、この規則に当たらない。

契約の外(見ない読みと宣言した物): 標準 library の関数が中で読む環境変数(`os.path.expanduser`・`tempfile` など)・文字列で引く
読み(`getattr(os, "environ")`)・module の表を通す読み(`sys.modules["os"]`)・unchecked-hash の .pyc(image の組み立て)。

## 将来の変更についての主張

| id | 軸 | 変更要求 | 主張 | 予想の範囲 |
| --- | --- | --- | --- | --- |
| S1 | effects | 環境変数の他の process の状態の読み(`sys.argv`・`os.getcwd()`・`os.path.expanduser` など)も照らせない読みと数える | 「何を照らせない読みと数えるか」は W の検出の関数と定数だけが持つ。C・R・L・B は変わらない | W の検出の関数と定数・検 |
| S2 | effects | 環境変数から組んだ表を、照らせない形でなく値で照らす(process ごとの展開し直しをなくす) | W の束縛の文の扱い(`_bound`)だけが変わる。C・R・L・B は変わらない | W・検 |
| S3 | storage | 記録の境界(.pyc・JSON・共有の置き場)を前の版と次の版が読み合う | 印は digest の欄(str)に入るので R の形と RECORD_TAG は変えない。前の版も次の版も印の行を古いと判じる | 変わらない |
| S4 | concurrency | 同じ process の中で os.environ を書き換えた後に照らす・thread をまたぐ照らし | 印の digest は process の中のどの覚えにも入らないので、毎回歩き直して印を出す | 変わらない |
| S5 | distribution | 共有の code の置き場を作業木・機体・環境の違う process がまたいで使う | 印の記録はいつも古いので、別の環境で作った展開を引かない | 変わらない |
| S6 | hardware | 対象外(照らしは機体の種類を読まない・機体ごとの環境変数は S5) | — | — |
| S7 | simulation | 対象外(照らしは時刻・乱数・外の service を読まない) | — | — |

## 実行してよい事(読み取りの木を変えない範囲)

- 自分の probe の file は `/tmp/blind-<A か B>/` の下だけに置く。木の file を書き換えない。git の操作をしない。
- probe は木の venv で走らせてよい: `cd /home/ubuntu/.worktrees/doeff-wt-hy-env-design-check && PYTHONPYCACHEPREFIX=/tmp/blind-<A か B>/pycache DOEFF_HY_CODE_STORE=/tmp/blind-<A か B>/store uv run --frozen python /tmp/blind-<A か B>/<名>.py`
  (検の `_scope_tree` と同じく、子の process で Hy の package を一時の dir に置き、1 つ目の process で .pyc を作り、環境を替えた
  2 つ目の process で読むと照らしを試せる)。
- 全数の検査・他の会話への連絡はしない。
