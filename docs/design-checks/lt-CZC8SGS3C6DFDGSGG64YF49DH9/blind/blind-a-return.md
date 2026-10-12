## 結論

責務分離が崩れる例が見つかりました。利用者が自分の業務の effect に `Transfer`(振込)と名付け、その handler を class の本体にまとめると、節が書き手の `Transfer` ではなく doeff の VM の `Transfer` を指します。その結果、effect は誤りも警告もなく外の handler へ抜けます。S1 の前提は守っており、S1 の適用範囲の中の例です。既存の `tests/test_handle_scope.py` は 45 件すべて通り、この例を捉えていません。

## 1. 変更の要求と、それが要る理由

- **要求**: 振込を扱う業務の module で、本番の handler と、test で差し替える handler(FakeBank)を class の属性としてまとめます。これは材料 §1「利用の状況」にある Handlers の形です。業務の effect の名は `Transfer` です。
  - 求める性質は「節の effect の型と節の本文が読む名は、展開が内部で import する名と綴りが同じでも、書き手の物を指す」ことです。
- **理由**: 08ddfc707 で class の本体の handle・defhandler が動くようになったので、利用者はこの形を使い始めます。業務の名が `Transfer`・`Put`・`Get`・`Pass` になるのは普通のことです。test の `_MODULE_OWN_NAMES` の註も「例えば振込の effect の Transfer」を想定しています。

## 2. 関係する主張・前提と、予想に反して変わる所

**前提は守っています。**
- `Transfer` は module の段の dataclass の effect です(S1 の前提「effect の型と節が読む名は module の段に在る」)。
- class の段に `yield` はありません。
- 公開契約の「展開の内部(どこで import するか)は公開契約ではない」の範囲で、書き手に予約語の制約が掛かる例です。

**崩れる主張**
- S1 は「節の関数は閉包から名を読み、module の globals を読まない」と主張しています。ところが閉包から読むのは展開の操作だけではありません。書き手が節に書いた effect の型の名(`isinstance` と注記)も、同じ関数の中で同じ閉包から引かれます。
- R3 が守るのは「展開の名が書き手の名に左右されない」の一方向だけです。逆向きの「書き手の節が展開の名に左右されない」は守られていません。
- 隠すはずの知識(handle-expansion の「import の名の束ね方」、handler-imports の「名と提供元」)が、書き手の節の名前空間に漏れています。

**要求を満たすために変える所(コードを読んで数えた見積もり。実際には直していません)**

| 変える所 | 責務 |
|---|---|
| `handle.hy` の `_vm-imports`(L74-82) | handler-imports |
| `_rewrite-ops`(L463・472・476・485)、`_build-clause`(L646)、`_build-handler-expr`(L671)、`_build-lazy-init-forms`(L241・255) | handler-expr |
| `_handler-fn-form`(L93・96)、handle(L780・784) | handle-expansion |
| defhandler の `lazy-imports`(L914-915) | defhandler-expansion |
| `binding_forms.py` の `_assign`(L614-615:素の `Put`・`Some`) | 節の本体の束縛の書き換え。責務の表に無い module |
| `macros.hy` の `set!`(L2521-2522:素の `Put`・`Some`) | macros.hy の別の責務 |
| `tests/test_handle_scope.py` の L32-44(`_HANDLE_IMPORT_NAMES`)と L300(`assert "WithHandler" in vars(mod)`) | 漏れを振る舞いとして固定している検 |
| module の段の展開 | S3 の「1 字も同じ」は、この変更では成り立たない |

変えずに済む所は、scope-judgment・clause-endings・bytecode-guard(digest は自動で変わる)です。

## 3. 何の知識がどこへ漏れるか

- **K1 実行時の名の綴り**: `Resume`・`Transfer`・`Pass`・`WithHandler`、session の `Get`・`Put`・`Some` です。表では handler-imports が持つはずの知識ですが、`binding_forms.py` と `set!` の註に「実行時は defhandler が出す import の Put / Some」と書かれ、前提にされています。
- **K2 置き場**: class の本体と関数の中では、名は節の関数を囲む関数の局所に置かれ、書き手の節の式から見えます。module の段では書き手の globals を上書きします。
- **区別**
  - 公開契約の拡張ではありません。節の頭は任意の effect の型で、内部の名は公開しないと明記されています。
  - 配線の変更でもありません。
  - 共同の不変条件として正当なのは、handler-imports と handler-expr の間で綴りが一致することだけです(S2 が予想している範囲)。それが表に無い `binding_forms.py` と書き手のコードにまで広がっている部分が漏洩です。
- **型検査の展開は逆の決めをしています**: `static_view.py` の L124-128 は、`_doeff_` を前に付ける理由を「書き手の書いた Put / Some の import と 1 つの名を分け合わないため」と書いています。実際に型検査の展開は `from doeff import Pass` だけを出すので(実測)、型検査では節の `Transfer` は書き手の型を指し、実行時とずれます。
- **S2 への含意(推測)**: S2 のように handler-imports に名を 1 つ足すたびに(例 `TransferThrow`・`ResumeThrow`・`Perform`)、書き手の節から見えなくなる名が 1 つ増えます。module の段では書き手の globals を上書きします。S2 の予想「handler-imports と handler-expr だけが変わる」は置き場については成り立ちますが、書き手に見える振る舞いの変更を伴います。

## 4. 最小の例と再現の手順、観測した結果(実測・Python 3.14.3)

```hy
(require doeff-hy.handle [defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Transfer [EffectBase] #^ int amount)  ; 書き手の振込
(defclass FakeBank [] (defhandler bank (Transfer [amount] (resume (+ amount 100)))))
```

`run(scheduled(state(initial={})(FakeBank.bank(Transfer(5)))))` の期待値は 105 です。

再現: 作業木で `uv run python -I /tmp/blind-a-ki-edab/p2_transfer.py` を走らせます(p3〜p7 も同じ形)。

| 形 | 08ddfc707 | 8fcefa7c6(直す前) |
|---|---|---|
| class の本体の defhandler・引数ありの defhandler・handle で `Transfer` を受ける | UnhandledEffect(module の `Transfer` は書き手の物のまま) | NameError `_doeff_do` |
| 対照: 同じ形で effect の名だけ `Bump` | 105 | — |
| 外に Python で書いた「本番」の handler、内に FakeBank | `'本番の振込が走った'`(期待は 105。黙って外へ抜ける) | — |
| session var を持つ handler で、書き手の KV の effect `Put` を受ける | UnhandledEffect | — |
| defk の本体の handle | UnhandledEffect | 同じ(前からの問題) |
| module の段の defhandler | module の `Transfer` が `doeff_vm.Transfer` に上書きされ TypeError | 同じ(前からの問題) |

- **展開を Python の文に落とした結果(p7)**: handler を組む関数の中で `from doeff import Resume, Transfer, Pass` の後に `def _hy_anon_3(effect: Transfer, k): if isinstance(effect, Transfer): ... yield Transfer(k, amount + 100)` が続きます。1 つの scope で、同じ名が書き手の型と VM の node の両方として使われています。
- **変わり方**: 08ddfc707 は、この入力での class の本体の失敗を、大きな音の出る失敗(NameError)から黙った誤配に変えました。

## 5. 未確認の前提と、足りない情報

- doeff-hy-check(pyright)は走らせていません。型検査は全体テストの時だけという規則のためです。型検査の展開の文から「書き手の `Transfer` として通す」と推測しています。
- Python 3.13 以前は測っていません。その版では `_effect-parameter` が注記を文字列にし、module の globals で評価するので、VM の絞り込みと `isinstance` のずれ方が違う可能性があります。
- 関数の中の形が ki-907f779b3290 より前にどう動いたかは見ていません。
- 実際の利用者の repo に `Transfer`・`Put` などの名の effect が在るかは調べていません(読む範囲の外)。現実性の根拠は test の註と業務の一般論だけです。
- 直すための変更範囲は、コードを読んでの見積もりです。
- 再現は test と同じ `hy.eval` の module で行い、file の import の道(bytecode guard を通る道)は通していません。
- 外の handler を Python で書いたのは、module の段の Hy の handler だと書き手の `Transfer` を上書きしてしまうためです。

作業木は元のままです(`git status` は `BLIND_INPUT.md` だけ)。一時の file は `/tmp/blind-a-ki-edab/` に置いてあります。

WAIT: work — 依頼元の会話の次の依頼
