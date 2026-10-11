# doeff-hy-check の展開の保存を、置き場の path と入口の名の違いで外さない — 設計(盲検の前の主張)

依頼 lt-TBEF3PAMAVQ8B3Z07MDSCA994C・card ki-1475765a3f18(agora-redesign)。著者 c-XTC7RB24JH38PC0PET3XV4F1EJ(claude-opus-5-5)。

## この文書の目的

読んだ人が次の 3 つを判断できる状態にする。

1. 日次の hy-type-trend が保存した展開を毎回外していた訳と、直しが変えた責務の境界。
2. 将来の変更(置き場・入口・後処理・共有)が、どの責務の中で収まると主張するか。
3. その主張を、どの検査がどこまで機械で守り、どこからが守れていないか。

## 完了の範囲

実装完了まで。直しは doeff の main に入っている(L2100・基準の版 4ccab04ac の上の 4 commit、先端 15db080f3)。
日次に効かせる doeff の版の下限の上げは agora-controllers の main に入っている(L6196・5456ec34)。
日次での秒の受入(card の受入 c5df0dcbc)は、この文書の範囲の外(日次の回の観測)。

## 固定した要件

- R1: 日次の hy-type-trend(agora-controllers の repo 全体に `python -m doeff_hy.static_check --strict`)の秒を、自分の上限
  3,300 秒と全体の上限 4,800 秒に余裕をもって収める。
- R2: 速さの変更は、同じ負荷の前後の数を出してから。
- R3: 保存は速さのためだけの層。保存を引いた答えは、展開し直した答えと同じでなければならない(古い展開を今に合うと
  判じない)。

## 現状の障害(直す前 = 基準の版 4ccab04ac)

- F1(置き場の path): 閉包の歩み `_Walker.function` は、関数の code の名の表(co_names)の名を関数の大域で引いて値を
  digest に入れる。`doeff_hy.static_check.project` の `module.__file__ = str(source)`(作った module への書き)が名の表に
  `__file__` を出し、static_check の大域 `__file__`(doeff の置き場の絶対 path)が後処理 POST_PROCESSING の digest に入った。
  日次は回ごとに `/work/state/jobs/task_t<番号>/home/.local/state/merge-queue/land/worktrees/doeff` に doeff を置く。
- F2(入口の名): 記録は POST_PROCESSING を `value_reference(__name__, "POST_PROCESSING")` で名指した。`python -m` で撃つと
  この file は親で `__main__`、展開の子(forkserver)で `__mp_main__` の名で読まれる。照らし(`load` → `record_is_current_here`)は
  親で、展開は子で走るので、子の記録(`__mp_main__` の project を辿った digest)と親の照らし(`__main__` の project — 関数の
  持ち主の名と `__name__` の値が違う)が合わない。

## 直し(実装済み)

- D1(6a21fcd47): 歩みは `__file__`・`__cached__`(`_LOCATION_NAMES`)に、関数の大域の名・module の属性(文字列の定数の名の
  読みを含む `_attribute`)のどちらで出会っても、値ではなく印 `location` を入れる。前例 = `_TEXT_SKIP`(module の説明の文
  `__doc__` を文字列の定数の名の候補から外す)。
- D2(15db080f3): 記録は POST_PROCESSING を `_POST_PROCESSING_MODULE = __spec__.name if __spec__ is not None else __name__`
  (import で引ける名 — `-m` でも `doeff_hy.static_check`)で名指す。親も子もその名で import した同じ file を辿る。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| walker | 閉包の digest を作る(何を digest に入れるか) | code の読み・大域/属性/文字列の定数の辿りの規則・`_LOCATION_NAMES`・`_TEXT_SKIP` | 辿りの順・process の中の覚え `_REMEMBERED`・code の読みの置き場 | `macro_digest(key, macro, sha256_of)`・`value_digest(reference, sha256_of)` → `ClosureDigest(digest \| None, modules)` | module の import・file の sha256 の読み・code の読みの置き場の読み書き | 覚えは process の中だけ(辿った file の状態が変われば捨てる) | 同じ中身(code・値・file の sha256)なら置き場の dir に依らず同じ digest。中身が変われば変わる。import の途中に当たった digest は None |
| record | 展開が使った物の記録の形と、今の環境との照らし | MacroRecord の行(macro・also・files)・指紋・照らしの判定(digest か file か) | digest の作り方(walker へ任せる) | `macro_recording(module).record(text, also=…)`・`record_is_current_here(record)`・`value_reference(module, name)`・JSON の読み書き | 照らしの時に記録が名指す module を import | なし(記録は値) | 記録は path を持たず、module の名で今の環境から引き直す |
| post-processing | 1 つの .hy の型検査の展開と、その結果を変える後処理を記録に名指す | 展開の手順・POST_PROCESSING の範囲・名指す module の名 | 後処理の中身(記録には名と digest だけ) | `project(root, roots, source)` → `Projection \| CompileFailure` | source の読み・Hy の compile(macro の module の import) | 1 呼びごと | 名指す module は、親・子・console script のどの入口でも同じ名 |
| store | 展開の結果を、場所(source の中身・module 名・相対 path・形の版)と記録の指紋の entry で保存して引く | 場所の名・entry の JSON の形・CACHE_VERSION・書きの置き換え | dir の並び・一時 file の名 | `place`・`load` → `CachedProjection \| CacheMiss`・`store`・`default_cache_dir` | 保存先の file の読み書き | entry は消さない(消すのは置き場の持ち主) | 場所の名に絶対 path を入れない。記録が今に合う entry だけを返す |
| expanders | 段ごとに保存を引き、引けない物を子で展開して保存する | 段の進め方・子の数・pool の寿命・保存の書きの 1 か所(親) | pool(forkserver)・子の起こし方 | `project_closure(root, roots, targets, cache_dir, limit)` → `Closure` | 子 process の起動・保存先の読み書き(親だけ) | pool は 1 回の実行の中 | 照らしは親・展開は親か子 — どちらが展開しても同じ記録 |
| daily-stage | 日次の処理ステージとして repo 全体に当て、前の回と比べる(agora-controllers) | 命令(`python -m doeff_hy.static_check`)・時間の上限・保存先の dir の設定(DOEFF_HY_CHECK_CACHE)・doeff の版の下限 doeff-min-commit | doeff の置き場(task ごとの作業木) | `hy scripts/hy_type_trend.hy --timeout-s N` | 子 process・結果の file | 1 回の日次 | doeff の版は doeff-min-commit ちょうど・保存先は回をまたいで残る |

## 変更シナリオと事前の主張

| id | 軸 | 変更 | 主張 | 予想する変更範囲 | 変えない責務 |
|---|---|---|---|---|---|
| S1 | storage | doeff の checkout の置き場を変える(回ごとの task の dir・別の根の下)、または保存先の dir を替える | 同じ中身の doeff なら置き場が変わっても保存を引く。置き場の変更は daily-stage の設定だけで済む | daily-stage(置き場・保存先の設定)。walker・record・store・post-processing・expanders は変えない | walker・record・store・post-processing・expanders |
| S2 | concurrency | 入口を `python -m` / console script の間で替える・展開の子の数を替える(1・4・8) | どの入口・どの子の数でも同じ記録を書き、次の実行の親が引く。替えるのは daily-stage の命令か env だけ | daily-stage | walker・record・store・post-processing・expanders |
| S3 | effects | 型検査の展開の後処理(project が呼ぶ関数)を足す・変える | walker が project の閉包を辿るので記録が古くなり、展開し直す。保存・照らし・並べ方は変えない | post-processing | walker・record・store・expanders・daily-stage |
| S4 | distribution | 複数の機体の worker(別の置き場の doeff)が 1 つの保存先を共有する | 中身が同じなら、ある機体が書いた entry を別の機体が引く。中身が違えば引かない。共有は store の既存の書き(書き手ごとの一時 file の置き換え)で足りる | daily-stage(保存先を共有の場所へ向ける) | walker・record・store・post-processing・expanders |
| S5 | hardware | 対象外 | digest と記録に機体に依る入力が無い。CPU の数は展開の子の数の上限だけに効く(S2) | — | — |
| S6 | simulation | 対象外 | 決定的な模擬(agora_sim)は doeff-hy-check の保存を使わない。保存は答えを変えない速さの層(R3) | — | — |

主張の前提:

- P1: 同じ「中身」= 同じ commit の doeff(code・値・file の sha256 が同じ)。Hy・Python の版が同じ。
- P2: 展開の結果(text・位置・所見)に doeff の置き場の絶対 path が入らない(場所の名は相対 path で決まる)。
- P3: 照らしの歩みが辿る値のうち、置き場で変わる値は module の `__file__`・`__cached__` だけ。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| walker: 置き場に依らない digest | 契約の検(別の木に同じ中身を写して照らす) | `packages/doeff-hy/tests/test_macro_use_record.py::test_a_macro_naming_the_place_of_its_module_is_current_in_another_tree`・`…_changed_macro_naming_the_place_of_its_module_is_stale_in_another_tree` | doeff の pytest(doeff-hy の検) | `__file__`・`__cached__` 以外の、置き場から作った素の値(例 module の大域の文字列 `HERE = os.path.dirname(__file__)`)は名で外せない — P3 の前提 |
| store + post-processing: 同じ中身の doeff を別の dir に置いて引く | 契約の検(doeff_hy を別の dir に写し、同じ保存先で 2 度目に展開 0) | `packages/doeff-hy/tests/test_static_cache.py::test_the_same_doeff_hy_in_another_place_keeps_the_stored_expansion` | 同上 | 検の probe が使う macro(defk)と POST_PROCESSING の閉包だけを測る。ほかの macro の閉包の漏れは測らない |
| post-processing + expanders: 入口の名に依らない記録 | 契約の検(static_check を `__main__` の名で 2 度撃ち、2 度目に全部引く) | `packages/doeff-hy/tests/test_static_check_parallel.py::test_the_entry_run_as_main_reads_the_store_its_expansion_children_wrote` | 同上 | console script の入口は測らない(`__name__` = import の名なので前から同じ) |
| post-processing: 後処理を変えたら展開し直す | 契約の検 | `test_static_cache.py::test_a_changed_type_check_post_processing_expands_again` | 同上 | — |
| 型・import の層 | 既存の pyright・semgrep・doeff-linter(doeff の commit の hook・日次) | — | commit の hook | 「digest に何を入れるか」は型で表せない(値の意味の契約)— 検で補う |

## 予想する変更範囲(直しそのもの)

- F1 の直し: walker(`_LOCATION_NAMES` と 2 か所の分岐)だけ。record・store・expanders・post-processing は変えない。
- F2 の直し: post-processing(名指す module の名の 1 つの定数)だけ。walker・record・store・expanders は変えない。
