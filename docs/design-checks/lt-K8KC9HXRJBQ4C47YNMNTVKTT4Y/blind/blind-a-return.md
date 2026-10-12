<!-- 盲検 A の返答(未加工・Agent tool が付けた行頭の 2 字の字下げと、返し手の枠の行だけを外した)。agentId a18f0c67c5a3f99b4 -->
依頼文の 1〜5 に沿って返します。失敗例は 1 つ見つかりました。対象は S3 の表が自ら例に挙げている「`:enum` つきの string を Literal にする」変更です。M6 だけでは済まず、型検査の側の 2 か所と M8 の側にも変更が要ることを、写しの木で実際に確かめました。合格・安全とは結論しません。

## 1. 変更要求と、それが要る理由

- **要求**: defmcp-tool で `:enum ["buy" "sell"]` を持つ string の引数に、handler の注記として `Literal['buy', 'sell']` を付ける。
- **理由(実測 E0)**: d8db309 のままでは、enum の値で `match` を分ける本体(`"buy" qty` / `"sell" (- qty)`)が strict で `reportMatchNotExhaustive` の赤になります。書き手は wildcard を足して黙らせるしかありません。
  - Literal にすれば、網羅の漏れと綴りの誤りを型検査が見てくれます。
  - `docs/12-mcp-tools.md` の例(submit-order)も `:enum` を使っています。
  - S3 自身が「変える要求」の例としてこの変更を挙げています。

## 2. 関係する主張・前提と、予想に反して変わる物

- **元の主張**: S3 の予想は「変わるのは M6 の match の表だけ。M7・M8 は変わらない」です。
- **前提**: S3 の前提は「新しい型は M7 の import を要しない(要るなら M7 も変わる — 共同の契約)」です。
  - Literal は import を要するので、この前提を満たしません。M7 の変更に限っては、「M6 だけ」の主張の適用範囲外と明示します。
  - ただし S3 の表は、この例そのものを「変わらない: M7」の行に置いています。表が自分の前提と食い違っています。
- **M6 の不変条件**: 注記は文字の注記にする/型検査のための展開と実行時の展開で同じ注記にする/非必須の引数の `| None` は M8 を写す。
- **M8 の形**: 渡す値は JSON を解いた値で、引数の取り出し方を書き手から隠す。

実際に変えて確かめた結果は次のとおりです。

| 変わる所 | 必要か | 区別 |
|---|---|---|
| M6 `_mcp-handler-param` | 予想どおり | 公開の形の意図した拡張 |
| M7 `static_view.py` の `MCP_STATIC_IMPORTS` に `_doeff_Literal` を足す | 要る(E2) | 前提が認める共同の契約・配線 |
| `static_check.py` の `with_static_helpers` / `_referenced_roots`(module の頭の import の刈り込み) | 要る(E3: import を足すだけでは刈られる) | 書かれていない。M6 の表現の知識の漏れ |
| M6 の不変条件「同じ注記」 | 実行時の注記が、型検査の側にしか無い名を指す(E4: `get_type_hints` が NameError) | M6 の公開の形の契約の綻び。今は読む所が無く潜在 |
| M8(または defmcp-tool の実行時の検査) | Literal を正しい型に保つには要る(E4) | 前提の外。S3 は「M8 は変わらない」と予想 |

## 3. どの知識がどこへ漏れて波及するか

**(i) M6 の注記の書き方が、doeff-hy-check の import の刈り込みへ漏れる**

刈り込みは、名の参照(`ast.Name`)だけを「使われている」と数えます。

```python
def _referenced_roots(statements):
    return frozenset(node.id for statement in statements for node in ast.walk(statement) if isinstance(node, ast.Name))
```

M6 の注記は文字なので、中の `_doeff_Literal` は使われていないと判定され、M7 に足した import が消されます。
- import の行を足す事自体は、前提が認める共同の契約です。
- しかし「刈り込みが文字の注記の中まで読む」必要は、設計のどこにも書かれていません。M6 の隠すはずの表現(文字の注記)を、doeff-hy-check が知らなければならなくなります。

**(ii) M6 の「両方の展開で同じ注記」と、型検査の側だけの名がぶつかる**

型検査の側の名(`_doeff_<名>`)は、M7 が module の頭にだけ置く物です。それを注記に書くと、実行時の `__annotations__` が評価できない名を持ちます。

**(iii) 主な漏れ: 実行時に届く値の保証を、誰も持っていない**

M6 の写しは「handler に届く値の集合」を型として断言しています。しかし M8 は schema を検めずに渡します。

```hy
(setv args (lfor name (.param-names tool) (.get req.arguments name)))
```

`mcp_server.py` の `_handle_tool_call` にも検めはありません。結果は次のとおりです(実測 E1・E4)。
- d8db309 の時点で既に、`int` と注記した引数に `"100"` が届くと、本体の `(* qty 2)` は `'100100'` を成功として返します。
- 必須の引数が送られないと、`int` と注記した引数に None が入ります。

設計が共同の不変条件として認めているのは「非必須なら None」だけです。「値が `:type`・`:enum`・`:required` に従う」は持ち主がいません。

Literal にすると、型検査の働きが逆転します(実測 E4)。
- 守りの無い本体は赤 0 件なのに、実行時には `"BUY"` や `"short"` で成功の None を返します。
- 大文字を正す守り `(= side "BUY")` を書いた本体は、`reportUnnecessaryComparison` の赤になります。

直すには、次のどちらかが要ります。
- **M8 が検める**: 合わない引数を会話の中で断る。M8 の契約の拡張で、JSON の型から Python の型への写しの表が M6 と M8 の 2 か所に分かれます。
- **defmcp-tool の実行時の展開が検査を足す**: M6 が M8 側の知識(bool は int の仲間、int は float として受ける、など)を持つ事になります。

どちらでも「M6 の match の表だけ」は成り立ちません。この部分は契約の拡張ではなく、隠すはずの知識の漏れ・宣言されていない共同の不変条件と判断します。

## 4. 最小の入力と再現手順、観測した結果

`/tmp/ki973b-blind-a/scripts/repro.sh` を流すと全段を再現し、最後に写しの木を d8db309 の形へ戻します(約 65 秒)。

| 段 | 変更 | 観測(すべて実測) |
|---|---|---|
| E0 | なし | `enum_tool.hy` の place-order に `reportMatchNotExhaustive` が 1 件 |
| E1 | なし | `int_guard.hy` の isinstance の守りが `reportUnnecessaryIsInstance`。M8 の実物 `_tool-result-with-stack` へ `{"qty":"100"}` を渡すと `('tool', True, '100100')`、`{"qty":2.5}` は `5.0`、`{}` は None 由来の TypeError |
| E2 | M6 だけを変える(`Literal`) | tool 1 つにつき `"Literal" is not defined` などの赤が 4 件。それでも 3 つの検は **59 passed** |
| E3 | M6 を `_doeff_Literal` に変え、M7 に import を足す | 赤は同じ。投影の頭から `typing` の import が刈られている |
| E4 | さらに刈り込みが文字の注記を読むように変える | place-order は赤 0 件。守りのある tool は `reportUnnecessaryComparison` が 1 件。M8 は `"BUY"` / `"short"` で `('tool', True, None)`。`get_type_hints` は NameError。3 つの検はなお **59 passed** |

3 つの検(表の検・注記の型検査の検・MCP の macro の検)には、`:enum` を持つ引数を strict で検める物がありません。そのため M6・M7・刈り込みの間の共同の契約を、どの段でも捕まえませんでした。

作った file はすべて `/tmp/ki973b-blind-a/` の下にあります。
- 検体: `e1/int_guard.hy`、`e2/enum_tool.hy`
- 道具: `scripts/run_m8.py`、`scripts/show_projection.py`、`scripts/repro.sh`
- 差分: `patches/e2-m6-literal.diff`、`patches/e3-m6-literal-alias.diff`、`patches/e3-m7-literal-import.diff`、`patches/e4-static-check-string-annotation.diff`
- 元と変更後の file: `orig/`、`patched_e2/`、`patched/`
- 写しの木: `tree`(d8db309 の形へ戻し済み。元の repo の管理情報に触れないよう、写しの中の `.git` の file だけは消してあります)

読む木と他の作業木は変えていません。doeff-hy-check の展開の cache(既定の `~/.cache/doeff-hy-check`)には、最初の数回の実行で記録が残っています。

## 5. 未確認の前提・不足する情報

- **実測した物**: 上の表の全部。M8 については VM の側の `_tool-result-with-stack` だけを走らせました。
- **読んだだけで走らせていない物**: HTTP の側(`_handle_tool_call`)の経路。
- **未確認**: Claude Code や Codex などの client が、送る前に inputSchema で引数を検めるかどうか。検めるなら M8 の危うさは小さくなりますが、server の側の保証にはなりません。この系で agent が実際に `"BUY"` や `"100"` を送るかも、記録を見ていません。
- **推測**: handler の注記を実行時に評価する読み手は無いと見ています。ただし探したのは `doeff/`・`doeff-agents/src`・`doeff-hy/src` の grep だけです。
- **試していない実装**: 型検査の側だけ文字でない注記を出す道。刈り込みを変えずに済みそうですが、M6 の 2 つの不変条件を破ると推測します。`:enum` を記号(定数)で書いた時に str へ戻る経路も試していません。
- **関連する観測(読んだだけ)**: `docs/12-mcp-tools.md` は `:default` を「省かれた時の値」と書いています。しかし M8 は `.get` で None を渡し、`McpParamSchema.default` は agent に見せる JSON schema にしか使われていません。
- **他の会話・GitHub・作業ボード**: 一切書いていません。
