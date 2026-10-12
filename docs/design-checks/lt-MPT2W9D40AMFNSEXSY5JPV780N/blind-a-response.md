## 結論

失敗例が 1 つ見つかりました。class の本体に書いた defeffect・defwire・defrecord(`:check` 付き)は、型検査用の投影で、展開が合成した `_doeff_ClassVar`・`_doeff_cast` が「未定義」になります(実測)。

直し方は 2 つしかありません。別名の表 expansion-names の契約を handler 以外へ広げるか、投影の後処理 static-check-postprocess に各 macro の綴りを教えるかです。どちらを選んでも、主張「後処理は変えない・綴りは隠す」とは合いません。

## 1. 変更要求と、それが必要になる理由

**要求:** class の本体に書いた defeffect・defwire・defrecord(`:check` 付き)を、doeff-hy-check(hook とマージ前の型検査)で赤なしに通す。要件 2 で defhandler に入れた直しを、型を建てる定義にも広げる要求です。

**理由:**
- 今回の変更(要件 1)で、これらの型は class の本体でも実行時に動くようになりました。書き手が使い始めるのは自然な流れです(例: 系の effect を名前空間の class にまとめる `(defclass Kv [] (defeffect Get …) (defeffect Put …))`)。
- ところが型検査では、書き手が書いていない名に赤が出ます。書き手には直せない赤なので、決まり(macro の展開の型は macro の側で付ける)により macro の側で直すしかありません。
- S2(展開が新しい関数を書く)も同じ所に当たります。展開が書く関数や型の本体が `_doeff_` の別名を読むと、class の本体では同じ赤になります。defrecord の `:check` の展開が書く `__post_init__` が `_doeff_cast` を読む例は、すでに起きています(実測)。

## 2. 関係する主張・前提と、予想に反して変わるもの

**関係する主張**
- static-check-postprocess は「どの import が macro の合成か(4 種の分類)」を知り、「別名の綴りの具体」は隠す。
- expansion-names は「handle・defhandler の node の別名の唯一の定義元」。
- defeffect-form と record-class-form は「型がどこに書かれたか」を隠す。
- S2 では、scoped-definition と static-check-postprocess は変わらない。

**前提を守っているか**
- 「書き手は `_doeff_` の名を import しない」: 守っています。検体の書き手が書いた import は dataclass だけです。
- 「seal_record / seal_effect は class を作り直さない」: 触れていません。
- 要件 3: 直すのは投影だけで、実行時は変えません。対照の module の段・関数の中の検体には赤が出ません(実測)。
- したがって、この例は契約の適用範囲内です。

**予想に反して変わるもの(直し方ごと)**
- **案 A:** `expansion_names.py` の `_HOMES` に `"ClassVar": "typing"` と `"cast": "typing"` を足す。実測で赤は 0 件になりました。
  - ただし、表の契約が「handle の node」から「型を建てる macro の typing の別名」まで広がります。
  - しかも `declarations.hy:381` と `record.hy:421`・`record.hy:534` は表を読まず、自前で `_doeff-ClassVar`・`_doeff-cast` と綴っています。案 A が効くのは、その綴りがたまたま `alias(name)`・`home(name)` と一致するからです。
  - 正しく契約を広げるには、declarations.hy(defeffect-form)と record.hy(defrecord・defwire)を、表から import を組む形に配線し直す必要があります。
- **案 B:** `static_check.py:380` の `_synthesized_import` に「typing からの `_doeff_<名>`」の規則を足す。後処理が 3 つの macro の綴りを知ることになり、「綴りを隠す」に反します。
- **案 C:** `_doeff_` の頭だけで合成かどうかを判じる(共同の不変条件に頼る形)。ただし traverse の docstring と examples は、書き手に `(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])` を書かせています。前提はすでに破れているので、traverse の契約も変えないとこの案は採れません。

## 3. どの知識がどこへ漏れるか

「class の本体から入れ子の型・method は名を引けない」問題を、実行時と投影では別の仕組みが扱っています。

| 面 | 扱う所 | やり方 | 新しい別名を足した時 |
|---|---|---|---|
| 実行時 | scoped-definition の `_publish-imports`(`macros.hy:738`) | 「名の綴りは読まない」。展開の import を全部 module の globals に置く | 変更不要(`_doeff_ClassVar`・`_doeff_cast` が globals に在るのを実測) |
| 投影 | static-check-postprocess の `_synthesized_import` | 投影では `_in-class-body?` が偽で scoped-definition は何もしない。後処理が綴りの分類 4 種で決める | 型を建てる macro が自前で綴った別名は、どの分類にも入らない |

**漏洩の中身**
- 「入れ子から読む合成の名」を知っているのは作る側の macro で、「それを合成と見分ける」のは後処理と表です。知識が 2 か所に分かれています。
- そのため、別名を 1 つ足すたびに、もう 1 か所の変更が要ります。
- 忘れても、module の段と関数の中では赤が出ず、class の本体でだけ出ます。作る側は「class の本体に書かれた時の投影」を意識せざるを得ず、隠すはずの場所の知識が漏れています。
- 後処理の docstring は、作る側を名指しで列挙しています(defsystem・defeffect の outcomes・handle)。作る側ごとの知識を後処理が持つ形で、今回の `ClassVar`・`cast` はその列挙から漏れた分です。

**区別**
- **意図した契約の拡張:** S1(handle の node を表に足す)。handle.hy が表から綴りを引くので、設計どおりです。
- **配線の変更:** 案 A を正しく行う時の、declarations.hy・record.hy の import の組み替え。それ自体は正当ですが、S2 の「変わらない」の一覧と合いません。
- **共同の不変条件:** `_doeff_` の頭の約束。成り立っていれば後処理を綴りから切り離せますが、traverse で破れています。

**検査の穴**
- 契約テスト 2 が見るのは `_doeff_Pass`・`_doeff_WithHandler` だけです。
- 既存の `test_static_projection_of_class_body_definition_binds_no_module_name`(`tests/test_nested_definition_scope.py:843`)は根の名(doeff_hy ほか)しか見ないので、この赤を通してしまいます。

## 4. 最小の再現(実測)

検体は `/tmp/tmp.Fxw86pJYIx/chk/` に置きました。要の 2 つは次のとおりです。

```
;; b_cls_effect.hy
(require doeff-hy.macros [defeffect])
(defclass Outer []
  (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}}))
;; c_cls_record_check.hy
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(defclass Outer [] (defrecord Span {:check [(>= start 0)]} #^ int start))
```

対照として次の 3 つも置きました。
- a: class の本体の defhandler(今回の直しの対象)
- d: 同じ定義を module の段に書いたもの
- e: 同じ定義を関数の中に書いたもの

実行した命令:
`uv run --no-sync --project /work/toolset/worktrees/doeff-wt-class-body-name doeff-hy-check --root <dir> --no-cache --pyright <.venv>/bin/pyright --python <.venv>/bin/python <dir>/*.hy`

結果:
```
5 個の .hy を検めた: 新しい error 3 件・基点に在る error 0 件・warning 1 件
b_cls_effect.hy:3:49 - error: "_doeff_ClassVar" is not defined (reportUndefinedVariable)
b_cls_effect.hy:3:49 - error: "_doeff_cast" is not defined (reportUndefinedVariable)
c_cls_record_check.hy:4:3 - error: "_doeff_cast" is not defined (reportUndefinedVariable)
```

- **対照:** a・d・e には未定義の赤が出ません。
- **投影の中身:** `class Outer:` の中に `from typing import ClassVar as _doeff_ClassVar, cast as _doeff_cast` が残り、入れ子の `class Inner` の `__doeff_answer__: _doeff_ClassVar[object] = _doeff_cast(...)` と `Span.__post_init__` がそれを読みます。defwire も `_doeff_ClassVar` で同じ赤になりました(pyright を直に走らせて実測)。
- **実行時:** 同じ class の本体の定義は動きます。`Outer.Inner(x=1)`、`:check` の ValueError、Inner の欄は `['x']` でした。
- **表を替えた実験:** source は変えず、process の中だけで `_HOMES` に 2 名を足すと、import が module の段へ移り、pyright の error は 0 件でした(`/tmp/tmp.Fxw86pJYIx/proj_patched.hy`)。

## 5. 未確認の前提と不足する情報

**実測したもの:** 上の doeff-hy-check(既定の mode、toolset の `.venv` の pyright 1.1.411、toolset の木は 8a759a4 で対象と同じ source)、pyright 直の検査、実行時の検体、表を替えた投影。

**推測・未確認のもの**
- `--strict` では走らせていません。`reportUndefinedVariable` は standard でも error なので、strict でも出ると推測しています。
- hook とマージ前の検査の pyright の版・設定が、この環境と同じかは確かめていません。
- 消費する repo に、いま class の本体の型が在るかは調べていません(全数検査をしない指示のため)。要求が現実的だという判断は、要件 1 と既存の test がこの形を扱う形としている事からの推測です。
- 案 A を配線し直した形、案 B、案 C は実装していません。案 C が traverse と衝突するという話は、docstring と examples を読んだだけの推測です。
- S2 の実行時の側(`__class__` で新しい method を書けば動く)と S1 は、コードを読んだ限りでは成り立ちますが、動かしてはいません。
- 検体 a の warning(`Instance methods should take a "self" parameter`)はこの失敗例とは別の件で、原因は調べていません。
- 設計が「class の本体の型の投影」を範囲外と考えているなら、これは主張の違反ではなく範囲の穴になります。既存の test がこの投影を検めているので、範囲内と読みました。

共有の source は書き換えていません(2 つの木とも `git status` は変化なし)。

WAIT: work — 依頼した側の次の依頼
