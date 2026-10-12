<!-- 盲検 B の返答(未加工)。Agent tool の返答の本文を、harness が付けた行頭の 2 字の字下げだけ外して保存した。起動の記録は blind-meta.md。末尾の `WAIT:` の 1 行は子の返答に入っていた物 -->

## 結論

検査を通るのに責務を侵す失敗例を、record.hy だけを変える差分 1 つとして組みました。中身は「defrecord の `__doeff_tags__` の型を型検査に見せる」機能追加で、defwire の作法をそのまま写したものです。

- **起きること**: class の本体に書いた defrecord の投影で、展開の import が外の class の局所の名として残ります。実行時は直り、投影だけが壊れます。
- **検査**: 列挙された検査はどれも、読んだかぎりではこの差分を拒みません。
- **実測**: 私が走らせたのは grep・cat・git show・sed・awk(と `ls`)の読み取りだけです。検査・pytest・pyright は一度も走らせていません。以下の「通るはず」は、すべて読みに基づく推論です。

## 1. 機能要求と差分

**要求**: 「defrecord の頭の辞書の `:tags` を書くと、型に `__doeff_tags__` が付く。ところが記帳の setattr は投影で外れるので、`(. Span __doeff_tags__ role)` を読む書き手の code が strict の pyright で赤になる。defwire が `__doeff_wire__` に対してしたのと同じ形(agora-redesign #2296)で、型検査に見せたい」。

S1 の手順どおり「macro を持つ module だけを変える」形で、変えるのは `/home/ubuntu/.worktrees/doeff-wt-scoped-definition/packages/doeff-hy/src/doeff_hy/record.hy` の 1 か所です。検体は既存の物(`:tags` つき)が使えるので足しません。

```diff
@@ (defmacro defrecord … — 413 行目の (setv written …) の直前と、416 行目の import の直後)
                           ~@statements
                           None)]
                       []))
+  ;; 定義のタグの型を型検査に見せる(defwire の __doeff-wire__ と同じ作法・#2296)。ClassVar は defwire と同じく
+  ;; from-import の別名で読む。defwire は自分で import して :class-vars を渡すので、ここでは import しない。
+  (setv tag-class-vars (if (is tags None)
+                           []
+                           (list `[#^ (get _doeff-ClassVar doeff_hy.declarations.DefinitionTags) __doeff-tags__]))
+        class-var-import (if (and tag-class-vars (is class-vars None))
+                             ['(import typing [ClassVar :as _doeff-ClassVar])]
+                             [])
+        class-vars (+ (list (or class-vars [])) tag-class-vars))
   (setv written (when (not static-view) (class-form docstring class-vars fields post-init)))
   (_scoped-definition _hy-compiler name
     [`(do
         (import doeff_hy.declarations doeff_hy.record ~@(if (is written None) [] ['doeff_hy.record_runtime]))
+        ~@class-var-import
         ~@(if (and static-view statements) ['(import typing [cast :as _doeff-cast])] [])
```

## 2. 本来の所有者と、侵す契約

**侵す契約(R1)**: R1 は「class の本体の中の defrecord の展開の import が、外の class の局所の名にならない」です。
- 投影では `from typing import ClassVar as _doeff_ClassVar` が `Outer` の本体に残ります。`_synthesized_import`(static_check.py 379 行目)は doeff_hy の module と表の module しか移さないからです。
- 入れ子の `Span` の本体の注記 `__doeff_tags__: _doeff_ClassVar[…]` から、この名は読めません。Python の名の引きは、外の class の段を飛ばします。
- 実行時は `_publish-imports` が module の globals へ置くので動きます。実行時と投影が食い違います。

**所有の分裂**: 「展開の import のうち、入れ子の型や method から届かせる物はどれか」という判断が 2 つの定義元に分かれています。
- 実行時(scoped-definition の `_imports-of`)は、forms の import を全部、形から拾います。
- 投影(projection-imports の `_synthesized_import`)は、module の綴りの表で拾います。
- 新しい import は前者には入り、後者からは落ちます。投影の場所の正しさが、定義の macro が選んだ import の綴りで決まってしまいます。これは「definition-macros は場所を判断せず、文の並びを渡すだけ」と R4「1 か所に寄せる」に反します。

**S1 の主張が成り立たない**: S1 は「別の module の import を足す時だけ表に 1 行足す」と言います。しかし表は別名なしの `import <module>` の形しか拾えず、`from typing import ClassVar as _doeff_ClassVar` を書き表せません。
- 表に `"typing"` を足しても効きません。
- `_synthesized_import` の from-import の枝を typing へ広げると、今度は利用者が関数の中に書いた `from typing import …` まで動かします。これは不変条件「利用者が書いた import は動かさない」に反し、これも列挙の検査では捕まりません。

**補助の名の置き場の迂回**: static_view.py 143–148 行目の註は、型検査の展開の補助の名は `STATIC_HELPER_IMPORTS` で module の頭に 1 度だけ置き、macro は出さない、としています。この差分は定義ごとに import を出すので、この置き場を迂回します。

## 3. 列挙の各検査が拒まない理由(読みに基づく・未実行)

| 検査 | 拒まない理由 |
|---|---|
| 定義の macro の数え上げ | macro の集合が変わらない |
| 局所の名の検 | 関数の中では `_scoped-definition` が包み、新しい import は包んだ関数の中に入る。外の関数の局所は `Span` と `_hy_*` だけ |
| module の状態の検 | 関数の中では publish しない。`typing` は名が doeff で始まる module ではない |
| 入れ子の定義の実行の検 | ClassVar の注記と後からの setattr という形は、defwire の `__doeff_wire__` と同じ道。`defrecord-in-defk-checks-fields` の `(. Span __doeff_tags__ role)` は setattr の値を読むので変わらない |
| class の本体の型の検 | `_CLASS_BODY_TYPE_CASES` の defrecord と defwire は `:tags` を持たないので、展開が変わらない。持っていても `_publish-imports` が `_doeff_ClassVar` を module の globals に置く |
| 投影の検(関数・class) | `_PROJECTION_ROOT_NAMES` は根の名 3 つ(`doeff_hy`・`doeff_core_effects`・`doeff_cluster`)だけを見る。`_doeff_ClassVar` は入らない。`doeff_hy.declarations` の import は module の段へ移る |
| defhandler の検 | defhandler に触れない |
| 品質検査(速い段) | 変わるのは record.hy(include に在る)だけ。module の段の import は増えない。引用の中の `(import typing [… :as _doeff-…])` と同じ綴りは、基底の record.hy の 417・530 行目に既に在る。型は速い段に含まれない。道具の中身は読んでいないので断定はしない |
| semgrep | record.hy に当たる generic の規則(defprogram・旧い defk の handler の形・bang の 2 本・defhandler を module の段に置く規則・WithHandler の shim)は、どれも足した文字列に合わない |

## 4. 最小の再現手順(設計者が実行)

**手順 1(検査)**: 差分を当てた作業木で、列挙の呼び方のとおりに pytest・品質検査(`--base b52770e03`)・semgrep を走らせます。期待は全部通ることです(推測)。

**手順 2(投影の違反を AST で確かめる)**: pyright を使わない確かめ方です。作業木のルートで次を走らせます。

```sh
uv run python - <<'EOF'
import ast, pathlib, tempfile
from doeff_hy.static_check import Projection, project
SOURCE = '''(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(defclass Outer [] (defrecord Span {:tags {:context "t" :role "type"}} #^ int start))
'''
root = pathlib.Path(tempfile.mkdtemp()); src = root / "class_body_tags.hy"; src.write_text(SOURCE)
p = project(root, [root], src); assert isinstance(p, Projection)
tree = ast.parse(p.text)
outer = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Outer")
span = next(n for n in outer.body if isinstance(n, ast.ClassDef) and n.name == "Span")
bound = lambda b: {a.asname or a.name.split(".")[0] for n in b if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
mod = bound(tree.body) | {n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
reads = {n.id for n in ast.walk(span) if isinstance(n, ast.Name)}
print(sorted(bound(outer.body)), sorted((reads & bound(outer.body)) - mod))
EOF
```

期待は次のとおりです。
- 差分の後: `['_doeff_ClassVar'] ['_doeff_ClassVar']`(R1 の違反、`Span` から引けない名)
- HEAD での対照: `[] []`(`:tags` だけの defrecord は、HEAD では投影がきれい)

**手順 3(実行時は動く)**: 同じ SOURCE を tests の `_eval_module` と同じやり方で `hy.eval` し、`Outer.Span(start=1)` と `Outer.Span.__doeff_tags__.role` を読みます。期待は `Outer.Span(start=1)` と `"type"` です。

**手順 4(任意・推測)**: 投影の text を pyright にかけます。期待は `"_doeff_ClassVar" is not defined` です。さらに、`__doeff_tags__` が dataclass の欄と数えられて、`Outer.Span(start=1)` に引数の欠けの赤が出るかもしれません。

## 5. 未確認の前提と不足

- **pyright の挙動は推測**です。入れ子の class の名の引きと、手順 4 の赤が実際に出るかは確かめていません。
- **宣言の数の上限も推測**です。module の段で定義ごとに `_doeff_ClassVar` の宣言が積まれ、static_view.py の註にある「64 を超えると Unknown」に当たる可能性があります。`without_bookkeeping` は `_synthesized_import` の物しか畳まないからです。
- **Python の版**は `.python-version` の 3.14.3t を前提にしています。注記は遅れて評価されます。実行時が動くという読みは、defwire の同じ道が通っている事(設計者の報告「110 passed」)に頼っています。
- **品質検査の道具**(dotfiles の code_quality)の中身は、作業木の外なので読んでいません。
- **列挙の外の検**(defrecord の形の deftests・`.pyi` の一致・doeff-linter の hook)は考えていません。

**HEAD に既に在る同じ穴(読みで確かめた。実行していない)**:
- class の本体の defwire と defeffect は `_doeff_ClassVar`、`:check` つきの defrecord は `_doeff_cast` を、投影で外の class に残しています。列挙の投影の検は根の名しか見ないので通ります。この差分は、その穴を最も普通の形(`:tags` だけ)へ広げるものです。
- `_synthesized_import` は、利用者が関数の中に書いた `from doeff_hy.wire import parse_json` も移します。不変条件「利用者が書いた import は動かさない」に反しています。

**入力どうしの食い違い**: 不変条件「module の globals を変えるのは nested-reads の class の本体だけ」と実装が合いません。関数の中の defk は、初めて呼んだ時に `_install-guard-globals` が補助を module の globals に置きます。検は 2 回目以降しか比べず、検体の header も module の段に defk を持っています。

WAIT: work — 依頼元の次の入力
