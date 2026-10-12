# 設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y — 設計本文(盲検の後・最終)(card ki-973b4e44d7bc)

盲検の前の主張は `design-before-blind.md`(commit f95de2618 で固定・sha256 d91e5225ebefb3b4e8ddcc9e124c0f07c09a906e21c2df9702fe4442112f5bde)。
この文書はその主張を書き換えず、盲検 A・B の失敗例・再現・直した事・再検証を足す。

## 0. 全体の図(直す前 ⇒ 直した後)

```
 直す前(21c737842)              d8db309f3(L2187・盲検に読ませた版)           直した後(6f4391412 L2197・e5b63f5e7 L2199)
 ───────────────                ─────────────────────────────                ─────────────────────────────────────
 do の置き場を 4 か所が綴る        M1 macros.hy _runtime-do が置き場を持つ            同じ。加えて M12 test_runtime_do_spelling.py が
   _helper-imports                  ├▶ M2 _helper-imports                           .hy の form で doeff.do を綴る所を数え、
   handle.hy _do-import             ├▶ M3 handle.hy _do-import                      _runtime-do の 1 つだけである事を守る
   _do-reference                    ├▶ M4 _do-reference                              (一覧 M9 の外の新しい macro も断る)
   _validation-imports              └▶ M5 _validation-imports

 MCP の引数                        M6 _mcp-handler-param が自分の match で           M10 doeff/mcp.py MCP_JSON_TYPES(表は 1 つ)
   handler の引数に注記なし           schema の :type を注記に写す                       ├▶ M6 注記: mcp-param-annotation
   (strict で赤 8 件)              M8 mcp_server_loop は値を検めずに渡す              └▶ M8 検め: McpToolDef.decode_arguments
                                     (注記の型と届く値が食い違う — 盲検 A)                 合わない値・省いた必須は handler の前で断る
                                                                                    M11 test_mcp_json_type_table.py: 表を差し替えると
                                                                                      M6 と M8 が両方従う
```

## 1. 完了の範囲・順序・版

| 項目 | 内容 |
|---|---|
| 範囲 | 実装完了まで(3 つの変更が doeff main に入済み) |
| 順序の申告 | 依頼は class=investigate。card の受入の条が「doeff main で緑」を求めていたので、この会話で実装して main に入れた後(d8db309f3)に設計検証を行い、盲検の失敗例を受けた直し(6f4391412)と強制の追加(e5b63f5e7)も同じ会話で入れた。本来の「調査 → 別の実装の依頼」の分け方に沿っていない |
| 盲検に読ませた版(base_revision) | d8db309f31b816f539dd226ba1fdab5b3a37f44d(L2187) |
| 直した版 | 6f439141223231fd40ba86a7640f3548409c7a6d(L2197・MCP の型の表を 1 つにし server が検める)、e5b63f5e7a3a9863b0cebd71c9c075519c305694(L2199・do の綴りの検) |

## 2. 固定した受入の条

- c99aedbcf: defmcp-tool の型検査のための展開で handler の引数に schema の型の注記が付き、strict で tool 1 つの赤が 0 件になる検が doeff main で緑 — ☑(d8db309f3)。直した後も緑(evidence/annotation/strict-before-after.txt: 直す前 8 件 → 0 件)。
- ca9a5d5a2: 実行時の do の import の決めが補助の表 1 か所になり、表を差し替えると 4 つの macro が追従する検が緑 — ☑(d8db309f3)。直した後も緑。

## 3. 責務(最終)

M1〜M9 は design-before-blind.md の表と同じ(M7 は投影の頭の import の刈り込み `static_check.py` も含めて読む)。盲検の後に M10〜M12 を足し、M6・M8 の公開の形が変わった。

| id | 変わった事 |
|---|---|
| M6 | 注記を自分の match ではなく M10 の `mcp-param-annotation` から引く。McpParamSchema の組み立ては書かれた欄を 1 度ずつ渡す `_mcp-param-schema`(`:default` を `:required`・`:enum` と一緒に書くと定義の時に落ちた誤りを直した)。既定値が `None` の引数も `| None` |
| M8 | `(.decode-arguments tool req.arguments)` で検め、断る訳があれば handler を呼ばずに `#("tool" False 訳)` を返す |
| M10(新) | `doeff/mcp.py` の `McpJsonType`・`MCP_JSON_TYPES`・`mcp_json_type`・`mcp_json_type_names`・`mcp_param_annotation`・`McpParamSchema.decode`・`McpToolDef.decode_arguments`。JSON の型 → 注記・受ける Python の型・number の整数を float にするかの唯一の定義元 |
| M11(新) | `tests/test_mcp_json_type_table.py`: 表を差し替えると M6(展開)と M8(本物の `_tool-result-with-stack`)が両方従う。表に足した型を両方が受け、表に無い型を両方が断る |
| M12(新) | `packages/doeff-hy/tests/test_runtime_do_spelling.py`: doeff_hy の `.hy` の form で `"doeff.do"` の文字と `doeff.do` の import を、top の定義の名ごとに数え、`_runtime-do` の文字 1 つだけである事を守る |

## 4. 変更シナリオ — 事前の主張と実測

| id | 事前の主張(design-before-blind.md) | 実測 | 判定 |
|---|---|---|---|
| S1 effects(do の置き場を替える) | M1 だけが変わる | 置き場を点を含む module へ差し替えると 8 macro が従う(M9 47 件)。M3・M4・M5 を直の綴りに戻すと 12・6・5 件落ちる。盲検は S1 を崩す例を出さなかった | 主張どおり |
| S2 simulation(記録つきの do に差し替える) | M1 の値の差し替えだけで 8 macro が従い、実際に包む | 同上。差し替えた do が実際に包む事も M9 の実行の検で見る | 主張どおり |
| S3 effects(MCP の引数の型を足す・変える) | M6 の match の表だけが変わる | **崩れた**。盲検 A: 注記は handler に届く値の型を約束するのに、M8 は値を検めずに渡す(`int` の引数に `"100"` → `'100100'` を成功として返す・enum の外の値も通る・省いた必須の引数に None)。盲検 B: M8 に検めを足すと写しの表が 2 つになり(number を float の isinstance で検めて正しい JSON の整数を断る)、3 つの検は全部通る。さらに Literal(盲検 A)は M7 の import と刈り込みの変更を要する(前提の外) | 知識の漏洩: 「値が schema に従う」の持ち主がいなかった。M10 へ表を移し、M6 と M8 がそこから読む形に直した。直した後は、型を足す変更は M10 の表の行だけ(M11 の probe-type で確かめた) |
| S4 effects(do を引く新しい macro) | M9 の一覧と新しい macro だけが変わる。一覧の外は見ない(限界を申告) | 盲検は攻めなかった。設計者の実験: `_runtime-do-import` を通す新しい macro は差し替えに従う。`doeff.do` を自分で綴る新しい macro を M9 は見ない(申告どおりの限界) | M12 を足して限界を閉じた。綴る新しい macro は M12 が名を出して断る |
| S5〜S8 | 対象外(理由は design-before-blind.md) | — | — |

## 5. 盲検 A・B

起動の記録は `blind/blind-meta.md`(第 1 候補 gpt-6-astra は起こせず opus・effort xhigh で代用、観測した model は未確認)。入力は `blind/blind-common.md`・`blind-a-input.md`・`blind-b-input.md`(commit f95de2618)。返答は未加工で `blind/blind-a-return.md`・`blind/blind-b-return.md`、実験の file は `blind/a-files/`・`blind/b-files/`。

| 失敗例 | 設計者の再現(版・証拠) | 成立 |
|---|---|---|
| A(iii): 注記の型と M8 が渡す値の食い違い | d8db309f3 で本物の `_tool-result-with-stack` に通す: `{"qty": "100"}` → `('tool', True, '100100')`、`{}` → None 由来の誤り、`{"side": "short"}` → 通る(evidence/a/m8-before-d8db309.txt) | 成立 |
| A(i)(ii): Literal は M7 の import と刈り込みの変更を要する | 再現していない。S3 の前提(M7 の import を要しない型)の外と盲検 A 自身が明示しており、M7 の共同の契約として限界に残す(盲検 A の E2〜E4 の差分は blind/a-files/) | 前提の外 — 限界に記す |
| B: M8 に写しの表を足しても検が通る | d8db309f3 に盲検 B の差分を当てると、3 つの検は 59 passed。`{"price": 2}`(正しい JSON の number)を断る退行(evidence/b/second-table-d8db309.txt) | 成立 |
| B(a): number の引数に 2 → defk の `(: price float)` で落ちる | evidence/a/m8-before-d8db309.txt の price-tool `{"price": 2}` | 成立 |
| B(c): `:default` を `:required False` と一緒に書くと定義の時に落ちる | evidence/a/m8-before-d8db309.txt の combo-tool(TypeError) | 成立 |
| B(c): `:default` を server が当てない | evidence/a/m8-before-d8db309.txt の default-tool `{}` → None | 成立 |

## 6. 直した事と再検証

- 6f4391412(L2197): M10 を足し、M6 と M8 がそこから読む。直した後、同じ再現は全部断るか正しい値で動く: `"100"`・省いた必須・bool の number・enum の外は断る、`{"price": 2}` → `4.0`、`:default` が当たり、`:default` と `:required False` の組も定義できる(evidence/fix/m8-after-fix-6f43914.txt)。
- 変異(evidence/fix/mutations-6f43914.txt・script は evidence/scripts/mutations.py.txt): 直した形を直す前・盲検 B の形へ戻すと、どれも検が落ちる — M3・M4・M5 を直の綴りへ(12・6・5 件)、M1 の値を記号から綴る(6 件)、M8 の検めが写しの表を引く(1 件)、M6 が自分の match を持つ(1 件)、M8 が検めずに渡す(2 件)。戻した後の作業木は空。
- e5b63f5e7(L2199): M12 を足した。直す前の版 21c737842 の `.hy` に同じ数え方を当てると、card の文が名指した 4 か所を数える(evidence/s4/spelling-check.txt)。S4 の正常例と失敗例は evidence/s4/s4-new-macro.txt。
- 関わる検: 6f4391412 で 339 passed(evidence/fix/related-tests-6f43914.txt)。strict の赤: 直す前 8 件 → 0 件(evidence/annotation/strict-before-after.txt)。

## 7. 強制方法(最終)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M1 が実行時の do の置き場を 1 つ持つ | 置き場を差し替えた展開の構文木と実行で 8 macro が従う事を見る契約の検(M9)。`.hy` の form で `doeff.do` を綴る所が `_runtime-do` の 1 つだけである事を数える検(M12) | `packages/doeff-hy/tests/test_helper_imports_table.py`・`packages/doeff-hy/tests/test_runtime_do_spelling.py` | 4 時間に 1 度の全体テスト・変更した file から選ぶ検 | M12 は doeff_hy の `.hy` だけを読む(他の package の Hy の macro・`.py` の module の import は見ない)。validate を class の本体に直に置く組は M9 の実行の検から外す |
| M10 が JSON の型の意味を 1 つ持ち、M6 と M8 がそこから読む | 表を差し替えると注記と server の検めが両方従う契約の検(M11)。`McpParamSchema` は表に無い型を組み立ての時に断る | `tests/test_mcp_json_type_table.py`・`tests/public_api/test_mcp_tool_def.py`(decode)・`doeff/mcp.py` の `__post_init__` | 同上 | 写しの表を置いても差し替えに従う形(例: 呼ぶたびに MCP_JSON_TYPES を読んで dict に組む)は M11 が見ない。Literal など M7 の import を要する型は M7 と刈り込みの変更が要る(前提の外) |
| M6 の注記が strict で書き手の赤を出さない | pyright strict の検(doeff-hy-check `--strict`) | `packages/doeff-hy/tests/test_static_check.py::test_defmcp_tool_handler_params_carry_the_schema_types` | 同上(pyright が在る時だけ) | 型検査は全体テストの処理ステージだけで走る |

## 8. 未確認・範囲の外

- HTTP の経路(`mcp_server.py` の `_handle_tool_call` から VM の loop まで)は通していない。検めは `_tool-result-with-stack` の中に在り、盲検 B と設計者の再現はこの関数を直に呼んだ。
- Claude Code などの client が inputSchema で引数を先に検めるかは確かめていない。
- validate を class の本体に直に書くと NameError(展開の import が class の属性になる)。表とは関わらない元からの形で、依頼者は今は card にしないと決めた(lt-28GBKZ3D0N0VBJXMDKHQZQKZG1)。
- defk の `(: x float)` の契約は実行の時に isinstance で int を断るが、pyright の float は int を受ける。MCP の引数は number の整数を float にして渡す事で食い違いを消したが、defk の契約そのものの食い違いは範囲の外。
- 盲検の model の実の出自は未確認(blind-meta.md)。
