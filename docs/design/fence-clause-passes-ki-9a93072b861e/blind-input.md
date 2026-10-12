# 盲検 A・B への入力(対象の版: doeff `e76b005506f605df68ef52d2c4c4157d25280df3`)

読んでよい物: 作業木 `/home/ubuntu/.worktrees/doeff-wt-dc-ki9a93`(この版の checkout)と、そこの `.design-check/lt-XNEJ/evidence/impl-change.patch`(変更前 `6533804cf` との差分 — analyzer の package だけ)。**読むだけ**。file の書き換え・pytest・型検査・semgrep・uv sync・他の会話への連絡はしない(実行した結果を書かない。推測と実測を分ける)。

## 1. 何のための仕組みか
`packages/doeff-effect-analyzer` は、ソースを読むだけで(実行せず)、ある Program が外へ出す効果(effect)を、handler の列を通して数える静的解析器。`pass_through` が、効果を内側の handler から外側へ順に通し、各 handler の「節」(clause)が答える効果を消し、節が自分で出す効果を足し、残りを返す。残りが「どの handler も答えない効果 = gap」。agora-controllers などの closure の検が、この残りが空である事を確かめる。

doeff-hy の `defhandler` / `handle` は、節に番(`:when 番`)を付けられる。例:

```
(defhandler fence
  (EffectBase [] :when (not (isinstance effect Nap)) (raise (ValueError "fenced"))))
```

は、Python の dispatcher として次の形に展開される(要点だけ):

```
if isinstance(effect, EffectBase):
    if not (not isinstance(effect, Nap)):      # = 番が偽 → 外へ渡す
        yield Pass(effect, k)                   # 効果を外側の handler へ渡す
    else:
        raise ValueError("fenced")
else:
    yield Pass(effect, k)
```

つまり Nap はこの handler を素通りして外へ出る。直す前の analyzer は、番を読まず、総受け(EffectBase)の節が全部の効果に答えると数えたので、この柵を通る Nap を「答えられた」と読み、外へ出る効果を空とした(受入条件 1)。

## 2. 受入条件
1. 柵の handler の節を通る X の効果が外へ出る事を analyzer が読む(module の段の節でも、関数の中の節でも)。
2. 番が class だけからは決まらない節(欄の番 `(Ask [key] :when (= key …))`)は、従来どおり節が答える。「通るかもしれない」と読むと closure の検が大量に赤くなる(agora-controllers の番つき節 170 件のうち 145 件がこの形)。

## 3. 責務と公開の形
(表は `claims-before-blind.md` の「責務」の節と同じ内容 — 下に写す。)

| id | 責務 | 持つ知識・判断 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 | 節が「届いた効果を答えず外へ渡す」条件の表し方と判定(`program_effects.py` の `ClassTest`・`NotTest`・`AllTest`・`AnyTest`・`UnknownTest`・`PassTest`、`Clause.passes`・`Clause.passes_on`) | 届いた効果の class から 渡す / 答える / 不明 の 3 値を出す算術(isinstance は部分類で真・上位の class で不明・無関係で偽、not / and / or の 3 値論理)。不明は「答える」へ畳む | 番の綴り・AST・scope | `PassTest.holds_for(arrived) -> bool \| None`、`Clause.passes_on(arrived) -> bool` |
| M2 | 番の読み(`handler_effects.py` の `_guards_in`・`_yielded_call_on`・`_sent_on_guards`・`_pass_test`・`_read_clauses`) | 節の suite の直下の `if` で本体が効果への yield 1 つのものを番の候補とする形の契約。その呼びが `Pass` かは scope で解いた class が `doeff_vm.Pass` かで判じる。番の if は節として数えない | doeff-hy の展開の綴り(別名・gensym の名)・`Pass` の import の仕方 | `_read_clauses(...) -> _ReadClauses`(`Clause.passes` を詰める) |
| M3 | 効果の流れ(`program_effects.py` の `pass_through`) | handler の列を内から外へ通して何が出るかの計算。`passes_on` が True の効果は節で消さず、届いたまま(出どころも保って)外へ出す | 節の中身の読み方・AST・番の綴り | `pass_through(inner, handlers, include) -> Residual`。節には `handles`・`passes_on`・`emits` しか問わない |
| M4 | doeff-hy の節の展開(`packages/doeff-hy/src/doeff_hy/handle.hy` の `_build-clause`)。**変更していない** | 番の綴り: `(if (not 番) (yield (Pass effect k)) 本体)` を節の suite の直下に置く | analyzer の読み方 | 展開した Python の AST |
| M5 | 解析結果の保存(`result_cache.py` と `Clause.__getstate__`) | analyzer の版(source の digest)を鍵に結果を覚える。`Clause` は pickle で往復する | 番の意味 | `Clause.__getstate__` の dict |

副作用: M1〜M3 は純粋。M2 は AST と import 済み module の globals を読むだけ。寿命: M1 は値(frozen dataclass)、M2 は 1 回の読み、M3 は 1 回の計算。

## 4. 将来の変更の候補と、設計側の主張(前提つき)

| id | 軸 | 変わる要求 | 変わる責務 | 変わらない責務・契約 | 局所化できる理由 | 前提・限界 |
|---|---|---|---|---|---|---|
| S1 | effects | doeff-hy が展開の綴りを変える(`Pass` の別名・gensym の名・補助の import の置き方) | M4 だけ | M1・M2・M3・M5 | M2 は名でなく scope で解いた class が `doeff_vm.Pass` かで判じ、M3 は `passes_on` しか見ない | 別名が scope で解ける事。番の if の形(suite 直下・本体が効果への yield 1 つ)を変えるなら M2 の形の契約の変更(契約の拡張) |
| S2 | effects | 番に class テストの新しい書き方が増える(`type(effect) is X`・`effect.__class__ in (…)`) | M1(variant を 1 つ)と M2(`_pass_test` の 1 分岐) | M3・M4・M5 | M3 は `PassTest` の中身を知らず `passes_on` だけを呼ぶ | テストが届いた効果の class だけで決まる事 |
| S3 | storage | 解析結果の保存。`Clause` に欄 `passes` を足した | M1 と M5(`__getstate__` に 1 項) | M2・M3 | 保存の鍵が analyzer の source の digest で、欄の無い古い保存は `passes=None`(従来の読み)として読める | 古い保存が混ざる経路は鍵が防ぐ |
| S4 | concurrency | 閉じの解析が複数の thread で同じ handler を読む | なし | M1〜M5 全部 | 番の読みは 1 回の `_read_clauses` の局所値。node ごとに覚える `_DISPATCHES` は scope に依らない構造だけで、`Pass` かの判定は scope ごと(覚えない) | 既存の `_DISPATCHES` の cache の扱いは変えていない |
| S5 | simulation | 模擬の環境が複合の番の柵(`or` した isinstance と欄の `and`)を足す | なし | M1〜M5 全部 | class で決まる部分だけで確実に渡る効果は外へ出て、欄で決まる部分は不明 = 従来どおり節が答える | 欄の部分の読みは範囲外 |
| S6 | distribution | 単一の機体から cluster の複数の Pod へ | 対象外 | — | analyzer は source と import 済み module だけを読む純粋な計算 | — |
| S7 | hardware | 機体の差 | 対象外 | — | 静的な読み | — |

### 設計側が予想する変更範囲
- S1: 変わるのは M4 の file(`handle.hy`)だけ。analyzer の file は変えない。
- S2: `program_effects.py` に variant 1 つと `handler_effects.py` の `_pass_test` に 1 分岐。`pass_through` の行は変えない。
- S3: `Clause.__getstate__` の 1 項と欄の既定値だけ。

前提: 未知の将来をすべて予測できるとは主張しない。doeff-hy が番の if の形そのもの(suite の直下・本体が効果への 1 つの yield)を変える変更は M2 の形の契約の変更で、この主張の範囲外。

## 5. 強制方法と検査の実体(盲検 B へ)
実施済みの強制は次だけ。これ以外の検(M1 の真理値表の検・`pass_through` を手組みの節で流す検・pickle の往復の検など)は**存在しない**。

- 契約の検 `packages/doeff-effect-analyzer/tests/python/test_guarded_clauses.py`(12 本): macro 展開の柵(module の段・関数の中の `handle`)・番の形(正の番・組の class・module 定数の class・複合の番・欄の番・factory の引数の class)・別名の `Pass` で書いた手書きの dispatcher・gap の報告。
- `PassTest` は型の和(`ClassTest | NotTest | AllTest | AnyTest | UnknownTest`)で、各 variant が `holds_for` を持つ。
- 検査の呼び方(B は実行せずに通るかを推論する。通ると断定する時は根拠を書く):
  - `uv run pytest packages/doeff-effect-analyzer/tests/python -q`(146 passed が基準)
  - `uv run semgrep --metrics=off --config .semgrep.yaml packages/doeff-effect-analyzer/python <変えた file> --error`(`.semgrep.yaml` の 35 規則。その file を読んでよい)
  - `uv run ruff check <変えた file>`(この package は変更前から 13 件の指摘を持つ。件数を増やさない)
  - `uv run pyright <変えた file>`(変更前から 7 件。増やさない)
  - 着地の門(merge queue): 変えた所の focused 検・衝突の印の走査・follow-install
  - 変えた所の品質検査 `code-quality --scope changed`(fast の段・型検査なし・変えた file は継続検査に未登録)

## 6. 実コードの在り処(読む対象)
- `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/handler_effects.py`(M2: `_dispatch_of`・`_guards_in`・`_yielded_call_on`・`_sent_on_guards`・`_pass_test`・`_read_clauses`)
- `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py`(M1: `ClassTest` 以下・`Clause`、M3: `pass_through`)
- `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/result_cache.py`(M5)
- `packages/doeff-hy/src/doeff_hy/handle.hy`(M4: `_build-clause`)
- `packages/doeff-effect-analyzer/tests/python/test_guarded_clauses.py`
- `.semgrep.yaml`(35 規則)
