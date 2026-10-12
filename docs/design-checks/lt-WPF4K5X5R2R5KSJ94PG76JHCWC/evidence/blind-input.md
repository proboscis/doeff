# 盲検への入力 — doeff の ADR-DOE-HY-008 R7(import でない形の Hy の内部への依りの照合)

## 対象の版と読む所

- repo: doeff。版: fe951a108(この作業木はその版で切ってある)。
- 読む file(この作業木の中。書き換えない):
  - `docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy` — 設計記録と検。R7 の部分は `scan-hy-reliances`・`python-assignment-reliances`・`hy-form-reliances`・`attribute-parts`・`probe-reliances`・`pypi-hy-run`・`reliance-violations`・`stack-call-violations`・`frame-only-use` と、deftest `test-adr-doe-hy-008-internal-reliances-exist-in-pypi-hy`・`test-adr-doe-hy-008-reliance-probe-names-what-pypi-hy-lacks`・`test-adr-doe-hy-008-judgments-reject-the-counterexamples`。
  - `pyproject.toml` の `[[tool.doeff.contract-tests]]` の `prefix = "uv.lock"` の行。
  - `scripts/run_changed_tests.py`(`make test-changed` の本体。`select_targets` が変えた file から検を選ぶ)。
  - doeff-hy の回避: `packages/doeff-hy/src/doeff_hy/__init__.py`・`macros.hy`・`handle.hy`・`light_stack.py`・`ast_unparse.py`、と doeff-hy の他の source。
  - 入っている Hy 1.3.1 の source: `.venv/lib/python3.14t/site-packages/hy/`。

## 要件(固定)

1. doeff-hy が import でない形で依る Hy の内部 — `hy.importer._could_be_hy_src` の差し替え・macro が受ける compiler の `scope` などの読み・light_stack の当て物 — を、uv.lock の Hy の版で照合する検が doeff の main に在り、uv.lock の変更で `make test-changed` に選ばれる。
2. 在らない名を 1 つ書いた失敗ケースで、その検が名を挙げて赤になる。

利用の状況: doeff の Hy の版は uv.lock が固定する(今 1.3.1)。Hy を上げる便は uv.lock(と doeff-hy の Hy の上限 = R6)を変え、登記の前に `make test-changed` を走らせる約束。着地の門そのものは検を走らせない。日次の全体の回は全部の検を走らせる。

## 責務と公開の契約

| 責務 | 持つ知識 | 公開の形 |
| --- | --- | --- |
| 依りを数える(reliance-population) | doeff の source の形から依りを数える: ① Python の `hy.X.Y = …`(hy.* の名への代入・差し替え)② Hy の第 1 引数を `_hy-compiler` にした defmacro(Hy が compiler を渡す約束)と、compiler を指す記号(`compiler`・`_hy-compiler`・`_hy_compiler`)の属性の読み `(. compiler 名)`(`compiler.名` も reader が同じ式に読む)③ doeff_hy.light_stack の `_HY_CALLER_MODULES` | `#(path 種類 対象 名)` の列。種類は replaced・compiler-parameter・compiler-attribute・light-stack の 4 つ |
| 依りを PyPI の Hy で測る(pypi-hy-probe) | uv の使い捨ての環境(指定の版の Hy だけ)で、replaced は名の在る事、compiler-parameter・compiler-attribute は公開の defmacro で定義した第 1 引数 `_hy-compiler` の macro を展開して受けた compiler とその属性、light-stack は module の source を返す | `{"missing" [...] "sources" {module source/None} "compiler-failure" str/None}` |
| 違反の文にする(reliance-judgment) | 種類ごとの文。light-stack は module の `inspect.stack` の使いがどれも `inspect.stack(…)[n][0]` で 1 つ以上在る事 | 名を挙げた違反の文の列(空 = 緑) |
| 版を決める(version-source) | uv.lock の hy の行を 1 つ読む。index から入れる版でなければ止める | 版の文字 |
| いつ走るか(selection) | uv.lock を変えた便は契約の組の 3 本(R4・R6・R7)を払う | pyproject.toml の表の行 |
| Hy の回避(doeff-hy-workarounds) | 回避そのもの | doeff-hy の macro と import の副作用 |

## 将来の変更についての主張(盲検の前に固定)

| id | 変える要求 | 変える所 | 変えない所 | 前提 |
| --- | --- | --- | --- | --- |
| S1 | Hy を上げる(uv.lock の hy を新しい版へ) | uv.lock・doeff-hy の Hy の上限。依りが壊れたら doeff-hy の回避 | 検の code(数える・測る・判定)・版を決める所・表 | 照合の版は uv.lock から読む。検の code に版の写しが無い |
| S2 | doeff-hy に import でない新しい依りを足す(macro が compiler の別の属性を読む・hy.* の別の名を差し替える) | その doeff-hy の source だけ | 数える code・enforcement-ledger.json・表 | 依りは source の形から数え、手で書く一覧を持たない |
| S3 | light_stack が当てる Hy の module を足す・替える | light_stack.py だけ | ADR の file・表 | ADR は `_HY_CALLER_MODULES` を module から読む |
| S4 | 依りの新しい種類を測る | ADR の file の中の数える・測る・判定の 3 か所(種類の語彙という共有の契約の拡張) | doeff-hy の回避・表・版を決める所 | 種類の語彙は 3 つが共有する契約 |

## 検の走らせ方(読み取りと、この作業木の中の一時の変更だけ)

```
uv run --no-sync pytest "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy::test_adr_doe_hy_008_internal_reliances_exist_in_pypi_hy" -q
uv run --no-sync pytest "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy::test_adr_doe_hy_008_judgments_reject_the_counterexamples" -q
```

機体の検の枠(8 本)が埋まっていると pytest は `check-slot: … 待つ` を出して順を待つ(止まってはいない)。
uv.lock の hy の版を書き換えると(`version = "1.3.1"` の 1 行)、検はその版の PyPI の Hy を使い捨ての環境に入れて測る。PyPI の Hy の最新は 1.3.1。
