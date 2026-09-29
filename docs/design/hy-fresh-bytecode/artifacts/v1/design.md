# macro が変わった時に古い bytecode を使わない仕組みを doeff の側で持つ(agora-redesign #1292)

- 親: agora-redesign #1290(Hy のフォークを段階的に無くす)
- 利用者の決定(2026-09-29 14:0x JST・逐語): "I see, then lets introduce such bypasses and not touch hy at all."
  — Hy には一切手を入れない。回避は doeff の側に置く。
- 書いた日: 2026-09-29。対象の版: doeff `origin/main` f56579612 から分けた branch `wt/hy-fresh-bytecode`。

## 0. 全体図(今 ⇒ 目標)

```
今(フォークの Hy が判定を持つ)
  import pkg.user
    └─ hy/importer.py(proboscis/hy のフォーク)
         get_code: .pyc の隣の pkg/__pycache__/user.cpython-314.hydeps(macro の file と sha256)を読んで照合
         source_to_code: Hy の compile + .hydeps を書く
    └─ doeff-adr lazy_collection.py → hy.importer.read_valid_records(フォークの _bytecode_verdict)[#1291 が置き換え]

目標(PyPI の Hy のまま・判定は doeff が持つ)
  Python の起動
    └─ site-packages/doeff_hy_bytecode_guard.pth ─→ doeff_hy_bytecode_guard.install()
  import pkg.user
    └─ importlib.machinery.SourceFileLoader.get_code        ← doeff_hy_bytecode_guard/loader_hooks.py の包み
         .pyc の code の定数の末尾の記録(macro の file と sha256)を照合 → 古ければ compile し直して書き直す
    └─ importlib.machinery.SourceFileLoader.source_to_code  ← 同じ module の包み
         1 つ前 = hy/importer.py(PyPI の Hy)の Hy の compile → 記録を code の定数の末尾に足す
    (.pyc は 1 file・標準の形。隣の file は作らない)
```

## 1. 問題

Hy の module は macro を展開してから bytecode(.pyc)になる。Python の .pyc の有効判定は、その module の
source の更新時刻と大きさ(または source の hash)しか見ない。macro の定義(doeff-hy の `macros.hy` など)を
変えても、macro を使う側の .pyc は有効とされ、古い展開のまま動く(#1003 の赤 — 共有の checkout を editable で
読む環境で、doeff-hy の macro の変更の後に 13 file 以上が古い展開のまま動いていた)。

フォーク(proboscis/hy)は .pyc の隣に記録の file(`.hydeps`: macro の提供元の file と hash)を書き、
Hy の importer の中の判定(`_bytecode_verdict`)で読み直していた。フォークをやめるので、同じ正しさを
Hy の外(doeff)で出す。

PyPI の Hy 1.3.1 の上で #1003 の形を再現した(`packages/doeff-hy/tests/test_bytecode_guard.py::test_pypi_hy_alone_keeps_the_old_expansion`):
macro を `(+ (base) 1)` から `(+ (base) 2)` に変えても、使う側の値は 11 のまま。

## 2. 求めること

| 番号 | 求めること | 出所 |
|---|---|---|
| R1 | macro の提供元(と、macro が展開の時に呼ぶ同じ package の補助)を変えたら、使う側の module は新しい展開で動く | #1292 本文・#1003 |
| R2 | Hy に手を入れない。Hy の公開されていない名前を import しない | 利用者の決定(#1290) |
| R3 | pytest・本番の入口・image の組み立て・editor の索引のどこから Hy の module を読んでも効く | #1292 の指示(比べる軸) |
| R4 | image の組み立ての bytecode の道具(agora-controllers の `deploy/bytecode.py`)が扱う .pyc は標準の形だけ | #1292 の受入 |
| R5 | macro が変わった時に作り直すのは、その macro に依る module だけ | #1292 の指示(速さの軸) |
| R6 | 起動の費えを増やさない(venv の全ての Python の起動に乗る部分は 1〜2 ms 程度に抑える) | 観測(全 process に乗るため) |

## 3. 候補の比較

| 軸 | a. bytecode の置き場を macro の hash で分ける(`PYTHONPYCACHEPREFIX`) | b. 標準の loader の 2 つの口を包み、記録を .pyc の中に置く(**採用**) | c. b と同じ包みで、記録は .pyc の隣の file(フォークの `.hydeps` の移植) |
|---|---|---|---|
| 正しさ | 置き場の名にする hash の対象(どの file が macro の提供元か)を**起動の前に**静的に決める必要がある。全ての Hy の file を入れると正しいが、次の「速さ」が壊れる。少なく選ぶと取りこぼす | module ごとに、実際に require した macro の提供元を compile の時に記録し、読む時に照合する。取りこぼしはフォークと同じ範囲(4 節の限界) | b と同じ |
| 速さ(作り直す量) | hash が変われば**全部**(標準 library と依存の .py も含め数千 file)を作り直す。古い置き場が溜まり続ける | その macro に依る module だけ | b と同じ |
| 置く場所 | 環境変数は process の起動の前に要る(pytest・本番・索引・子 process の全部に配る)。起動の後に `sys.pycache_prefix` を変えると、既に読んだ module と食い違う | venv の起動時の `.pth` 1 行 + `import doeff_hy`(2 つ目)。起動の形を問わない | b と同じ |
| Hy の内部への依存 | 無い | Hy が module の名前空間に置く macro の表の名(`_hy_macros`・`_hy_reader_macros`)と `hy.__version__` を**読むだけ**。import しない | b と同じ |
| image の bytecode | image の組み立ては置き場を木の中に固定している(#732 で `PYTHONPYCACHEPREFIX` の継承を外した)。置き場を分けると道具の前提と衝突する | .pyc は標準の形のまま(記録は code の定数の 1 つ)。道具の綴り直しは値を変えないので記録も残る | 隣の file が増える。道具は .pyc しか運ばない・検めないので、image では記録が落ちるか、道具に新しい種類の file を教える必要がある |
| 既存の検査との関係 | root の `conftest.py` が `sys.pycache_prefix` を None に固定している(#1110 付近の R2)。固定と衝突する | 衝突しない | 衝突しない |

採った案 = **b**。理由:

1. 正しさと速さを両立するのは、module ごとに実際の依存を記録する形だけ(a は静的に決める依存の範囲と作り直す量が引き換えになる)。
2. 記録を .pyc の中に置けば、.pyc は 1 つの file のまま標準の形で、image の道具・Python の読み込み・他の道具が何も知らずに扱える(c は file の種類が増える)。
3. 包むのは Python 標準の `SourceFileLoader` の口で、Hy の関数を置き換えない。本家の Hy 1.3.1 は `source_to_code` だけを置き換え、`get_code` には触れない。

## 4. 仕組み

```
起動時(.pth)                         import pkg.user(Hy の module)
  install() ──┐                         │
              ▼                         ▼
  SourceFileLoader.get_code ─────► 包み(読みの口)
                                    1. 1 つ前の get_code を呼ぶ(.pyc を読む か source から compile)
                                    2. 今 compile した → そのまま返す
                                    3. .pyc から読んだ → code の定数の末尾の記録を読み、
                                       記録の Hy の版と各 file の sha256 が今と合えば返す
                                    4. 合わない・記録が無い:
                                       .pyc が「Python が source と突き合わせない形」(unchecked-hash)なら信じて返す
                                       それ以外は source から compile し直し、同じ方式の .pyc を書き直して返す
  SourceFileLoader.source_to_code ► 包み(compile の口)
                                    1. 1 つ前の source_to_code(Hy が置き換えた物)で compile
                                    2. import の途中の module の名前空間の macro の表から、提供元の module と
                                       その file の sha256 を集め、code の定数の末尾に記録を 1 つ足す
```

記録の形(code の定数の末尾):

```
("doeff-hy/macro-dependencies/1", <Hy の版>, ((<module 名>, <file の path>, <sha256>), ...))
```

依存の辿り方(フォークの `_macro_dependencies` と同じ範囲): module が require した macro の提供元の module、
提供元が名前空間に持つ値(関数・class・module)が属する**同じ top package**の module(macro が展開の時に呼ぶ補助)、
提供元自身が require した macro の提供元。`hy.*` は記録の Hy の版で覆う。

### 責務の表

| 責務 | 持ち主 | 置き場 |
|---|---|---|
| macro の依存の一覧を求める(辿り方の唯一の定義元) | `doeff_hy_bytecode_guard.records.macro_provider_files`(純関数) | doeff-hy |
| file の sha256 を計算する(同じ process では更新時刻と大きさが同じ file を読み直さない) | `doeff_hy_bytecode_guard.loader_hooks.file_sha256` | doeff-hy |
| 記録を書く(code の定数へ) | compile の口の包み(`records.with_record`) | .pyc の中 |
| 記録を読んで照合する | 読みの口の包み(`records.record_of`・`records.record_is_current`) | — |
| 古いと判じた .pyc を無効にする(compile し直して同じ方式で書き直す) | 読みの口の包み(`records.pyc_bytes` + `set_data`) | 元の .pyc の置き場 |
| 包みを入れる | `doeff_hy_bytecode_guard.install`(何度呼んでも 1 度) | `.pth`(起動時)・`doeff_hy/__init__.py`(2 つ目) |
| 索引のキャッシュの鍵に同じ依存の一覧を使う(#1291) | `doeff_hy_bytecode_guard.macro_dependencies`(公開の口) | doeff-hy(#1291 の担当へ連絡済み) |

### 限界(フォークと同じ・意図して残す)

- macro が展開の時に**別の top package**の関数を呼び、その関数だけが変わった場合は見ない(フォークと同じ範囲。
  全 module を辿ると依存が標準 library まで広がる)。
- module の外での compile(`py_compile`・image の組み立ての `warm`・`runpy` の main)では、何を require したかが
  見えないので記録を足さない。記録の無い .pyc は、Python が source と突き合わせる形なら次の読みで作り直す。
- Python が source と突き合わせない .pyc(PEP 552 の unchecked-hash — image の形)は、Python が source の変更を
  信じないのと同じく macro の変更も信じない。image は組み立ての中で焼くので、組み立ての時点の macro で正しい。
- 同じ秒に同じ大きさで書き換えた file の .pyc は、提供元自身が Python の判定で古いまま有効とされる(Python の限界 — この仕組みの対象外)。

## 5. 置く場所ごとの効き方

| 置く場所 | 効き方 |
|---|---|
| pytest | venv の起動時の `.pth` で入る(doeff-hy-pytest の plugin より前) |
| 本番の入口 | 同じ(`.pth` は venv の全ての Python の起動で読まれる)。`python -S` などで `.pth` が読まれない時は `import doeff_hy` で入る |
| image の組み立て | `deploy/bytecode.py warm` は `source_to_code` を module の外で呼ぶので記録は足さず、unchecked-hash の .pyc を焼く。実行時は信じる(上の限界)。.pyc は標準の形なので `rehead-tree`・`check`・綴り直しは変更不要 |
| editor の索引 | doeff-indexer(Rust)は source を静的に読み、.pyc を使わない。影響なし |

起動の費え(実測・この機体): `.pth` からの import は累計 約 1.4 ms(typing・threading・hashlib は Hy の source に初めて当たる時まで読まない)。

## 6. 移行(フォークが入っている間)

フォークの Hy(adbe989a)が入っている間は、フォークの `.hydeps` の判定と、この包みの両方が効く(どちらも
compile し直す側にしか倒れないので、両方あっても正しさは変わらない)。フォークの `get_code` は起動時に入った
この包みを「1 つ前」として呼ぶ。Hy の指定を PyPI の版へ戻す変更(#1290 の手順 4)の後は、この包みだけが残る。
残った `.hydeps` の file は読む者が居なくなるだけで害は無い。

doeff-adr の遅延収集(`lazy_collection.py`)が使う `read_valid_records`(フォークの `_bytecode_verdict`)は、
索引を doeff へ移す子 #1291 が置き換える。この issue では触らない。

## 7. 検証

`packages/doeff-hy/tests/test_bytecode_guard.py`(PyPI の Hy 1.3.1 を `uv run --no-project --isolated --with hy==1.3.1` の
使い捨ての環境で使う 7 本 + venv の起動の 1 本):

| テスト | 確かめること |
|---|---|
| `test_pypi_hy_alone_keeps_the_old_expansion` | 包み無しでは #1003 の赤が出る(反例のテストが意味を持つことの確かめ) |
| `test_a_macro_change_recompiles_the_user_on_pypi_hy[before-hy/after-hy]` | macro を変えた後、使う側は新しい展開で動く(包みを Hy の前に入れても後に入れても) |
| `test_a_change_of_the_helper_a_macro_calls_recompiles_the_user` | macro が展開の時に呼ぶ補助の変更でも作り直す |
| `test_the_record_lives_inside_a_standard_pyc` | `__pycache__` には .pyc しか無く、記録は code の定数の中 |
| `test_a_checked_hash_pyc_is_recompiled_in_the_same_form` | checked-hash の .pyc も作り直し、同じ方式で書き直す |
| `test_an_unchecked_hash_pyc_is_trusted_like_python_trusts_it` | image の形(unchecked-hash)は信じる |
| `test_the_venv_installs_the_guard_at_startup_before_hy` | venv の起動の時点で(Hy の import の前に)包みが入っている |

## 8. 戻し方

この変更の revert 1 つ。包みを外すと、フォークが入っている間はフォークの `.hydeps` が同じ赤を防ぐ。
PyPI の Hy へ戻した後に revert すると #1003 の赤が戻る。
