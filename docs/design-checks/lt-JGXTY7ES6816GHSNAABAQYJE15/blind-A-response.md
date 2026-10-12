<!-- 盲検 A の返答(未加工)。Agent tool の返答の本文を、harness が付けた行頭の 2 字の字下げだけ外して保存した。起動の記録は blind-meta.md -->

# 失敗例: class を建てる定義の macro に typing の補助を 1 つ足すと、projection-imports の判定まで変えることになる

**要旨**

S1 では、別の module の補助を足す時は「static_view.py の表を 1 行足せば済む」としています。しかし、この系で typing の補助を足す綴りは別名の from-import(`from typing import ClassVar as _doeff_ClassVar`)で、後処理の判定 `_synthesized_import` はこの形を表から拾えません。

このため class の本体に書いた定義では、この import が外の class の本体に残ります。入れ子の型の本体や method からは引けないので、pyright は未定義と読むはずです。

同じ形の変更はすでに 3 回入っています(#2296・#2322・#4196)。今の版の defwire・defeffect・defrecord(:check つき)を class の本体に書くと、この失敗が今も起きるはずです(推測。設計者の実行で確かめてください)。今ある検はこれを捕まえません。

## 1. 変更の要求と、それが要る理由

**要求**: class を建てる定義の macro の型検査の展開に、pyright strict の誤検出を消すための typing の補助を 1 つ足す。

例: 頭の辞書つきの defrecord の記帳(`__doeff_tags__`・`__doeff_checks__`)を、defwire の `__doeff_wire__` と同じく `ClassVar` の注記で型検査に見せる。

**理由**: 型検査の展開は記帳の setattr を外します。属性の型を見せるには、型の本体に注記を置くしかありません。同じ理由の変更は、コードの註から次の 3 つが読めます(実測は読んだだけ)。

- defwire の `ClassVar`(#2296): `/home/ubuntu/.worktrees/doeff-wt-scoped-definition/packages/doeff-hy/src/doeff_hy/record.hy` の 513-520 行・530 行
- defeffect の `ClassVar` と `cast`(#2322): `/home/ubuntu/.worktrees/doeff-wt-scoped-definition/packages/doeff-hy/src/doeff_hy/declarations.hy` の 374-387 行・256-269 行
- defrecord の :check の `cast`(#4196): record.hy の 396-399 行・417 行

綴りを別名の from-import にしている理由も record.hy の 526-529 行の註にあります。pyright は ClassVar を `ClassVar` の名か `typing.ClassVar` の形でしか見分けません。また `import typing` を定義ごとに出すと、strict が reportDuplicateImport を出します。

## 2. 関係する主張・前提と、予想に反して変わるもの

| 元の主張 | コードを読んだ結果 |
|---|---|
| S1: 変えるのは macro の module と検体 1 行。scoped-definition と projection-imports は変えない | 後処理の判定 `_synthesized_import`(static_check.py)か、module の頭に置く補助の文字列 `STATIC_HELPER_IMPORTS`(static_view.py の 149-164 行。責務の表のどこにも割り当てが無い)を変える必要がある。後者なら、macro 側にも「型検査の展開では import を出さない」分岐が要る |
| S1: 別の module なら表を 1 行足す(公開契約の拡張) | (a) from-import の枝は `_doeff_hy_module(module)` しか見ず、表を読まない(static_check.py の 394-395 行)。(b) 表を読むのは `ast.Import` で、しかも `asname is None` の時だけ(385-393 行)。(c) 「表」は 1 つの表ではない。macro ごとの名前つき定数 2 つを、判定が名指しで並べている(389 行 `(*DEFSYSTEM_IMPORTS, DEFEFFECT_OUTCOMES_MODULE)`)。`DEFEFFECT_OUTCOMES_MODULE` を足した commit 23c7686d4 でも、static_check.py の import と判定を書き換えている(git show で実測)。さらに `DEFSYSTEM_IMPORTS` は defsystem の展開の綴りそのもの(system_form.hy の 285 行)なので、ここに行を足すと defsystem の展開まで変わる |
| R1: 型検査の投影で、class の本体の defrecord・defwire・defeffect の展開の import が、外の class の局所の名にならない | `_doeff_ClassVar`・`_doeff_cast` の import は外の class の本体に残る(読み)。不変条件 4 は「doeff_hy と表の module」に絞っていて、R1 より狭い |
| 不変条件: 利用者が書いた import は動かさない | 表に素の `import typing` の行を足す道を取ると、利用者が関数の中に書いた `import typing` も移ってしまう。判定は綴りで見分けていて、誰が書いた import かを見ていないため |
| 検体 1 行で漏れを捕まえる | class の本体の投影の検(tests/test_nested_definition_scope.py の 774-784 行)は `_PROJECTION_ROOT_NAMES` の 3 つの名(689 行)しか見ないので、通るはず。実行時の検(673-684 行)は `_publish-imports` が名を module に置くので通るはず。class の本体の検体の表 2 つ(630 行・694 行)は、macro の数え上げで足すことを強いられていない |

**前提は守っています。**

- 変更は S1 の型そのもの(既存の class を建てる macro に補助の import を足す・`nested-reads` は真)です。
- 「型検査の展開は包めない」も、入力の概要にある名の引き方の規則(入れ子の class の本体と method は外の class の段を飛ばす)も、そのまま使っています。
- 規模は import 1 つ・class 1 つです。

## 3. どの知識・判断がどこへ漏れるか(契約の拡張との区別)

**隠すはずの知識の漏洩**

漏れているのは「class の本体では、入れ子の型の本体や method が読む名を、module の段から引けるようにする」という判断です。責務の表では、scoped-definition が持つ「場所ごとの import の置き方」にあたります。

- 実行時の側: `_publish-imports`(macros.hy の 737-757 行)が、forms の中の import を形で全部拾って module の globals に置きます。綴りは見ません。
- 型検査の投影の側: 同じ判断が projection-imports にあり、こちらは module の綴りの許可リストで決まります。
- 基準が 2 つに分かれているので、新しい補助は実行時では建つのに、投影では解けません。
- 定義の macro は、補助の綴りを後処理が見分けられる形に合わせる必要があります(doeff_hy は素の import と点つきの読み、typing は別名)。型検査の展開だけで import を出し分ける分岐も、すでに `_helper-imports`(macros.hy の 633-649 行)と record.hy の 417 行にあります。責務の表は definition-macros の隠す知識を「なし」としていますが、実際は投影の置き方を知っています。

**共同の不変条件(今は宣言されていない)**

「macro が合成した import のうち入れ子の scope が読む名は、どの場所でも module の段で解ける」。実行時は scoped-definition、投影は projection-imports が分けて担っていて、両方に共通の検がありません。

**意図した契約の拡張で済む部分**

素の `import X`(別名なし)で新しい module を足す場合だけです。それでも static_check.py の 389 行の並びに定数を足す配線の変更が要るので、「static_view.py の 1 行」ではありません。ここは漏洩ではなく、配線の見積もりが小さすぎるという話です。

**S2 との関係**

判定を変えると POST_PROCESSING の digest が変わり、保存した投影は全部作り直しになります。これは S2 の主張どおりで欠陥ではありません。費用として書いておきます。

## 4. 最小の入力と再現の手順(設計者が走らせる)

**入力** `/tmp/sd-repro-classvar/src/outer.hy`(4-6 行が class の本体、7 行が対照の関数の本体):
```hy
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(defclass OuterWire [] (defwire Span {:names :camel} #^ int start-at))
(defclass OuterRecord [] (defrecord Span {:check [(>= start 0)]} #^ int start))
(defclass OuterEffect [] (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "t" :role "intent"}}))
(defn in-function [] (defrecord Span {:check [(>= start 0)]} #^ int start) (defwire Wire {:names :camel} #^ int start-at) None)
```

**script** `/tmp/sd-repro-classvar/tools/show_projection.py`:
```python
import ast, symtable, sys
from pathlib import Path
import doeff_hy.macros, doeff_hy.record  # 検と同じく先に読む
from doeff_hy.static_check import Projection, project

root = Path(sys.argv[1])
p = project(root, [root], root / "outer.hy")
assert isinstance(p, Projection), p
print(p.text)
bound = set()
for n in ast.parse(p.text).body:
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        bound |= {a.asname or a.name.split(".")[0] for a in n.names}
print("module binds:", sorted(x for x in bound if x in {"_doeff_ClassVar", "_doeff_cast"}))
def walk(t, path):
    here = path + [t.get_name()]
    for s in t.get_symbols():
        if s.get_name() in {"_doeff_ClassVar", "_doeff_cast"} and s.is_referenced():
            print("/".join(here), s.get_name(), "free" if s.is_free() else "global" if s.is_global() else "local")
    for c in t.get_children():
        walk(c, here)
walk(symtable.symtable(p.text, "projection", "exec"), [])
```

**手順と観測すべき結果**(結果はすべて推測)

1. `cd /home/ubuntu/.worktrees/doeff-wt-scoped-definition && uv run python /tmp/sd-repro-classvar/tools/show_projection.py /tmp/sd-repro-classvar/src`
   - `class OuterWire:` の本体に `from typing import ClassVar as _doeff_ClassVar` が残り、`class Span:` の本体に `__doeff_wire__: _doeff_ClassVar[doeff_hy.wire.WireShape]` がある。
   - `OuterRecord` の本体に `from typing import cast as _doeff_cast` が残り、`Span.__post_init__` が `_doeff_cast` を読む。
   - `OuterEffect` の本体に `from typing import ClassVar as _doeff_ClassVar, cast as _doeff_cast` が残り、`Inner` の本体が両方を読む。
   - `import doeff_hy.wire` などは module の段へ移っている(今回の修正が効いている所)。
   - `module binds: []` と出る。
   - symtable の行: class の本体の 3 か所(3.14 では注記の scope `__annotate__` も含む)と `__post_init__` が `global`。`in_function` の下は `free`。
   - **判定**: class の本体で `global` が出て、module がその名を束ねていなければ、失敗例が成り立ちます。
2. pyright で確かめる: `uv run doeff-hy-check --root /tmp/sd-repro-classvar/src --no-cache /tmp/sd-repro-classvar/src/outer.hy`
   - pyright が PATH に要ります。手での型検査を `hy_type_check.hy gate` に通す決まりがあるなら、そちらで走らせてください。
   - 観測: 4・5・6 行に reportUndefinedVariable(`"_doeff_ClassVar" is not defined`・`"_doeff_cast" is not defined`)が出て、7 行には出ない。
   - ClassVar が解けないので、`__doeff_wire__`・`__doeff_answer__` が dataclass の欄に数えられ、欄の順や構築子の赤が重なって出るかもしれません。
3. 今の検が捕まえないことの確認: `uv run pytest tests/test_nested_definition_scope.py -k "class_body"` は通るはずです(根拠は 2 節の最後の行)。
4. S1 の手当てが効かないことの確認(使い捨ての作業木で):
   - static_view.py に新しい定数 `"typing"` を足しても、手順 1 の結果は変わらないはずです。
   - static_check.py の 389 行の並びに足しても、別名の from-import は残るはずです。from-import の枝が表を読まないためです。
   - 逆に、利用者の検体 `(defn g [] (import typing) (typing.cast int 1))` では、`import typing` が module の段へ移るはずです(不変条件の違反)。

## 5. 未確認の前提と不足する情報

- **実測(読んだだけ)**: 2 節の行番号の中身、git show 23c7686d4 で判定の行が書き換わったこと、`STATIC_HELPER_IMPORTS` に ClassVar と cast が無いこと、型検査の展開では `_in-class-body?` が偽になり publish が出ないこと(macros.hy の 714-718 行・776-786 行)。
- **推測(未実行)**: 投影の文面、symtable の印、pyright の赤とその位置、既存の検が通ること。
- **pyright の名の引き方**: 外の class の段を飛ばすのは、binder の作りについての私の知識です。版と設定(reportUndefinedVariable が有効か)は確かめていません。
- **R1 の範囲**: 設計者が R1 を「doeff_hy と表の module の import」に限る意図なら、この例は「R1 の文言と不変条件 4 の食い違い」になります。その場合も、S1 の「表 1 行で拡張できる」は成り立ちません。
- **利用の実態**: 消費する repo で class の本体に defwire・defeffect・defrecord を書いているかは調べていません(R1・R2 があるので、想定はされています)。
- **直し方の前提**: 判定を広げる道を取ると、`ClassVar` だけと `ClassVar, cast` の 2 つの文が module の段で同じ名を 2 回宣言し、strict が赤にする恐れがあります(推測)。

**補足(主の例とは別の観察)**: `_synthesized_import` は綴りだけで見分けるので、利用者が関数の中に書いた `from doeff_hy.x import y` や `import doeff_core_effects.outcomes` も、今の版で module の段へ移るはずです(読みからの推測)。不変条件「利用者が書いた import は動かさない」と食い違います。

共有の source は変えていません。全数の検査と、他の会話への連絡もしていません。
