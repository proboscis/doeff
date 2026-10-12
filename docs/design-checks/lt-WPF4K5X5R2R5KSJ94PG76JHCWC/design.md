# 設計検証 lt-WPF4K5X5R2R5KSJ94PG76JHCWC — doeff-hy の import でない Hy の内部への依りを、登記の前に照合する(ADR-DOE-HY-008 R7・card ki-2fa4c02604b1)

## この文書の目的

読み終えた人が次の 3 つを言えるようにする。

1. R7 の検が、何を数え、どの Hy の版で、どう測り、いつ走るか。
2. 将来の変更(Hy の版上げ・新しい依り・light_stack の対象の変更・依りの新しい種類)が、どの部品だけを変えれば済むか。
3. 盲検 A・B が見つけた 2 つの穴と、その直し方・直した後の確かめ。

完了の範囲は**実装完了まで**。最初の実装は doeff main fe951a108(L2162)、盲検を受けた直しは dcdbb260d(この記録と一緒に登記する)。
記録の中の sha(95bee57fa・dcdbb260d)は測った時の branch wt/ki-2fa4-design-check の commit で、main へ載せ替えると sha は替わる
(中身の差分は同じ。report.json の base_revision も測った時の dcdbb260d)。

## 全体の図

```
 Hy を上げる便(uv.lock の hy の行を変える)
        │  make test-changed(scripts/run_changed_tests.py select_targets)
        │  root pyproject.toml [[tool.doeff.contract-tests]] prefix "uv.lock" → R4・R6・R7 の 3 本
        ▼
 docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │ version-source         locked-hy-version(uv.lock の hy の行 → "1.3.1")              │
 │ reliance-population    scan-hy-reliances                                          │
 │   ├ Python: hy.X.Y = …            → #(path "replaced" "hy.X" "Y")                   │
 │   ├ Hy: (defmacro m [_hy-compiler …]) → "compiler-parameter"                       │
 │   ├ Hy: (. compiler a)・compiler.a・(.a compiler …) → "compiler-attribute" a        │
 │   └ doeff_hy.light_stack._HY_CALLER_MODULES → "light-stack" module                 │
 │ pypi-hy-probe          probe-reliances → pypi-hy-run                              │
 │   uv run --isolated --with hy==<uv.lock の版>(使い捨ての環境・PyPI の Hy だけ)        │
 │   └ {"missing" […] "sources" {module source} "compiler-failure" …}                │
 │ reliance-judgment      reliance-violations(名を挙げた文)                            │
 │                        stack-call-violations ← light-stack-sample-row               │
 │                          Hy の inspect.stack(…)[n]<読み> を、light_stack が返す        │
 │                          実物の行に当てて frame が返るか                               │
 └───────────────────────────────────────────────────────────────────────────────────┘
        │ 違反 0 = 緑 / 違反あり = 名を挙げて赤
        ▼
 doeff-hy-workarounds(測られる側): doeff_hy/__init__.py(_could_be_hy_src)・macros.hy・handle.hy
 (compiler の scope・this・filename)・light_stack.py(inspect.stack の軽い版)・ast_unparse.py(rewriting_unparse)
```

## 現状と要件

要件(card ki-2fa4c02604b1 の受入の条・固定):

1. 3 つの依り(compiler.scope・hy.importer._could_be_hy_src・light_stack の当て物)を uv.lock の Hy の版で照合する検が doeff main に在り、uv.lock の変更で make test-changed に選ばれる。
2. 在らない名を 1 つ書いた失敗ケースで、その検が名を挙げて赤になる。

依りの在りかと Hy 1.3.1 の側は evidence/design-pre-blind.md の「現状」の表。

## 責務

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| reliance-population | source から依りを数える | 数える source の形(下の 4 つ) | 目印の正規表現・file の歩き方・reader と ast の使い分け | `[#(path 種類 対象 名)]` |
| pypi-hy-probe | 依りを指定の版の PyPI の Hy で測る | 使い捨ての環境の作り方・compiler の受け取り方(公開の defmacro) | uv の引数・env の掃除・probe の本文 | `{"missing" "sources" "compiler-failure"}` |
| reliance-judgment | 測った結果を名を挙げた違反にする | 種類ごとの文・「Hy の読みを light_stack の行に当てて frame が返る」 | ast の親の引き方 | 違反の文の列 |
| version-source | 照合の版を決める | uv.lock の hy の行 | toml の形 | 版の文字 |
| selection | いつ走るか | uv.lock を変えた便が払う検の組 | 選び方の実装 | 表の行 |
| hy-version-pin | doeff が入れて確かめる Hy の版 | uv.lock の hy の行・doeff-hy の上限(R6) | — | uv.lock・pyproject |
| doeff-hy-workarounds | Hy の回避 | 回避そのもの・light_stack の行の形 | — | doeff-hy の macro と import の副作用 |

依りの「種類」(replaced・compiler-parameter・compiler-attribute・light-stack)は reliance-population・pypi-hy-probe・reliance-judgment の 3 つが共有する契約。light_stack の行がどの読み(`[0]`・`.frame`)に応えるかは doeff-hy-workarounds(light_stack)の持ち物で、判定は写しを持たない(盲検 A の後の直し)。

## 変更シナリオ — 盲検の前の主張と実測

盲検の前の主張は evidence/design-pre-blind.md(sha256 は evidence/pre-blind.sha256・commit 95bee57fa)。ここでは主張を書き換えず、実測を並べる。

| id | 変える要求 | 盲検の前に予想した範囲 | 実測(直す前 fe951a108) | 実測(直した後 dcdbb260d) |
| --- | --- | --- | --- | --- |
| S1 | Hy を上げる | uv.lock(と上限)。依りが壊れたら doeff-hy の回避だけ | Hy が `inspect.stack()[n].frame` で読む版では、light_stack を正しく直しても R7 が赤のまま(evidence/a-before-fixed-light-stack.log)。判定と反例の検も変える必要があった = **予想外の波及**(盲検 A) | uv.lock と light_stack.py だけで緑(a-after-fixed-light-stack.log)。元の light_stack のままなら名指しの赤(a-after-orig-light-stack.log)。0.27.0 は 6 つを名指しの赤(s1-negative.log)。uv.lock だけの変更で R7 が選ばれる(s1-positive.log) |
| S2 | doeff-hy に新しい依りを足す | その doeff-hy の source だけ | method 呼びの形 `(._syntax-error compiler …)` は数えられず、在らない名でも緑(b-before.log)。この形を数えるには数える code を変える必要があった = **数え方の穴**(盲検 B) | method 呼びも数え、在る名は緑・在らない名は名指しの赤(b-after.log)。`(. compiler module)` と `hy.macros.require = …` を足した file は ADR・台帳・表を変えずに数えられ緑、在らない名は赤(s2-positive.log・s2-negative.log) |
| S3 | light_stack が当てる module を足す・替える | light_stack.py だけ | S1 と同じ穴(判定が行の形を `[n][0]` の写しで持つ) | light_stack.py だけを変えて hy.reader.hy_reader を足すと、行が `.frame` に応える形なら緑・元の tuple なら 142 行を名指しの赤(s3-positive.log・s3-negative.log) |
| S4 | 依りの新しい種類を測る | ADR の file の中の 3 か所(種類の語彙という共有の契約の拡張) | — | 種類の語彙を書く所は ADR の 1 file だけ(s4-positive.log — 他の当たりは別の意味の語)。知らない種類は判定も probe も ValueError で名指しで止まり、黙って緑にならない(s4-negative.log)。種類を足す実の変更は行っていない |
| 軸の外 | hardware・storage・distribution・concurrency・simulation | 不適用(理由は report.json) | — | — |

### 波及の理由の区別

- 盲検 A(S1・S3): **知識の漏れ**。light_stack の行がどの読みに応えるかは light_stack の持ち物なのに、判定が `[n][0]` という写しを持っていた。直し = 判定は、Hy の source の読み(`[k]` か `.属性`)を light_stack が実際に返す行(`light-stack-sample-row` — `__name__` を `hy.compiler` などにした大域で `inspect.stack(0)` を呼ぶ)に当て、frame が返るかを測る。行の形を変える便は light_stack.py だけを変える。
- 盲検 B(S2): **数える形の欠け**(契約の不足)。「compiler の属性の読み」という契約の中に method 呼びの形が入っていなかった。直し = reader が `((. None 名) compiler …)` に読む形も compiler-attribute として数える。
- S4 の 3 か所の変更は**共有の契約の意図した拡張**で、漏れではない。

## 強制の方法

| 守る事 | 強制の方法 | 実装の在りか | 呼ばれる経路 | 限界 |
| --- | --- | --- | --- | --- |
| 依りが uv.lock の版の PyPI の Hy に在る | deftest `test_adr_doe_hy_008_internal_reliances_exist_in_pypi_hy` | docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy | uv.lock の変更 → make test-changed の契約の組(s1-positive.log)・日次の全体の回 | 着地の門そのものは検を走らせない(登記の前の make test-changed は約束) |
| Hy の inspect.stack の読みが light_stack の行から frame を返す | 判定 `row-read-gives-frame` に実物の行 `light-stack-sample-row` を渡す | 同じ file | 同じ | light_stack が install されていない process では測れない(foundation が install してから呼ぶ) |
| 数え方が壊れて 0 件にならない・形ごとに数える | 本物の reader と ast で読む反例の assert(method 呼び・tuple 行と namedtuple 行・Hy の言葉の source を含む) | `test_adr_doe_hy_008_judgments_reject_the_counterexamples` | ADR の file の変更・日次 | 数える形の外は数えない(下の限界) |
| 在らない名を名指す | 在らない名・属性・module を probe に渡す deftest | `test_adr_doe_hy_008_reliance_probe_names_what_pypi_hy_lacks` | ADR の file の変更・日次 | — |
| 知らない種類で黙って緑にならない | 判定・probe の ValueError | `reliance-violations`・probe の本文 | 同じ | — |
| 検と台帳が揃う | enforcement-ledger.json | docs/adr/enforcement-ledger.json・scripts/check_enforcement_ledger.py | commit の hook・日次 | — |

## 限界(未導入・表現できない条件)

- 数える形の外は数えない: 別名で受けた module への代入(`import hy.importer as hi; hi.x = …`)・`setattr`/`getattr` の文字列の名・Hy の source の `(setv hy.X.y …)`・compiler を `compiler`・`_hy-compiler`・`_hy_compiler` 以外の名で受けた読み。盲検 A が挙げたが、失敗例として確かめていない。
- 名が在る事を測り、意味(`_syntax_error` が return か raise か・scope の型)は測らない(盲検 B の 5 節)。
- doeff_hy_bytecode_guard の Hy の内部への依り(`_hy_macros` の表の引き方など)は数えない — 依頼者が別の card にした(受け入れの返事 lt-VXXF30ATKCDKMQS5XTAK2Q03VJ)。
- `hy.compat.rewriting_unparse` も数える。Hy の版上げで消えると R7 が赤になる — 当て物(ast_unparse.py)を消す合図として意図した形。

## 証拠の一覧

| file | 中身 |
| --- | --- |
| evidence/design-pre-blind.md・evidence/pre-blind.sha256 | 盲検の前の主張(commit 95bee57fa) |
| evidence/blind-input.md・blind-{a,b}-prompt.md・blind-meta.md | 盲検に渡した物と起動の記録 |
| evidence/blind-{a,b}-reply.md・blind-{a,b}-files/ | 未加工の返答と、盲検が再現に使った file |
| evidence/a-*.log・b-*.log | 盲検の失敗例の再現(直す前)と再検証(直した後) |
| evidence/s1〜s4-{positive,negative}.log | シナリオごとの正常例・失敗例(dcdbb260d) |
| evidence/final-checks.log | ADR の file の 8 本・台帳の検査・契約の表の検(dcdbb260d) |
