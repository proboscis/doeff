## 結論

S2 の前提の中で失敗例が見つかりました。S2 の書き方どおりに、pytest の item を作る定義の macro を doeff_hy.macros に足します。すると、外の関数を呼ぶたびに記録が module に積まれ、それでも契約の検は 98 件すべて緑のままです。正しく書くには、deftest の本体にしか無い「記録を出すか」の決めを写すことになり、それは隠すはずの知識の漏れです。

## 1. 変更の要求と、要る理由

**要求**: doeff_hy.macros に、deftest と同じ書き方の性能の検 `defbench` を足します。pytest-benchmark の `benchmark` を受け取る pytest の関数を定義し、記録は M5 の公開の口 `record-function` から取ります。

**要る理由**: 性能の退行は、この repo が実測で追ってきた懸念です(展開の時間 #786・収集の 1 秒 #1211)。また item を作る macro は、すでに deftest・defadr・defsemgrep の 3 つがあり、増えるのは自然な流れです。hypothesis を使う性質の検など、item を作る macro なら何でも同じことが起きます。

**前提を守っているか**: 守っています。名は `def` で始まり、doeff_hy.macros の中で定義しているので、S2 の前提どおりで契約の範囲内です。書き方も S2 のとおり、文の並びを `_scoped-definition` に渡すだけです。

## 2. 関わる主張と、予想に反して変わるもの

関わる主張は次の 3 つです。
- **S2**: 変わるのは M3 と M7 の検体 1 行だけで、M1・M2・M5 は変わらない。M2 を通らない macro は M7 が止める。
- **M3 の守る事**: `forms` は module の globals に記録を積まない。
- **M5**: deftest が記録を出すかの決めを持つ。S5 の前提は「記録は module の段の関数だけを表す」。

実際には、次の判断が M2 にも M5 の公開の口にもありません。
- 判断の中身は「この定義は pytest が集める module の段の関数か、だから記録を出すか」です。
- この判断は deftest の macro の本体(`macros.hy` L2145)にだけ書かれています。
- M5 の `record_function`(`pytest_items.py` L281)は、書いた場所を問わず記録の式を返します。

そのため、正しい新しい macro を作るには次のどれかが要ります。
- (a) M3 に判断を写す(漏れ)。
- (b) M5 の口に「書いた場所」の引数を足す(契約の拡張)。
- (c) M2 に「module の段だけで出す文」の引数を足す(契約の拡張)。

(b) と (c) は、S2 が「変わらない」とした M5 または M2 の変更です。

## 3. 何がどこへ漏れるか(区別)

| 種類 | 中身 | 判定 |
|---|---|---|
| 配線 | `_DEFINITION_SAMPLES` の 1 行と、`_HEADER` の require に defbench を足す | 欠陥ではありません。require を足さないと検体の検が `set()` で赤になりました(実測)。S2 の「1 行」は、厳密には 2 か所です |
| 意図した公開契約の拡張 | 新しい macro の書き方そのもの | 正当です |
| 共同の不変条件 | 「`forms` は module の globals に記録を積まない」(`_scoped-definition` の docstring に書かれた、呼び手の負う事) | 正当です |
| **隠すはずの知識の漏洩** | 上の不変条件を守る判断を、item を作る macro ごとに作り直すことになる | **欠陥** |

判断を作り直すには、次の 3 つを知る必要があります。
- M2 が隠すはずの知識: 関数の中では、`forms` が外の関数を呼ぶたびに評価される。
- M5 の前提: 記録は module の段の関数だけを表す。
- M1 の述語の本当の意味: 「包むか」の答えであって、「module の段か」の答えではない。
  - 型検査の展開でも class の本体でも偽を返します。
  - 「module の段か」を問う別の述語(`(isinstance (. compiler scope) ScopeGlobal)`・`_module-level` L881)が別にあります。

**強制の穴**: S2 が挙げる強制は名の漏れしか見ません。globals の検は手で並べた `_CASES` だけが入力で、検体を数えても `_CASES` への追加は求めません。§6 の限界の欄にも書かれていません。

## 4. 最小の再現と、見る結果

作業木の差分は残してあります。
- `macros.hy` に `defbench` を足しました。既定は V1(判断なし)です。環境変数 `DOEFF_BLIND_A_BENCH_GUARD=1` を付けると V2 になり、deftest と同じ判断を写します。
- `tests/test_nested_definition_scope.py` に検体 1 行と require を足しました。

再現の手順と結果(作業木を cwd に):
1. 契約の検 `uv run --frozen pytest tests/test_nested_definition_scope.py tests/test_handle_scope.py -q -W ignore` を走らせると、V1 も V2 も **98 passed** でした(実測)。
2. `probe_records.py` の結果(実測):

   | macro | 外の関数を 0〜3 回呼んだ後の記録の件数 | `verify_records` の problems |
   |---|---|---|
   | defbench V1 | `[0, 1, 2, 3]` | `['記録の test_bench_inner が module に無い']` |
   | deftest | `[0, 0, 0, 0]` | `[]` |
   | defbench V2 | `[0, 0, 0, 0]` | `[]` |

3. `probe_globals_check.py` で、既存の globals の検に defbench の入力を直に渡しました(実測)。
   - V1 は赤、V2 は緑でした。既存の検は捕まえる力を持っていますが、`_CASES` に defbench の入力は無く、追加を求める仕組みもありません。
4. `probe_static_view.py` で、同じ source を 2 通りに展開しました(実測)。
   - 型検査の展開では、関数の中の deftest と V2 の defbench が `import doeff_hy.pytest_items` と `record_at_import(globals(), …)` を外の関数の中に出しました。
   - 記帳を外す `without_bookkeeping`(`static_check.py` L420)は module の直下の文しか見ないので、関数の中のこの 2 文は残ります。
   - symtable で見ると、`doeff_hy` が外の関数 `make_tests` の局所の名になっていました。その関数は先に `owner = doeff_hy.__name__` を読みます。
   - 写した判断(V2)も、型検査の展開では効きません。

## 5. 未確認の前提・推測と実測の区別

- **推測**: 型検査の展開に pyright をかけると、unbound や stub 無しの赤になると見ています。pyright は手元に用意が無く(走らせると npm から取り寄せる)、型検査を走らせる決まりもあるので、走らせていません。
- **推測(コードを読んだだけ)**: problems が空でない file は item のキャッシュに保存されず、毎回 import して収集する遅い経路になります(`pytest_plugin.py` L530-533)。プラグインを通した実際の収集は走らせていません。
- **未確認**: 型検査の展開で関数の中の deftest が記録を出す振る舞いは、今の deftest にもあります。R5(型検査の展開は変えない)の範囲では退行ではありません。基準の版 da660e83 と同じかどうかは比べていません。
- **補足(実測)**: class の本体の deftest も、module の段の関数として記録されます(`verify_records` は「module に無い」と言う)。ただし class の中の deftest は使い方として誤りに近いので、本筋には入れていません。
- **コードを読んだだけ**: doeff-adr の defadr・defsemgrep(`doeff-adr/.../macros.hy` L70・L154)は、判断なしで記録を出します。S2 の範囲の外ですが、`record-function` を呼ぶ 3 か所のうち判断を持つのが 1 か所だけ、という事実の裏付けです。
- **不足**: defbench は仮の要求で、pytest-benchmark が入っているかは確かめていません。macro を展開した結果だけを見ていて、pytest の item としては走らせていません。

実験の置き場は `/tmp/lt0YB1-blind-a/` です。
- 契約の検の出力: `contract-tests-V1.txt`・`contract-tests-V2.txt`
- 記録の積み増し: `probe_records.py` と `.out`
- globals の検: `probe_globals_check.py` と `.out`
- 型検査の展開: `probe_static_view.py` と `.out`、入力の `static/nested_items.hy`
- class の本体: `probe_class_body.py` と `.out`

WAIT: work — 依頼元(設計検証の会話)の次の入力
