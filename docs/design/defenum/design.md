# `defenum` — 文字列の Enum を 1 行で建てる Hy の macro

- 日時: 2026-09-26
- 決めた人: Claude Opus 5.5(作業 branch `wt/defenum`)。どれも後から戻せる決定なので、推奨を 1 つ選んで決めた。main へ入れるかは operator が見てから決める。
- 出自: operator 2026-09-26 "and perhaps we should use macro for enum"
- 実装: `packages/doeff-hy/src/doeff_hy/record.hy`(`defrecord` の隣)
- テスト: `packages/doeff-hy/tests/test_defenum.py`

## 使い方

```hy
(require doeff-hy.record [defenum])
(import enum [StrEnum])

(defenum PlacementMismatch KEY CONVERSATION KIND PHASE CANCELLED GUARANTEED)
(defenum Phase (PENDING "Pending") (RUNNING "Running"))   ; 値を明示する形
```

展開の結果:

```hy
(defclass PlacementMismatch [StrEnum]
  (setv KEY "key" CONVERSATION "conversation" KIND "kind"
        PHASE "phase" CANCELLED "cancelled" GUARANTEED "guaranteed"))
```

## 決めたこと

### 1. 値は member の名前から作る。小文字にし、`_` と `-` を `-` にそろえる(kebab-case)。明示の値 `(NAME "値")` も許す

- `KEY` → `"key"`、`IN-PROGRESS` / `IN_PROGRESS` → 属性 `IN_PROGRESS`・値 `"in-progress"`。
- `(RUNNING "Running")` は値をそのまま使う。Kubernetes の phase のように、外の約束で綴りが決まっている値のため。
- 連番(`auto()` の整数)は採らない。JSON に書いた時に意味が読めず、member の並べ替えで値が変わる。

理由(綴りの数え方と内訳): agora-controllers と agent-control-plane の追跡中の `.hy` 全部(695 本 / 289 本)で、`kind` / `phase` / `state` / `status` / `reason` / `type` / `mode` / `verdict` / `outcome` / `result` / `role` / `decision` / `cause` / `category` / `stage` / `level` / `severity` / `action` の欄(`"kind" "…"` と `:kind "…"` の形)に書かれた文字列の値を綴りの形で分けた。数は異なる値の種類数。

| 綴りの形 | agora-controllers | agent-control-plane |
|---|---|---|
| 小文字 1 語(`key`) | 228 | 109 |
| kebab-case(`budget-exhausted`) | 75 | 41 |
| snake_case(`conversation_input_result`) | 62 | 3 |
| PascalCase(`Running`) | 47 | 3 |
| camelCase | 11 | 7 |

- 一番多いのは小文字 1 語で、これは kebab でも snake でも同じ綴りになる。
- 2 語以上の値では、snake_case の 62 種のうち 59 種は `type` 欄(websocket の通信の種類の名前 `conversation_*`)で、Enum にする閉じた値の集合とは性格が違う。`type` 欄を除くと agora-controllers は kebab 72 対 snake 5、agent-control-plane は kebab 41 対 snake 3(snake の 3 は Anthropic API 由来の `tool_use` / `tool_result` と argparse の `store_true`)。
- `reason` 欄は両方とも kebab だけ(47 / 34)。
- PascalCase は Kubernetes の condition の type・reason・phase(`Running` / `Pending`)で、外の約束の綴り。これは明示の値で書く。

だから既定は kebab-case、外の約束で綴りが違う値だけ明示にした。

### 2. 基底は `enum.StrEnum`(Python 3.11 以上)

- 値が `str` なので `json.dumps` にそのまま渡せ、`str(member)` と f-string も値の綴りになる(`(str, Enum)` の多重継承は 3.11 以降 `str()` が `Cls.NAME` を返すので、ログや文字列の組み立てで綴りが化ける)。
- doeff-hy の `requires-python` は `>=3.10` のままだが、`enum.StrEnum` は 3.11 から。展開は `defrecord` の `dataclass` と同じく使う側の scope の `StrEnum` の名前を指すので、3.10 で使う人は自分で同じ名前の基底を用意すれば動く。doeff-hy に 3.10 用の互換の class は置かない — doeff-hy の Hy の module には型の宣言(`.pyi`)が無く、そこから import した基底は型検査で型不明になり、member の網羅が効かなくなるため。実際の実行環境は 3.14(`.python-version`)。
- Hy は 1.3.0(uv.lock)。

### 3. `match` と型検査の網羅

- 実行時: `(match x Cls.KEY … Cls.CONVERSATION …)` で分岐できる(テスト `test_match_dispatches_on_members`)。
- 静的: 最後に `_ (assert-never x)` を置くと、doeff-hy-check(展開を pyright にかける)が漏れを捕まえる。1 つ抜くと `Argument of type "Literal[PlacementMismatch.GUARANTEED]" cannot be assigned to parameter "arg" of type "Never"`(reportArgumentType)が抜けた行に出る。全部書いた形は 0 件(テスト `test_exhaustive_match_passes_the_static_check` / `test_missing_member_is_caught_by_the_static_check`)。

### 4. 展開の時に拒む形

Enum が黙って別名にしたり読み違えたりする形を、展開の時に止める。

- member が 1 つも無い
- member の名前が `[A-Za-z][A-Za-z0-9_-]*` でない(`_A` のような `_` 始まりは Enum が member にしない)
- 同じ名前が 2 度(`IN-PROGRESS` と `IN_PROGRESS` は Python の名前が同じなので重なり)
- 同じ値が 2 度(`A-B` と `(C "a-b")` など。StrEnum は後の方を黙って別名にし、member の数が減る)
- member が `NAME` か `(NAME "値")` 以外の形

### 5. `defenum` は template macro ではない(値を展開の時に計算する)

`defrecord` は quasiquote 1 つだけの template macro だが、`defenum` は同じ形では書けない。quasiquote の逐語の置換では `KEY` から `"key"` を作れず、また各 member の後ろに値を差し込む(並びを交互に組む)こともできない。template で書ける形は `(defenum X KEY "key" CONVERSATION "conversation")` のように使う側が値を全部書く形だけで、これは operator の示した使い方と違い、綴りを 2 度書かせる。

だから `defenum` は展開の時に計算する macro にし、計算は macro の本体の中にだけ置いた(別の関数に切り出すと、展開の時に呼ばれる関数は Program を返す defk にできず defn になるため)。

置き場は `defrecord` と同じ `record.hy`。新しい file は足していない(`record.hy` は ADR-DOE-HY-005 の macro の所有者の名簿に在る)。

### 6. 共通の品質検査の側に要る変更(このリポジトリでは変えていない)

共通の品質検査(`~/repos/code-quality`、旧 `~/dotfiles/agent/quality/`)は macro を実行せず、展開の規則を検査器の側に持つ。今の検査器では次の 2 つが起きる。

- `record.hy` 自身: 宣言されていない `defmacro` として投影できず、検査が incomplete になる(`code-quality-unexecuted: packages/doeff-hy/src/doeff_hy/record.hy: hy:89: 安全な静的投影の対象外の core macro: defmacro`)。変更前の main では `record.hy` は通っている。
- 使う側: `(require doeff-hy.record [defenum])` が「require の macro は未対応」になり、その file は型の投影の外に落ちる。

検査器に要る変更(下の diff。`~/repos/code-quality` の HEAD `ad8baff` へ `git apply` で当たることを確かめた):

1. `quality/hy_record.py`: `RECORD_MACROS` に `defenum` を足し、展開の規則 `enum_declaration` を足す(値の作り方は `record.hy` の現物と同じ規則)。
2. `quality/hy_projection.py`: 使う側で `defenum` を `enum_declaration` で展開する。
3. macro の宣言の種類(`MacroKind`)に `"fixed"` を足す: 「展開の規則は検査器の固定表が持つ、計算する macro」。定義している file では現物を検証せず、名前が固定表に在ることだけ確かめて投影から落とす。

検査器が出た後で、doeff の `.agents/code-quality.json` の `doeff-hy-record` の `macros` に `"defenum": "fixed"` を足す(今の検査器は `"fixed"` を知らず契約の読み込みで落ちるので、この branch では足していない)。

この diff を一時の複製に当てて確かめたこと:

- 契約に `"defenum": "fixed"` を一時的に足した doeff の作業木で `code-quality --scope changed --base origin/main` が `passed`(`record.hy` の incomplete が消える)。
- 使う側 `(defenum PlacementMismatch KEY CONVERSATION IN-PROGRESS (RUNNING "Running"))` の投影が次の Python になり、所見・欠測とも 0:

```python
from enum import StrEnum

class PlacementMismatch(StrEnum):
    KEY = 'key'
    CONVERSATION = 'conversation'
    IN_PROGRESS = 'in-progress'
    RUNNING = 'Running'
```

<details>
<summary>検査器への diff(code-quality ad8baff 基準)</summary>

```diff
diff --git a/quality/contract.py b/quality/contract.py
--- a/quality/contract.py
+++ b/quality/contract.py
@@ -93,9 +93,11 @@
 
 def macro_kind(value: object) -> MacroKind:
     """macro の書かれ方は閉語彙。語彙外の宣言は読まずに拒否する。"""
-    if value != "template":
-        raise ValueError(f"未知の macro kind: {value!r}")
-    return "template"
+    if value == "template":
+        return "template"
+    if value == "fixed":
+        return "fixed"
+    raise ValueError(f"未知の macro kind: {value!r}")
 
 
 def parse_macros(value: object) -> tuple[MacroDecl, ...]:
diff --git a/quality/hy_projection.py b/quality/hy_projection.py
--- a/quality/hy_projection.py
+++ b/quality/hy_projection.py
@@ -35,7 +35,7 @@
     test_function,
 )
 from quality.hy_effects import effect_function, expose_programs
-from quality.hy_record import RECORD_MACROS, record_declaration
+from quality.hy_record import RECORD_MACROS, enum_declaration, record_declaration
 from quality.hy_macro import (
     MacroCallError,
     MacroContractError,
@@ -109,6 +109,8 @@
     for form in read_forms(source.text, source.path):
         if defined_macro(form) != name:
             continue
+        if declaration.kind == "fixed":
+            raise MacroContractError(origin, f"宣言 {declaration.name} は検査器の固定表の規則で、template ではない")
         if declaration.kind != "template":
             assert_never(declaration.kind)
         return template_macro(form, name, source.path)
@@ -232,6 +234,12 @@
                 own: MacroSource | None = self.macros.get(self.source.path)
                 defined: str = defined_macro(node)
                 declaration: MacroDecl | None = declared(own, defined) if own is not None else None
+                if declaration is not None and declaration.kind == "fixed":
+                    # 検査器の固定表が展開の規則を持つ macro(計算する macro)。現物は検証せず、
+                    # 名前が固定表に在ることだけ確かめて投影から落とす。
+                    if defined not in {hy.mangle(item) for item in RECORD_MACROS}:
+                        raise MacroContractError(node, f"固定表に無い macro を fixed と宣言している: {defined}")
+                    return expression(node, "do", ())
                 if declaration is not None:
                     # 宣言済みの定義は現物を検証して型検査の投影からは落とす。
                     # 引数の型は使用側でしか決まらないので、本体は著者側投影が別に判定する。
@@ -256,6 +264,8 @@
             if macro in RECORD_MACROS:
                 if not top:
                     raise DeclarationError(node, "入れ子の defrecord は型投影未対応", True)
+                if macro == "defenum":
+                    return self.lower(enum_declaration(node), top)
                 return self.lower(record_declaration(node), top)
             if macro or name in MACROS:
                 raise DeclarationError(node, f"宣言の位置/由来/機能が未対応: {name}", True)
diff --git a/quality/hy_record.py b/quality/hy_record.py
--- a/quality/hy_record.py
+++ b/quality/hy_record.py
@@ -11,13 +11,16 @@
 
 from __future__ import annotations
 
-from hy.models import Expression, Keyword, List, Object, Symbol
+import re
 
+import hy
+from hy.models import Expression, Keyword, List, Object, String, Symbol
+
 from quality.hy_dsl import DeclarationError
 from quality.hy_syntax import expression, items
 
 #: doeff-hy が所有する記録の足場の macro 名。増やす時はここと doeff-hy を同便で。
-RECORD_MACROS: frozenset[str] = frozenset({"defrecord"})
+RECORD_MACROS: frozenset[str] = frozenset({"defrecord", "defenum"})
 
 
 def record_declaration(node: Object) -> Expression:
@@ -41,3 +44,48 @@
     )
     decorators.replace(node, recursive=False)
     return expression(node, "defclass", (decorators, parts[1], List(()), *parts[2:]))
+
+
+MEMBER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
+
+
+def enum_declaration(node: Object) -> Expression:
+    """`(defenum Name MEMBER... (MEMBER "値")...)` を StrEnum の defclass へ写す。
+
+    doeff-hy の現物(`doeff_hy/record.hy` の defenum)と同じ規則: 名前から作る値は
+    小文字にして `_` を `-` にそろえる。明示の値はそのまま。重なりは拒む。
+    """
+    parts: tuple[Object, ...] = items(node)
+    if len(parts) < 2 or not isinstance(parts[1], Symbol):
+        raise DeclarationError(node, "defenum の第 1 引数は enum の名前ちょうど")
+    if len(parts) < 3:
+        raise DeclarationError(node, "defenum に member が 1 つも無い")
+    pairs: list[Object] = []
+    names: set[str] = set()
+    values: set[str] = set()
+    for member in parts[2:]:
+        key: Object
+        value: str
+        if isinstance(member, Symbol):
+            key, value = member, str(member).lower().replace("_", "-")
+        elif (isinstance(member, Expression) and len(member) == 2
+              and isinstance(member[0], Symbol) and isinstance(member[1], String)):
+            key, value = member[0], str(member[1])
+        else:
+            raise DeclarationError(member, "defenum の member は NAME か (NAME \"値\") ちょうど")
+        if not MEMBER_NAME.fullmatch(str(key)):
+            raise DeclarationError(member, "defenum の member の名前は [A-Za-z][A-Za-z0-9_-]* ちょうど")
+        mangled: str = hy.mangle(str(key))
+        if mangled in names or value in values:
+            raise DeclarationError(member, "defenum の member の名前か値が重なっている")
+        names.add(mangled)
+        values.add(value)
+        symbol: Symbol = Symbol(mangled)
+        symbol.replace(member, recursive=False)
+        literal: String = String(value)
+        literal.replace(member, recursive=False)
+        pairs.extend((symbol, literal))
+    body: Expression = expression(node, "setv", tuple(pairs))
+    bases: List = List((Symbol("StrEnum"),))
+    bases.replace(node, recursive=False)
+    return expression(node, "defclass", (parts[1], bases, body))
diff --git a/quality/model.py b/quality/model.py
--- a/quality/model.py
+++ b/quality/model.py
@@ -6,7 +6,7 @@
 from typing import Literal
 
 #: macro の書かれ方の閉語彙。増やす時は投影側(hy_macro の検証器と展開器)も同時に増える。
-MacroKind = Literal["template"]
+MacroKind = Literal["template", "fixed"]
 
 
 @dataclass(frozen=True)
```

</details>

## 戻し方

- `defenum` をやめる: `record.hy` の `defmacro defenum` とその頭注の節、`packages/doeff-hy/tests/test_defenum.py`、この文書を消す。使っている所が在れば、展開の結果(`(defclass X [StrEnum] (setv …))`)を手で書いた形に置き換える — 展開は 1 対 1 なので機械的に戻せる。
- 値の綴りを snake_case に変える: `record.hy` の `(.replace (.lower (str member)) "_" "-")` を `(.replace (.lower (str member)) "-" "_")` にし、検査器の `enum_declaration` も同じに直す。ただし既に JSON に書き出した値が在れば、読み手の側の綴りも移す必要がある(ここだけは使い始めた後だと戻す費用が上がる)。
- 基底を `(str, Enum)` に変える: 展開の `StrEnum` を差し替える。`str(member)` の綴りが変わる点に注意。
