**結論**: 失敗例を 1 つ見つけ、実際に動かして確かめました。型検査のための展開(doeff-hy-check)では、handler の展開が読む別名をどの scope に束ねるかを決める所が 4 つの module に分かれていて、持ち主がいません。そのため class の本体の handler と、handler の多い module で赤が出ます。主張の材料にある検 211 件はすべて通ったままで、これを捕まえていません。

## 1. 変更要求と、それが要る理由

**変更要求**: doeff-hy-check --strict を次の 2 種類の module に掛けても、書き手に直せない赤を出さないようにする。
- class の本体に handle・defhandler を書いた module(R1 が認める置き場で、実行時は動く)
- module の段に handler を 65 本以上持つ module

自然な直し方は、前例(`_doeff_do` の #1686、defmcp-tool の ki-4848d81e0bc5、session の `SESSION_STATIC_IMPORTS`)と同じです。handler の node の別名を module の頭に 1 度だけ置きます。

**理由(実測)**:
- **(a) class の本体**: 型検査のための展開は `from doeff import Pass as _doeff_Pass`・`from doeff_vm import WithHandler as _doeff_WithHandler` を class の段に置きます。それを読むのは入れ子の関数で、入れ子の関数は class の段を見ません。結果は `reportUndefinedVariable: "_doeff_Pass" / "_doeff_WithHandler" is not defined` です。doeff-hy-check では位置 1:1 の赤になり、書き手には直せません。
- **(b) module の段の handler の本数**:
  - 60 本: `_doeff_WithHandler` は `type[WithHandler[Unknown]]`、`_doeff_Pass` は `type[Pass]` で型が付く。
  - 70 本: 別名の import が 70 回並び、pyright は 64 を超えた宣言の推論をやめて、どちらも `Unknown` にする。
  - pyright を strict で直に掛けると、`return _doeff_WithHandler(...)` ごとに "Return type is unknown" が 70 件。doeff-hy-check では位置 1:1 の 1 件にまとまって出る(まとめる仕組みは確かめていない)。

## 2. 関係する主張・前提と、予想に反して変わる所

**関係する主張**:
- R1(4 つの置き場)、R3(型検査のための展開は別名だけを読む)、R4(名は 1 つの表から引き、CLAUSE-OPS に追従する)。
- S1「変えるのは clause-ops-table だけ」。
- 責務の表: 「置き場ごとの import の置き方」は handler-expansion(handle.hy)が持ち、「補助の名の一覧」は static-head-imports が持つ。
- 型検査のための展開の検は検体 1 本で、module の段だけ。

**前提**: S1〜S3 の前提も書き手の source も変えません。どちらも R1 が認める使い方なので、契約の適用範囲の中の例です。

**予想に反して変わる所**:
- **macros.hy の `_in-class-body?`・`_in-function-body?`**: 責務の表に無いのに、型検査のための展開では handler の置き場を決めています(「型検査のための展開は偽」)。実験で、この述語から型検査の例外を外すだけ(この process の中での差し替え)で、class の本体の赤は 3 件から 0 件になりました。
- **static_view.py(module の頭に置く方式で直す場合)**: CLAUSE-OPS の static-nodes の一覧が要ります。ところが clause_ops.hy はすでに static_view を import しているので、読み込みが循環するか、一覧を写すことになります。写せば、S1 で static-nodes を持つ行を足すたびに static_view も直す必要があり、S1 の「clause_ops だけ」と R4 の追従が崩れます。
- **static_check.py(重複を畳む方式で直す場合)**: `_synthesized_import` は今、畳む対象が「doeff_hy の module と defsystem」だけです。ここが展開の別名を見分ける必要が出ます。
- **テスト**: `test_handle_clause_ops.py::test_added_operation_node_is_imported_in_both_expansions` は、node の import が macro の出力にあることを確かめています。頭に置く方式にすると、この確かめは後処理の側へ移ります。

## 3. どの知識がどこへ漏れているか

- **共同の不変条件に持ち主がいない**: 不変条件は「展開が読む別名は、読む関数から見える scope で束ねる」です。関わる module は次の 5 つです。
  - 読む側: clause_ops.hy、binding_forms.py・macros.hy(`Put`・`Some`)
  - 置き場ごとに束ねる側: handle.hy の `_vm-imports`
  - 頭に束ねる側: static_view.py(session の名だけ)
  - 置き場を決める側: macros.hy

  R3 の検は「素の名が無い」ことしか見ず、「別名が解ける」ことは見ません。標本の class-handle・class-defhandler(型検査モード)は別名が解けないまま通っています。
- **macros.hy の前提を handle.hy が破っている(漏洩)**: macros.hy の述語は「型検査のための展開は置き場で import を出さない」を前提にしています(`_scoped-definition` の註)。handle.hy はその述語に置き場を委ねながら、型検査のための展開でも置き場に import を出します。対比の実測があります。同じ class の本体の session var つき handler で、頭で束ねる `_doeff_Put`・`_doeff_Some`・`_doeff_session_key` は解け、置き場で束ねる `_doeff_Pass`・`_doeff_WithHandler` だけが未定義でした。1 つの名の族に、束ね方が 2 通りあるということです。
- **名の一覧の持ち主がいない**: expansion_names は綴りと置き場しか持ちません。展開が読む名の一覧は次の所に散っています。
  - clause_ops の各行の nodes・static-nodes
  - handle.hy の `["Pass" "WithHandler"]`・`["Some" "Get" "Put"]`
  - binding_forms.py・macros.hy の `Put`・`Some`
  - static_view.py の `("Some", "Put")`
  - テストの写し 2 つ

  束ねる場所を頭へ移すと、clause-ops-table の知識(node の名の一覧)が static-head-imports へ漏れ、依存の向きも逆になります。
- **区別**:
  - 公開契約の拡張ではありません。書き手の見る形は変わりません。
  - 重複を畳む直しは expansion_names の公開の関数(`alias`・`home`)を使うだけで、module 契約が許す正当な配線です。ただしそれだけでは (a) は直りません。

## 4. 最小の入力・再現手順・観測すべき結果

最小の入力(class の本体):
```hy
(require doeff-hy.macros [defhandler])
(import probe_effects [Tick])
(defclass Ledger []
  (defhandler only-tick
    (Tick [step] (resume step))))
```

script はすべて /tmp/blind-a-ki-1618/ に置き、作業木の根で `uv run python /tmp/blind-a-ki-1618/<script> <dir>` で走らせました。

| script | 調べたこと | 結果 |
|---|---|---|
| `static_probe.py` | doeff-hy-check --strict を 3 つの置き場に | module の段 0 件 / class の本体 9 件(`_doeff_Pass`・`_doeff_WithHandler` 未定義を含む)/ defk の本体 2 件(主題と別の unused import) |
| `classbody_probe.py` | 展開した Python を pyright に | そのまま 3 件(L28・L35・L66 の未定義)、`--patch`(`_in-class-body?` の型検査の例外だけ外す)で 0 件 |
| `classbody_session_probe.py` | session var つき handler | 頭で束ねる名は解け、Pass・WithHandler だけ未定義 |
| `many_probe.py <dir> N` | module の段に handler を N 本 | N=60 で 0 件、N=70 で 1 件 |
| `render_probe.py` + reveal_type | N=60 と N=70 の展開した Python | N=70 で別名が Unknown。strict の設定は `many/n70_check/pyrightconfig.json` |

材料の検の命令(5 file・211 件)はすべて通りました(211 passed)。

## 5. 未確認の前提・不足する情報

- **基準版 dc3385b9a とは比べていません**(履歴を読まない制限のため)。推測: 基準版も素の `Pass` を class の段に import していたので、書き手が `Pass` を import していない module では同じく未定義になったはずです。64 の限りも handler ごとの import なので同じです。つまりこの変更による退行ではなく、この変更の検が見落としている既存の穴だと見ています。
- 実在の repo に class の本体の handler や、65 本を超える module があるかは見ていません。
- 循環 import は実行していません。import 文からの推測です。
- `--patch` の実験は pyright の既定のモードで、strict の全部の赤までは見ていません。
- 次の 2 つは走らせていません(推測: Pass と同じく未定義)。
  - inline の handle を 65 本超並べた時
  - S1 の throw の行を足した上での class の本体

## 補足(主の例とは別)

- **A. S2 は契約の変更で、漏洩ではありません。** S2 は「writer-namespace は変えない」と主張しますが、保存先の `Get`/`Put` は公開の契約です。
  - 根拠: `session.py` の docstring、README の 140〜142 行、ADR-DOE-HY-006 の R8 と法則 `session-values-flow-through-state-effects`。
  - 契約を使っている実例: `test_val_var_lazy.py::test_outer_handler_answering_get_skips_the_session_initialiser_and_sees_put`。書き手の外の handler が `(Get [key] …)` で session の値を差し替え、`Put` を観測します。この検とキーの検の 2 件は今通っています(実測)。
  - S2 を実装すると、この種の書き手の handler・検、ADR、README も変わります(推測・模擬はしていない)。S2 は契約の変更として宣言されるべきです。
- **B. R4 の検の名の一覧は手で写したものです**(コードを読んだだけで、未実行)。`tests/test_expansion_names.py` の `_NAMES`(9 名)と `tests/test_handle_writer_names.py` の 7+2 名です。S1 で足す `TransferThrow` は監視されないので、本物の行の書き換えが素の綴りで読んでも `test_no_source_spells_an_expansion_name_bare` は通ります。

共有の source は変えていません。実験は /tmp の下だけで、述語の差し替えもその process の中だけです。

WAIT: work — 利用者の次の入力
