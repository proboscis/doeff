## 盲検 A の結果: 責務分離が崩れる失敗例

失敗例は見つかりました。defmcp-tool が tool ごとに出す固有の import(`doeff.mcp` の `McpToolDef`・`McpParamSchema`)が、型検査のための展開では畳まれずに残ります。そのため 1 つの module に tool を 65 個書くと `McpToolDef` の型が Unknown になり、tool を使う側の型の誤りが黙って通ります。#1686 と同じ訳で、設計が自分で実測に使った「tool 65 個」の規模そのものです。直そうとすると defmcp-tool(M3)の中では閉じず、M2 と M1 にも手が入ります。

### 1. 変更要求と、それが要る理由

- **要求**: 「#1686 の直しを tool の値にも効かせる。1 つの module(または class)に defmcp-tool を 65 個以上書いても、型検査のための展開で tool の値が `McpToolDef` と読め、doeff-hy-check が tool を使う側の型の誤りを捕まえる」。
- **理由(実測)**: 実装の版 d4c8d7ce9 でも、型検査の投影には tool ごとに `from doeff.mcp import McpToolDef, McpParamSchema` が残ります(2 個なら 2 文、65 個なら 65 文)。tool が 65 個になると、pyright は `McpToolDef` を Unknown にします。
  - MCP server の tool の一覧を `McpToolDef` として登録したり読んだりする code の型の誤りが、65 個を境に検出されなくなります。
  - strict の設定では、書き手には直せない赤が tool ごとに 1 つ増えます。

### 2. 関係する主張・前提と、予想に反して変わる所

**関係する主張**
- M3 の行: 「持つ知識: その定義に固有の import(defmcp-tool の doeff.mcp の McpToolDef・McpParamSchema)」。
- M2 の行: 「不変条件: 補助の名の宣言は module の頭の 1 つだけ(#1686)」「隠す知識: 型の出所を定義の macro から隠す」。
- 基準の版の障害の記述: 「tool 65 個の module で pyright が `_doeff_do` を Unknown にした」。

**前提を守っているか**
- 利用者は `_doeff_do` を import しません(検体が import するのは `doeff` の `Pure` だけ)。
- M1 の表は変えません。
- 置き場は M4 の範囲内です(module の直下と class の本体)。
- pyright の設定は doeff-hy-check の既定(basic)です。
- 公開契約の 2 つ目(`_` で始まる名を import する文は頭の補助の import だけ)は、書いてあるとおり守られています。実測で、指定の検 `-k mcp` は 3 passed、`_doeff_do` の宣言は 1 つでした。
- つまり公開契約の違反ではありません。崩れるのは責務の表のほうで、「固有の import は M3 が持てる」という割り当てが成り立ちません。
- 「`McpToolDef` の型が読める事は契約の外だ」と読む事もできます。その場合は、#1686 の直しの後も 65 個の tool を持つ module の型が読めない事を設計が明示していない、という不足になります。

**予想に反して変わる所**
- **案 (b): 投影の後処理で重なった import を畳む(defsystem の先例と同じ形)。** static_view に `doeff.mcp` の綴りを足し、`static_check._synthesized_import` も変えます。M2 の側が defmcp-tool の固有の import を知る事になります。module の直下では効きましたが、class の本体の tool 65 個には効きませんでした(実測)。
- **案 (a): 頭の補助の import に載せる。** 変更は 3 か所に及びます(推測・試していない)。
  - M2: `STATIC_HELPER_IMPORTS` に `from doeff.mcp import McpToolDef as _doeff_McpToolDef …` を足す。
  - M3: defmcp-tool はその別名を読み、型検査のための展開では import を出さない。
  - M1: 実行時の展開でも、M3 が読むようになった `_doeff_` の別名を誰かが import しなければなりません。表に `doeff.mcp` の行を足すと、defk などすべての展開が `doeff.mcp` を import します。そうしないなら `(_helper-imports)` に macro ごとの引数を足す事になり、口そのものが変わります。

### 3. どの知識がどこへ漏れるか

- **M2 から M3 へ**: M2 が隠すはずの「置き方」の知識が、定義の macro に漏れます。その知識とは、「型検査の投影では、定義ごとに出した import の宣言が積もり、64 を超えると Unknown になる。だから名は頭に 1 度だけ置く」という事です。定義の macro は、固有の import を自分の展開に置いてよいかを自分では決められません。
- **M3 から M2 へ**: 投影の後処理(`static_check.py:369` の `_synthesized_import`)は、「macro が合成した import か」を module の名の許可リストで判断します(doeff_hy と `DEFSYSTEM_IMPORTS`)。そのため、固有の import を持つ定義の macro が増えるたびに、その module の名が static_view と static_check に入ります。
- **先例(実測・git log)**: static_view.py には、macro ごとの import の不具合の後に、`HANDLER_STATIC_NAMES`・`TRAVERSE_STATIC_NAMES`・`SESSION_STATIC_IMPORTS`・`DEFTEST_FIXTURE_TYPES`・`import pytest as _doeff_pytest`・`DEFSYSTEM_IMPORTS`(97886d454)が順に足されています。M2 は、実態としては各 macro の import の台帳になっています。

**区別**
- **意図した公開契約の拡張ではない**: 補助の表に行を足す事自体は、設計が用意した拡張の口です(S1 は「足す補助が M1 の表に既に在る」を前提にして、この場合を外しています)。問題はその手前にあります。`McpToolDef` は設計が「展開が名で読む補助」ではなく「M3 に固有の知識」に分類した物です。それでも M2 の不変条件を守るには補助へ移すしかありません。つまり「補助か固有か」という分類が、責務の境として成り立っていません。
- **配線の変更ではない**: 実装の版の defmcp-tool はすでに `_helper-imports` を呼んでいて、それとは関係がありません。
- **共同の不変条件に持ち主がいない**: 「投影の中の 1 つの名の宣言は 64 以下」は、すべての macro の出力に掛かる不変条件です。ところが持ち主(M2)と検(M5)が置かれているのは `_` で始まる補助の名だけで、固有の import の名には持ち主も検もありません。M5 の検は `_` の名だけを数え、70 個の tool の検も後ろの defk の呼びしか見ません。だから tool の値が Unknown でも緑のままです(実測)。

### 4. 最小の具体例と再現手順、観測した結果

**検体**(`/tmp/ln19-blind-a/many/n65/tools.hy`、`many_tools.py` が作る):

```hy
(require doeff-hy.macros [defmcp-tool <-])
(import doeff [Pure])
(defmcp-tool tool-0 "tool 0" [{:name "text" :type "string" :description "text"}] (<- r (Pure text)) r)
;; … tool-1 から tool-64 まで同じ形(計 65 個)
(defn #^ int last-tool-name [] (. tool-64 name))   ; わざとの誤り: name は str
```

**手順**(作業 dir は読ませる版の根、Python はすべて `uv run --frozen python -I` で走らせる):
1. `many_tools.py /tmp/ln19-blind-a/many 2 64 65 70` で検体を作り、doeff-hy-check(basic)にかける。
2. `strict_counts.py /tmp/ln19-blind-a/many 64 65` で strict の設定にかける。
3. `patch_fold.py /tmp/ln19-blind-a/many` で案 (b) を試す。1 つの process の中で `_synthesized_import` を差し替え、`doeff.mcp` の import も畳むようにして、投影を pyright に直にかける。
4. `class_body.py /tmp/ln19-blind-a/many 65` と、対照の `class_body.py /tmp/ln19-blind-a/many 2` で、class の本体の場合を見る。

**観測した結果**(すべて実測・pyright 1.1.411):

| 条件 | 投影の `from doeff.mcp import` | わざとの誤り(reportReturnType) |
|---|---|---|
| N=2 / N=64(basic) | 2 文 / 64 文 | 出る |
| N=65 / N=70(basic) | 65 文 / 70 文 | 出ない(診断 0 件) |
| strict の N=64 | – | 出る(診断 449 件) |
| strict の N=65 | – | 消えて reportUnknownMemberType に変わる(診断 587 件) |
| 案 (b) の差し替え、module の直下に N=65 | 1 文に畳まれる | 出る(396 行) |
| 案 (b) の差し替え、class の本体に N=65 | 65 文のまま | 出ない |
| 対照: class の本体に N=2 | 2 文 | 出る(21 行) |

- strict の N=65 では、tool ごとに `Type of "tool_k" is unknown` が増えました。
- 指定の検 `pytest packages/doeff-hy/tests/test_static_check.py -k mcp` は 3 passed で、この欠陥を捕まえません。

### 5. 未確認の前提と不足する情報

- **規模(推測)**: 実際に 65 個以上の defmcp-tool を 1 つの module か class に持つ利用者が今いるかは見ていません。消費者の repo の全数検査はしていません。閾の 65 は、設計自身の実測と #1686 に合わせた数です。
- **案 (a) の波及(推測)**: M1・M2・M3 が変わるという見込みは、code を読んでの推測です。試したのは案 (b) を 1 つの process の中で差し替えた所までです。
- **`McpParamSchema`(推測)**: 同じ import 文に入っているので同じく Unknown になると見ていますが、個別には確かめていません。strict の N=65 で reportUnknownArgumentType が 128 から 195 に増えたのは、その傍証にすぎません。
- **pyright の版**: 実測は 1.1.411 です。本番の版(static_types.pyi の註には 1.1.414 とある)で閾が同じ 64 かは確かめていません。
- **実行時の展開**: 試していません。tool ごとの import は実行時には無害だと見ていますが、推測です。
- **別の欠陥(深追いしていない)**: strict の設定では tool の数に関係なく、handler の引数に注記が無いため tool ごとに reportMissingParameterType などが出ました(実測)。`:type "string"` が型の注記に写されていないためです。
- **S3 への別の反例の候補(code を読んだだけ・実行していない)**:
  - `macros.hy:2594` の `_validation-imports` も、「実行時は `doeff.do`、型検査では static_types」という同じ決めを持っています。S3 が範囲の限界に挙げた M6 と `_do-reference` に、これは入っていません。
  - static_types.pyi は `from doeff.do import do as do` で実行時の `do` をそのまま再公開しています。実行時の `do` を別の実装へ替えるなら、主張に反して M2 も変わります。

### 作った実験の file(すべて `/tmp/ln19-blind-a/` の下)

- 実験の script:
  - `/tmp/ln19-blind-a/show_projection.py`
  - `/tmp/ln19-blind-a/many_tools.py`
  - `/tmp/ln19-blind-a/strict_counts.py`
  - `/tmp/ln19-blind-a/patch_fold.py`
  - `/tmp/ln19-blind-a/class_body.py`
- 検体と投影:
  - `/tmp/ln19-blind-a/two/`(tools.hy と strict の pyrightconfig.json)
  - `/tmp/ln19-blind-a/two_py/`(tools.py と pyrightconfig.json)
  - `/tmp/ln19-blind-a/many/` の下: `n2`・`n64`・`n65`・`n70`・`strict-n64`・`strict-n65`・`fold-a-as-is`・`fold-b-patched`・`class2`・`class2-patched`・`class65`・`class65-patched`

読ませる版の作業木は変えていません(`git status` は空)。

WAIT: work — 頼んだ会話の次の依頼
