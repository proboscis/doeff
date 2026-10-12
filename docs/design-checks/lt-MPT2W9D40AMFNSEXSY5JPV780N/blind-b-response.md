## 結論

8a759a421 の上で、失敗例を 1 つ組みました。提示された 4 つの検査はどれも通りました(実測)。一方で、どの import を macro が書いたかという判断が expansion-names から static_check へ移ります。その結果、実行すると NameError で落ちるコードを、型検査(pyright)が黙って通すようになります(実測)。

## 1. 機能要求と差分

**機能要求(実在する残りの不具合)**: class の本体に書いた defeffect・defwire を型検査用の Python に写すと、pyright が `"_doeff_ClassVar" is not defined`・`"_doeff_cast" is not defined` を出します。要件 2 で直した defhandler と同じ形の不具合です。今の版で実際に 3 件出ました。原因は、`from typing import ClassVar as _doeff_ClassVar` などが `is_alias_import` に当たらず、class の本体に残ることです。

**差分**(`packages/doeff-hy/src/doeff_hy/static_check.py` だけを変えます。全文は `/tmp/tmp.3s0OhDj5PW/proposal.diff`):
```diff
-from doeff_hy.expansion_names import is_alias_import
+from doeff_hy.expansion_names import PREFIX
@@ _synthesized_import(docstring に「`_doeff_` の頭の別名の from-import は macro の合成 — 書き手は `_doeff_` の名を import しない(expansion_names.py の頭注)」を追記)
         case ast.ImportFrom(module=str(module), level=0, names=names):
-            return _doeff_hy_module(module) or all(
-                is_alias_import(module, alias.name, alias.asname) for alias in names
-            )
+            return _doeff_hy_module(module) or all(_macro_alias(alias) for alias in names)
+
+def _macro_alias(alias: ast.alias) -> bool:
+    """macro が `_doeff_` の頭の別名で束ねた名か(handle・defhandler の node と、defeffect・defwire の型の本体が読む名)。"""
+    return alias.asname is not None and alias.asname.startswith(PREFIX)
```
普通の実装者がこう書きそうな理由は 3 つあります。
- 名前を表へ足す正規の直し方は重いです。`_doeff_effect_base` は `alias("EffectBase")` の形に合わないので、macros.hy・declarations.hy・record.hy の綴りを実行時の展開ごと変えることになり、要件 3 に触れます。
- 頭注と依頼の前提が、この 1 行の根拠を与えています。
- `PREFIX` は誰でも使ってよい公開の定数です。

## 2. 本来の所有者と、破る契約

- **判断が移る**: 「この import は macro が合成した別名か」に答えるのは expansion-names の `is_alias_import` です(表の名・置き場・別名)。static-check-postprocess は綴りの具体を知らない側のはずです。差分の後は static_check が綴り(`_doeff_` で始まるか)から自分で決め、`is_alias_import` に問いません。「macro の別名」の定義が 2 つ目の場所にでき、defeffect-form と defwire が手で書く `_doeff_ClassVar` などの綴りも、持ち主に問わずに取り込みます。
- **前提が実際には成り立たない**: for/do と traverse の docstring(macros.hy:1618-1620・1651-1654)は、書き手に `(import doeff [do :as _doeff-do])`・`(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])`・`(import doeff_traverse [Skip :as _doeff_traverse_Skip])` を書かせます。tests/test_nested_definition_scope.py:81 や examples も実際に書いています。綴りで決めると、書き手の書いた文を macro の合成と取り違えます。
- **破られる公開契約**:
  - static_check.py:381 の「利用者の重複の import は赤のまま残す」と、:541 の「利用者が書いた式は外さない」が破られます。
  - 書き手が class の本体に置いた import が module の段へ移るので、型検査が見る名の範囲と実行時の名の範囲が食い違います。

## 3. 各検査が拒まない理由

- **契約テスト 1**: 実行時の展開しか動かさず、static_check を通りません。差分を当てても 5 件とも通りました(実測)。
- **契約テスト 2**: 調べるのは `_doeff_Pass`・`_doeff_WithHandler` が scope の中で束ねられず、module の段で束ねられることだけです。綴りで決める規則はそれより広いので、この 2 つも移します。書き手の import が残るかは調べません。2 件とも通りました(実測)。
- **code-quality fast**: 8a759a421 の写しに差分を当てて流し、rc=0・status passed・findings 0 でした(実測)。static_check.py は `registration_needed`(include に無い)に載りましたが、fast の段では止めません。
  - 依存 static-check → expansion-names は module_extensions で許されています。expansion_names.py は丸ごと public です。`_macro_alias` は非公開の関数で注釈もあり、untyped-boundary は公開関数しか見ません(設定とコードを読んだ上での推論)。
  - file が include の外なので、これらの規則がこの file を実際には測っていない可能性があります(推論)。
- **semgrep**: semgrep 1.169.0(uv.lock と同じ版)に `.semgrep.yaml` を当て、変更前・変更後とも results 0・errors 0 でした(実測)。hook の基準値(`scripts/hook_finding_baseline/semgrep.json`)に static_check.py の行は無く、所見は増えていません。

## 4. 確かめる手順と結果

検体はすべて `/tmp/tmp.3s0OhDj5PW` にあります。共有の木は変えていません。差分は `apply_patch.py` が、patched/ の関数をそのまま読み込んだ module へ入れ直して当てます。

1. 2 つの契約テスト(コピーした test file。中身は木の物と同じ):
   `cd tests_orig`(または `tests_patched`)して `uv run --project /work/toolset/worktrees/doeff-wt-class-body-name pytest -p no:cacheprovider -q test_nested_definition_scope.py::test_class_body_type_methods_read_their_own_class test_nested_definition_scope.py::test_static_projection_of_nested_defhandler_reads_module_level_nodes`
   → どちらも **7 passed**(実測)。
2. code-quality: `snap/`(8a759a421 の archive に差分を当てた物)に `code-quality --repo snap --scope changed --base-repo <設計検証の木> --base 8a759a421 --paths-file changed.json --format json --no-cache` を流す → **passed**(実測。`cq_patched.json`)。
3. 機能が直ること: `demo_project.py <orig|patched> out_<mode>/<名> samples/feature_class_body_types.hy` で写し、pyright を strict で当てる → reportUndefinedVariable が **orig 3 件 → patched 0 件**(実測)。
4. **責務違反の害**: `samples/author_class_body_import.hy`。書き手が `_doeff_traverse_Traverse` の import を class の本体に置き、method の中で for/do を使います。

   | 確かめ方 | 今の版 | 差分の後 |
   |---|---|---|
   | 実行(`demo_runtime.py`) | `NameError: name '_doeff_traverse_Traverse' is not defined` | 同じく NameError |
   | 写した Python の中の import | class の本体に残る | module の段へ移る |
   | pyright | 13:16 で `"_doeff_traverse_Traverse" is not defined` | その error が消える |

   いずれも実測です。実行すると落ちるのに、型検査は通るようになります。
5. 補足 `samples/author_duplicate_import.hy`: 書き手が書いた同じ import 2 行が、patched では 1 行に畳まれました(実測)。ただし pyright の strict は、どちらの版でもこの重複に診断を出しませんでした(実測)。したがって害は契約の上だけで、診断の違いは確かめられていません。

## 5. 確かめていない前提と、足りない情報

- pyright は写した .py に直に当てました(一時置き場の strict 設定)。doeff-hy-check そのもの(設定・基準値の分け方・Hy の行への写し戻し・ProcessPool の子 process)は通していません。子 process には入れ直した関数が届かないため、測ったのは同じ process の中の `project()` だけです。doeff-hy-check でも同じ診断になるかは推測です。
- semgrep は hook の入口(`hook_finding_baseline.py check semgrep`)ではなく、同じ版を直に流しました。hook に並ぶ doeff-linter は提示された検査に入っていないので、評価していません。
- 書き手が class の本体に必要な import を置くことがどれほどあるかは、根拠がありません(推測)。
- `_doeff_ClassVar`・`_doeff_cast`・`_doeff_dataclass`・`_doeff_effect_base` の綴りの持ち主は、責務の表のどこにも割り当てられていません。表は expansion-names を handle・defhandler の node に限っています。どこを正しい置き場とするかは、足りない情報です。
- 別に見つけた事(実測): 今の版でも `is_alias_import("doeff", "do", "_doeff_do")` は True を返します。`home()` が知らない名を全部 `doeff` とみなすためで、for/do が書き手に書かせる `(import doeff [do :as _doeff-do])` を macro の合成と答えます。範囲は狭いですが、前提とのずれは 8a759a421 の時点ですでにあります。ただし `_doeff_do` は型検査用の補助の import でも module の頭に置かれるので、診断の違いとして表れるかは確かめていません。

WAIT: work — 呼び手の次の依頼
