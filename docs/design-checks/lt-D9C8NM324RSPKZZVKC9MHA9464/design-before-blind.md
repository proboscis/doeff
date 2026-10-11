# 設計検証 lt-D9C8NM324RSPKZZVKC9MHA9464 — 盲検の前の主張(card ki-799eb0e54c56)

完了の範囲: **実装完了まで**。順の申告: この依頼(class investigate)では、直しを先に実装して着地し(L2095・main 38629119)、
その後で設計検証の手順を始めた。この file は実装の後・盲検 A・B を起こす前(2026-10-11T18:2xZ)に書いた主張で、盲検の返答を
受けて書き換えない。盲検の後の結論は design.md に書く。

## 版

| 版 | 中身 |
| --- | --- |
| 4ca9fcd502928a0ffcde455f390f66bd629144b9 | 基準版(直す前の main) |
| eb83e2f52(branch wt/hy-env-dict-stale) | 失敗ケースだけの commit(赤) |
| d793b6fde(同) | 直しの commit |
| 38629119b5729e7ec310b32c579c740f59ee6fb5 | 着地した版(L2095 — 上の 2 commit を main へ並べ直した物) |

## 要件(固定)

1. 不変条件 B1(packages/doeff-hy/architecture.hy): 記録の digest が依った物(macro を定義した file の中身・macro が辿る値の
   module の file の中身・**環境変数など file の外で決まる値**)のどれかが記録を作った時と違えば、照らしは古いと判じて展開し直させる。
2. 依頼の受入(card ki-799eb0e54c56): 付録 3 の probe の形(環境変数から組んだ dict を macro が読む)を写した検が、直す前に赤・
   直した後に緑。素の文字列の形の既存の検と、照らしの回数(照らし・歩み・stat)が直す前と同じ事を card に書く。
3. 今の本物の macro(doeff_hy などの閉包)の使い手を、照らせない形にしない(.pyc を使えなくする費用を今の利用者に出さない)。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effects | 寿命 | 不変条件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W(macro_use の歩み — `_Walker`・`_remembered`・検出の関数 `_reads_environment`・`_environment_module`・`_environment_object`・`_ENVIRONMENT_MODULES`・`_ENVIRONMENT_NAMES`) | macro の閉包の digest を作る。何を digest に入れ、どの読みを照らせない読み(環境変数)と数えるかを決める | 値の種類ごとの入れ方・環境変数の読みの形 | 値の入れ方と検出の規則(呼び手は ClosureDigest だけを見る) | `macro_digest`・`value_digest` → `ClosureDigest(digest: str \| None, modules)`。digest = 16 進の sha256・None(import の途中)・印 `ENVIRONMENT_READ`(環境変数の読み) | module の import(辿るため)・file の stat と sha256・code の読みの置き場の読み書き | 歩み 1 つ。digest は process の中の覚え `_REMEMBERED`(file の状態で確かめる)— 印と None の digest は覚えない | 環境変数の読みに当たった歩みの digest は印になり、覚えない |
| C(macro_use の照らし — `record_is_current`・`_rows_are_current`・`_row_check`) | 記録の行と今の digest を照らして、合う / 古いを答える | 行の照らしの結末(SAME・STALE・BY_FILES) | digest の作り方 | `record_is_current(record, hy_version, sha256_of) -> bool` | W を呼ぶ(import・stat)— 自分は書かない | 照らし 1 回 | 記録か今の digest のどちらかが印なら STALE(今の歩みが import の途中で file の行で比べる形になっても) |
| R(records — 記録の形と印の定義点) | .pyc の code の定数・JSON の境界の記録の形 | 記録の欄の形・印 `ENVIRONMENT_READ`・`RECORD_TAG` | marshal・JSON の綴り | `MacroRecord`・`UsedMacro`・`UsedValue`・`record_of`・`record_from_json` など・定数 `ENVIRONMENT_READ` | なし(純関数) | なし | 印は 16 進の sha256 とも None とも一致しない。記録の印(RECORD_TAG)は変えない |
| B(bound_source — 束縛の文の読み) | 大域 1 つを束縛し書き換える source の文を読む(中身で決まる純な読み・置き場に覚える) | 文の正準の形・文が読む名(loads)・属性の名(attributes) | 解き方(ast・Hy の読み) | `binding_of(path, name) -> Binding \| None` | file の読み・置き場の読み書き | process の覚えと置き場の entry | 読みは source の中身と名と読み方だけで決まる(生きた値を覚えない) |
| L(loader_hooks — compile と .pyc の読みの口) | 使い手を compile する時に記録を作り、.pyc を読む時に照らして古ければ compile し直す | 口の包み方・置き場への足し方 | — | `record_is_current_here`・`source_to_code_as_import` など | .pyc と置き場の書き | import 1 回 | 照らしが偽なら compile し直す |
| T(検 — tests/test_macro_use_record.py の環境変数の節) | 環境変数で決まる値を読む macro の形ごとに、環境変数を替えた子の process が展開し直す事を確かめる | 形の一覧 | — | pytest の検 | 子の process・一時 dir | 検 1 本 | — |

## 変更シナリオと主張(盲検の前)

| id | 軸 | 変更要求 | 主張(何が変わり、何が変わらないか・理由) | 予想の範囲 | 前提 |
| --- | --- | --- | --- | --- | --- |
| S1 | effects | 環境変数の他の process の状態の読み(例: `sys.argv`・`os.getcwd()`・標準 library の関数が中で読む環境変数 `os.path.expanduser`)も照らせない読みと数える | 「何を照らせない読みと数えるか」は W の検出の関数と定数だけが持つ。印の意味(照らせない)と照らしの答えは同じなので、C・R・L・B は変わらない | W の検出の関数と定数・T(形を足す) | 新しい読みが、code の名の並び・束縛の文の名の並び・値として当たる object のどれかで歩みに現れる |
| S2 | effects | 環境変数から組んだ表を照らせない形でなく値で照らす(使い手が process ごとに展開し直す費用をなくす) | W の束縛の文の扱い(`_bound`)が、文が直に環境変数を読む時に大域の値を入れる形に変わる。C・R・L は変わらない(digest が 16 進になるだけ)。B は変わらない(文の名の並びはもう読める) | W(`_bound` と値の入れ方)・T | 値が素の値の入れ物で、書き換える関数が無い |
| S3 | storage | 記録の境界(.pyc の code の定数・型検査の展開の保存の JSON・共有の code の置き場)を前の版と次の版が読み合う | 印は digest の欄(str)に入るので R の形と RECORD_TAG は変えない。前の版の照らしは印を今の digest と比べて古いと判じ、次の版は前の版の記録を今の歩みが印になるので古いと判じる — どちらも展開し直すだけ | 変わらない(R・L・C) | 前の版の照らしは digest を等しさで比べる |
| S4 | concurrency | 同じ process の中で os.environ を書き換えた後に照らす(長く走る process・検の monkeypatch)・thread をまたぐ照らし | 印の digest は process の中の覚え `_REMEMBERED` にも一番外の読み込みの覚えにも入らないので、照らしは毎回歩き直して印を出す — 変わる所なし | 変わらない | 環境変数の読みの検出が、毎回の歩みで同じに働く |
| S5 | distribution | 共有の code の置き場(DOEFF_HY_CODE_STORE)を作業木・機体・環境の違う process がまたいで使う | 印の記録は照らしでいつも古いので、別の環境で作った展開を引かない。code の読みの置き場の entry は code の中身で引く純な読み(環境変数に依らない)で、変える所なし | 変わらない | 置き場から引いた code も同じ照らしを通る(L の `_from_shared_store`) |
| S6 | hardware | — | 対象外: 照らしは機体の種類を読まない。機体ごとに違う環境変数は S5 が扱う | — | — |
| S7 | simulation | — | 対象外: 照らしは時刻・乱数・外の service を読まない。検は子の process の環境変数を monkeypatch で替えるだけで、決定的な再生の仕組みは要らない | — | — |

## 強制方法(実装後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| B1(環境変数で決まる値が違えば古い) | 失敗ケースの検 — 形 9 つ(文が組む dict・list・別名の environ・getenv・読み込みの時の補助の関数・Python の module の表・展開の時の直の読み・展開の時の補助の関数・関数の中の import)を子の process で環境変数を替えて照らす | `test_a_macro_reading_a_value_decided_by_the_environment_is_stale_when_it_changes[形]` | doeff-hy の pytest(日次の全体と着地の前の focused) | 意味の判断(どの読みが process の状態か)は静的に表せない。検の形の外の読みは縛らない |
| 照らせない形の広がりを狭く保つ(要件 3) | 環境変数を読まない macro と素の値の macro が照らせるままの検・本物の doeff_hy の macro の使い手が process をまたいで照らせる既存の検 | `test_macros_that_do_not_read_the_environment_stay_checkable_beside_ones_that_do`・RealTree の検 | 同上 | 本物の閉包の数え(印 0)は一度の実測で、検ではない |
| 記録の印は記録の側でも今の側でも古い | 行の照らしの検 | `test_a_row_of_an_environment_read_is_stale_even_when_the_walk_now_compares_files` | 同上 | — |
| 印の定義点は 1 つ | 定数 `records.ENVIRONMENT_READ` を W・C が import して使う(綴りを写さない) | records.py・macro_use.py | 人の読み | 写しを止める静的な検は無い |

## 見ない読み(契約の外 — docstring と B1 に書いた)

- 標準 library の関数が中で読む環境変数(`os.path.expanduser`・`tempfile` など)
- 文字列で引く読み(`getattr(os, "environ")`)
- module の表を通す読み(`sys.modules["os"]`)
- unchecked-hash の .pyc(image の組み立て)は前と同じく記録を照らさない
