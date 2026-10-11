# 盲検の材料 — doeff-hy-check の展開の保存の照らし(doeff 4ccab04ac → 15db080f3)

## 1. 固定した要件と利用状況

- doeff-hy-check(`doeff_hy.static_check`)は Hy の source を本物の macro で展開し、pyright で型を検める。依存の .hy の展開は
  保存先(`--cache-dir` か環境変数 DOEFF_HY_CHECK_CACHE)に保存し、次の実行は「展開が依った物が今の環境と同じ」entry だけを引く。
- 利用状況: agora-controllers の日次の処理ステージ hy-type-trend が、repo 全体(.hy 約 2,450 本)に
  `python -m doeff_hy.static_check --strict` を撃つ(展開の子の数は環境変数 DOEFF_HY_CHECK_EXPANDERS・既定 4)。日次は回ごとに
  別の dir(`/work/state/jobs/task_t<番号>/home/.local/state/merge-queue/land/worktrees/doeff`)に doeff を置き、保存先
  (`/work/state/doeff-hy-check-cache`)は回をまたいで残る。doeff の版は agora-controllers の pyproject.toml の doeff-min-commit。
- R1: 日次の秒を自分の上限 3,300 秒と全体の上限 4,800 秒に余裕をもって収める。
- R2: 速さの変更は同じ負荷の前後の数を出してから。
- R3: 保存は速さのためだけの層。引いた答えは展開し直した答えと同じでなければならない(古い展開を今に合うと判じない)。

## 2. 直す前の障害と直し

- F1(置き場の path): 閉包の歩み `_Walker.function` は関数の code の名の表(co_names)の名を関数の大域で引いて値を digest に
  入れる。`doeff_hy.static_check.project` の `module.__file__ = str(source)` が名の表に `__file__` を出し、static_check の大域
  `__file__`(doeff の置き場の絶対 path)が後処理 POST_PROCESSING の digest に入った。
- F2(入口の名): 記録は POST_PROCESSING を `value_reference(__name__, "POST_PROCESSING")` で名指した。`python -m` ではこの file が
  親で `__main__`、展開の子(forkserver)で `__mp_main__` の名で読まれ、照らし(親)と記録(子)が別の名の project を辿った。
- D1: 歩みは `__file__`・`__cached__`(`_LOCATION_NAMES`)に、関数の大域の名・module の属性(文字列の定数の名の読みを含む)の
  どちらで出会っても、値ではなく印 `location` を入れる(前例 `_TEXT_SKIP` の `__doc__`)。
- D2: 記録は POST_PROCESSING を `_POST_PROCESSING_MODULE = __spec__.name if __spec__ is not None else __name__` で名指す。

## 3. 責務(module)と公開の形

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| walker | 閉包の digest を作る | code の読み・大域/属性/文字列の定数の辿りの規則・`_LOCATION_NAMES`・`_TEXT_SKIP` | 辿りの順・process の中の覚え・code の読みの置き場 | `macro_digest`・`value_digest` → `ClosureDigest(digest \| None, modules)` | module の import・file の sha256・code の読みの置き場 | 覚えは process の中だけ | 同じ中身なら置き場の dir に依らず同じ digest。中身が変われば変わる。import の途中は None |
| record | 記録の形と今の環境との照らし | MacroRecord の行(macro・also・files)・指紋・照らしの判定 | digest の作り方 | `macro_recording(module).record(text, also=…)`・`record_is_current_here`・`value_reference(module, name)` | 照らしの時に記録が名指す module を import | なし | 記録は path を持たず、module の名で今の環境から引き直す |
| post-processing | 1 つの .hy の型検査の展開と、後処理を記録に名指す | 展開の手順・POST_PROCESSING の範囲・名指す module の名 | 後処理の中身 | `project(root, roots, source)` → `Projection \| CompileFailure` | source の読み・Hy の compile | 1 呼びごと | 名指す module はどの入口でも同じ名 |
| store | 場所(source の中身・module 名・相対 path・形の版)と記録の指紋の entry で保存して引く | 場所の名・entry の形・CACHE_VERSION | dir の並び・一時 file の名 | `place`・`load` → `CachedProjection \| CacheMiss`・`store` | 保存先の file の読み書き | entry は消さない | 場所の名に絶対 path を入れない。記録が今に合う entry だけを返す |
| expanders | 段ごとに保存を引き、引けない物を子で展開して保存する | 段の進め方・子の数・pool の寿命・保存の書きの 1 か所(親) | pool・子の起こし方 | `project_closure(root, roots, targets, cache_dir, limit)` → `Closure` | 子 process の起動・保存先の読み書き(親だけ) | pool は 1 回の実行の中 | 照らしは親・展開は親か子 — どちらが展開しても同じ記録 |
| daily-stage | 日次の処理ステージ(agora-controllers — 材料の外) | 命令・時間の上限・保存先の設定・doeff の版の下限 | doeff の置き場 | `hy scripts/hy_type_trend.hy --timeout-s N` | 子 process・結果の file | 1 回の日次 | doeff の版は下限ちょうど・保存先は回をまたいで残る |

## 4. 変更シナリオと主張(前提つき)

| id | 軸 | 変更 | 主張 | 予想する変更範囲 | 変えない責務 |
|---|---|---|---|---|---|
| S1 | storage | doeff の checkout の置き場を変える、または保存先の dir を替える | 同じ中身の doeff なら置き場が変わっても保存を引く。daily-stage の設定だけで済む | daily-stage | walker・record・store・post-processing・expanders |
| S2 | concurrency | 入口を `python -m` / console script の間で替える・展開の子の数を替える(1・4・8) | どの入口・子の数でも同じ記録を書き、次の実行の親が引く | daily-stage | walker・record・store・post-processing・expanders |
| S3 | effects | 型検査の展開の後処理(project が呼ぶ関数)を足す・変える | 記録が古くなり展開し直す。保存・照らし・並べ方は変えない | post-processing | walker・record・store・expanders・daily-stage |
| S4 | distribution | 複数の機体の worker(別の置き場の doeff)が 1 つの保存先を共有する | 中身が同じなら他の機体の entry を引き、違えば引かない。store の既存の書きで足りる | daily-stage | walker・record・store・post-processing・expanders |
| S5 | hardware | 対象外 | digest と記録に機体に依る入力が無い(CPU の数は子の数だけ — S2) | — | — |
| S6 | simulation | 対象外 | 決定的な模擬は doeff-hy-check の保存を使わない | — | — |

前提: P1 同じ中身 = 同じ commit の doeff・同じ Hy と Python の版。P2 展開の結果(text・位置・所見)に doeff の置き場の絶対 path が
入らない。P3 照らしの歩みが辿る値のうち、置き場で変わる値は module の `__file__`・`__cached__` だけ。

## 5. 強制の手段(検査)

| 守る物 | 検査 | 実行経路 |
|---|---|---|
| walker が置き場に依らない | `packages/doeff-hy/tests/test_macro_use_record.py::test_a_macro_naming_the_place_of_its_module_is_current_in_another_tree`・`::test_a_changed_macro_naming_the_place_of_its_module_is_stale_in_another_tree` | doeff の pytest |
| 同じ中身の doeff_hy を別の dir に置いて引く | `packages/doeff-hy/tests/test_static_cache.py::test_the_same_doeff_hy_in_another_place_keeps_the_stored_expansion` | 同上 |
| `__main__` の名の入口でも子の記録を親が引く | `packages/doeff-hy/tests/test_static_check_parallel.py::test_the_entry_run_as_main_reads_the_store_its_expansion_children_wrote` | 同上 |
| 後処理を変えたら展開し直す | `packages/doeff-hy/tests/test_static_cache.py::test_a_changed_type_check_post_processing_expands_again` | 同上 |
| 型・import の層 | 既存の pyright・semgrep・doeff-linter | commit の hook・日次 |

## 6. 読む物と実験の制限

- 実コード(読むだけ・変えない): `/work/toolset/worktrees/doeff-wt-design-check-ki1475`(doeff 15db080f3 の作業木)。主な file:
  `packages/doeff-hy/src/doeff_hy_bytecode_guard/macro_use.py`(`_Walker`・`_LOCATION_NAMES`・`_TEXT_SKIP`)・`loader_hooks.py`・
  `records.py`・`packages/doeff-hy/src/doeff_hy/static_check.py`(`project`・`POST_PROCESSING`・`_POST_PROCESSING_MODULE`・
  `project_closure`・`_Expanders`)・`static_cache.py`・上の検の file。
- 差分: 同じ dir の `change.patch`(`git diff 4ccab04ac 15db080f3 -- packages/doeff-hy`)。
- 実験は任意。する時は `/tmp/ki1475-blind-<A か B>/` の下だけに file を置く。doeff_hy を変えて試す時は、その dir へ写した物を
  `PYTHONPATH` で先に読ませる(検 `test_static_cache.py` の `copied_doeff_hy` と同じ形)。python は
  `uv run --frozen --no-sync --project /work/toolset/worktrees/doeff-wt-design-check-ki1475 python …`、bytecode は
  `PYTHONPYCACHEPREFIX=/tmp/ki1475-blind-<A か B>/pyc`。pytest を撃つなら 1 本ずつ名指す(file の全部・全数は撃たない)。
- 共有の source・作業木を変えない。git の作業木を作らない。network・他の会話への連絡をしない。
