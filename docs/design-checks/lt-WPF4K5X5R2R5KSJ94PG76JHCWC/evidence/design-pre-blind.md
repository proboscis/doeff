# 設計検証 lt-WPF4K5X5R2R5KSJ94PG76JHCWC — 盲検の前の主張(card ki-2fa4c02604b1)

盲検 A・B を起こす前に書いて固定した主張。盲検の後に書き換えない(結論の変化は design.md に書く)。

## 完了の範囲

実装完了まで。実装は doeff main fe951a108(L2162・2026-10-12 11:58 JST 着地)。この記録の基準の版も fe951a108。

## 受入の条(card ki-2fa4c02604b1・固定)

1. 3 つの依り(compiler.scope・hy.importer._could_be_hy_src・light_stack の当て物)を uv.lock の Hy の版で照合する検が doeff main に在り、uv.lock の変更で make test-changed に選ばれる。
2. 在らない名を 1 つ書いた失敗ケースで、その検が名を挙げて赤になる。

## 現状(fe951a108)

doeff-hy は import でない形で Hy の内部に依る:

| 依り | 在りか | Hy 1.3.1 の側 |
| --- | --- | --- |
| `hy.importer._could_be_hy_src` を差し替える | packages/doeff-hy/src/doeff_hy/__init__.py:39 | importer.py:118-126(`_hy_source_to_code` が大域の名で引く) |
| macro の第 1 引数 `_hy-compiler` で compiler を受ける | macros.hy・handle.hy の defmacro | macros.py:405(`co_varnames[:1] == ('_hy_compiler',)` の時だけ渡す) |
| compiler の `scope`・`this`・`filename` を読む | macros.hy:51・684・890・891 | compiler.py:345-357 |
| `inspect.stack` を frame 1 つの tuple の列に差し替える(hy.compiler・hy.macros から呼ばれた時だけ) | packages/doeff-hy/src/doeff_hy/light_stack.py | compiler.py:55・macros.py:153・179・203(どれも `[n][0]`) |
| `hy.compat.rewriting_unparse` を差し替える(在る時だけ) | packages/doeff-hy/src/doeff_hy/ast_unparse.py:84 | compat.py:15-40 |

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| reliance-population | doeff の source から依りを数える(scan-hy-reliances・python-assignment-reliances・hy-form-reliances・attribute-parts) | 数える source の形 3 つ・読む file の絞り込みの目印 | file の歩き方・目印の正規表現・reader と ast の使い分け | `(scan-hy-reliances repo-root) → [#(path 種類 対象 名)]` |
| pypi-hy-probe | 依りを PyPI の Hy の指定の版で測る(probe-reliances・pypi-hy-run) | uv の使い捨ての環境の作り方・compiler の受け取り方(公開の defmacro で第 1 引数 `_hy-compiler` の macro を定義して展開) | uv の引数・env の掃除・probe の Python の本文 | `(probe-reliances rows version) → {"missing" [...] "sources" {module source/None} "compiler-failure" str/None}` |
| reliance-judgment | 測った結果を名を挙げた違反の文にする(reliance-violations・stack-call-violations・frame-only-use) | 種類ごとの文・light_stack が受け入れる形 `inspect.stack(…)[n][0]` | ast の親の引き方 | 純関数。違反の文の列 |
| version-source | 照合の版を決める(locked-hy-version・R4 と共有) | uv.lock の hy の行の読み方・index から入れる版でなければ止める | toml の形 | `(locked-hy-version text) → str` |
| selection | いつ検を走らせるか(root の pyproject.toml の [[tool.doeff.contract-tests]] の uv.lock の行と scripts/run_changed_tests.py) | uv.lock を変えた便が払う検の組 | 選び方の実装 | 表の行 |
| doeff-hy-workarounds | Hy の振る舞いの回避(__init__.py・macros.hy・handle.hy・light_stack.py・ast_unparse.py) | 回避そのもの | — | doeff-hy の公開の macro と import の副作用 |

依りの行の「種類」は閉じた語彙 4 つ(replaced・compiler-parameter・compiler-attribute・light-stack)で、reliance-population・pypi-hy-probe・reliance-judgment の 3 つが共有する契約。

## 変更シナリオと事前の主張

| id | 軸 | 変える要求 | 変える module | 変えない module・契約 | 収まる理由 |
| --- | --- | --- | --- | --- | --- |
| S1 | effects | Hy を上げる(uv.lock の hy を新しい版へ・R6 の上限も同じ commit) | uv.lock・packages/doeff-hy/pyproject.toml。依りが壊れたら doeff-hy-workarounds | reliance-population・pypi-hy-probe・reliance-judgment・version-source・selection | 照合の版は uv.lock から読み、uv.lock の変更で selection が検を選ぶ。検の code に版の写しが無い |
| S2 | effects | doeff-hy に import でない新しい依りを足す(例: macro が compiler の別の属性を読む・hy.* の別の名を差し替える) | doeff-hy-workarounds の 1 file | reliance-population の code・台帳(enforcement-ledger.json)・selection | 依りは source の形から数え、写しの台帳を置かない |
| S3 | effects | light_stack が当てる Hy の module を足す・替える(`_HY_CALLER_MODULES`) | light_stack.py だけ | ADR の file・selection | ADR は `_HY_CALLER_MODULES` を doeff_hy.light_stack から読む |
| S4 | effects | 依りの新しい種類を測る(例: 別の当て物が Hy の内部の表を読む) | ADR の file の中の reliance-population・pypi-hy-probe・reliance-judgment(種類の語彙という共有の契約の拡張) | doeff-hy-workarounds・selection・version-source | 種類の語彙は 3 つの共有の契約で、足すのは契約の拡張。ADR の 1 file に閉じる |
| X-hardware | hardware | 不適用 | — | — | 検は source と PyPI の Hy の名を読むだけで、機体に依らない(Python の版は検を走らせる venv の major.minor) |
| X-storage | storage | 不適用 | — | — | 永続の保存を持たない。入力は作業木の file と uv.lock、出力は検の結果だけ |
| X-distribution | distribution | 不適用 | — | — | 検は 1 process で閉じ、機体の数に依らない。cluster の worker でも同じ作業木を読む |
| X-concurrency | concurrency | 不適用 | — | — | 検の状態は process の中だけ。uv の cache の並走は uv の錠が持つ |
| X-simulation | simulation | 不適用 | — | — | 決定的な模擬の対象の振る舞いを持たない(検そのものが Hy の版上げの模擬) |

## 強制の方法

| 守る事 | 強制の方法 | 実装の在りか | 呼ばれる経路 | 限界 |
| --- | --- | --- | --- | --- |
| 依りが uv.lock の版の PyPI の Hy に在る | deftest `test_adr_doe_hy_008_internal_reliances_exist_in_pypi_hy` | docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy | uv.lock の変更 → make test-changed(契約の組)・日次の全体の回 | 着地の門は検を走らせない(登記の前の make test-changed は約束) |
| 数え方が壊れて 0 件にならない(R4 の 0 件の再来を防ぐ) | 本物の Hy の reader と ast で読む反例の assert | 同じ file の `test_adr_doe_hy_008_judgments_reject_the_counterexamples` | ADR の file の変更・日次 | 数える形の外は数えない |
| 在らない名を名指す | 在らない名・属性・module を probe に渡す deftest | `test_adr_doe_hy_008_reliance_probe_names_what_pypi_hy_lacks` | ADR の file の変更・日次 | — |
| 検と台帳が揃う | enforcement-ledger.json | docs/adr/enforcement-ledger.json・scripts/check_enforcement_ledger.py | commit の hook・日次 | — |

## 限界(盲検の前から分かっている物)

- 数える形の外(別名で受けた module への代入・`setattr` の文字列・`getattr` の文字列の名・compiler を 3 つの名 `compiler`・`_hy-compiler`・`_hy_compiler` 以外で受けた読み)は数えない。
- 名が在る事を測り、意味(scope の型・this の中身)までは測らない。
- doeff_hy_bytecode_guard の Hy の内部への依り(`_hy_macros` の表の引き方など)は数えない(card の範囲の外として依頼者へ報告した)。
