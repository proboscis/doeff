失敗例が 1 つ見つかりました。提示された 2 つの検は変更後も 97 passed(実測)のまま通ります。一方で、関数の中の defeffect は外の関数を呼ぶたびに effect 型を別 module のプロセス全体の索引に積み増します。変更は作業木に当てたまま残しています(`packages/doeff-hy/src/doeff_hy/declarations.hy` に +16 行、commit はしていません)。

## 1. 機能要求と差分

**要求(仮定)**: 「`:tags` の文脈(context)ごとに、defeffect で定義した effect 型を module を名指さずに引ける索引がほしい。一覧の道具や handler の網羅の検に使う」。declarations.hy の冒頭の註にある「静的検査と一覧の道具が読めるようにする」の延長として、普通に出てきそうな要求です。設計書の S1 が言うとおり、defeffect-form が返す form の中に足します。

差分(`/tmp/lt0YB1-blind-b/violation.diff`)のうち、declarations.hy に足した物:
```hy
(setv EFFECT-INDEX {})

(defn #^ tuple effects-in-context [#^ str context]
  (.get EFFECT-INDEX context #()))

(defn #^ type register-effect [#^ type effect-type]
  (setv context (. effect-type __doeff_tags__ context))
  (setv (get EFFECT-INDEX context) (+ (effects-in-context context) #(effect-type)))
  effect-type)
```
defeffect-form が返す form に足した 1 行:
```hy
     (setattr ~name "__doeff_defeffect__" True)
+    (doeff_hy.declarations.register-effect ~name)
```

## 2. 本来の所有者と、侵す契約

- **侵す契約**
  - M4 の守る事: 「返す form は M2 に渡せる(定義して名に束ねるだけ)」
  - M3 の守る事: 「`forms` は module の globals に記録を積まない」。積み先は `doeff_hy.declarations` という module の globals です。
  - `_scoped-definition` の docstring にある呼び手の負う事: 「関数の中では `forms` は外の関数を呼ぶたびに評価される…記録を積む文を置かない(呼ぶたびに積み増す)」
  - S4 の主張: 「呼びごとの状態はすぐ呼ぶ関数の局所に閉じ、呼びの間で分け持つ物が無い」
- **判断の移動**: 「この文の並びが module の段で 1 度だけ走るか、外の関数の呼びごとに走るか」の判断は M1(`_in-function-body?`)が持ちます。登録のように 1 度だけ走る前提の記録を出すかどうかは、deftest(M5 の決め)が M1 に問うて決めています。この変更は M1 に問わずに登録を無条件で置くので、「defeffect は常に 1 度だけ評価される」という誤った定数の判断を M4 が持つことになります。
- **状態と effect**
  - 責務表のどこにも持ち主の無い、プロセス全体の可変の状態(EFFECT-INDEX)が増えます。寿命は process で、M5 の記録の寿命(module)より長くなります。
  - M2 の包みの下では、外の関数を呼ぶたびに別 module の状態を書き換える effect が実行時に走ります。

## 3. 各検が拒否しない理由(すべて実測で緑)

| 検 | 拒否しない理由 |
|---|---|
| `test_every_definition_macro_has_a_nested_sample` | macro を足していないので、名の集まりは変わりません |
| `test_function_with_only_a_definition_binds_only_its_names` | 呼びに使う `doeff_hy` は、包みの関数の中の既存の `(import doeff_hy.declarations)` が束ねます。外の関数の局所の名は `{"Inner"}` のままです(実測 [2]) |
| `test_nested_definition_runs`・`test_outer_function_binds_no_helper_name` | 答えも局所の名も変わりません |
| `test_nested_definition_leaves_module_globals_unchanged` | `_module_globals_view` が読むのは定義を書いた module の `vars(mod)` だけで、`doeff_hy.declarations` は見ません(実測 [1]) |
| `test_class_body_definition_runs_without_module_definitions` | 検体に defeffect がありません |
| `test_module_and_class_level_definitions_are_not_wrapped` | module の段の TopEffect は登録されますが、`__qualname__` と repr は変わりません |
| test_handle_scope.py の全部 | defeffect を使っていません(grep で 0 件) |

## 4. 確かめ方と結果(作業木を cwd に)

1. **変更なしで検を走らせる**: `uv run --frozen pytest tests/test_nested_definition_scope.py tests/test_handle_scope.py -q -W ignore` → 97 passed(実測 46.82s)。
2. **差分を当てて同じ命令を走らせる**: 97 passed(実測 39.07s・`checks_after_change.log`)。
3. **違反の実測**: `uv run --frozen python /tmp/lt0YB1-blind-b/demo_violation.py`(検の file の `_CASES`・`_eval_module`・`_module_globals_view`・`_DEFINITION_SAMPLES` をそのまま使います)。結果は `demo_after_change.log` に在ります。
   - [1] `defeffect-in-defk-builds-instance` を 2 回呼ぶと、定義を書いた module の globals の眺めは同じ(True)ですが、索引の文脈 `t` は 0 → 2 に増えます。
   - [3] 次を 1000 回呼ぶと、gc の後も 1000 個の相異なる Inner 型が生き残ります。qualname は `make_effect.<locals>._hy_anon_3.<locals>.Inner` で、module からは引けません(`hasattr(mod3,'Inner')=False`)。
     ```hy
     (defn make-effect [n]
       (defeffect Inner {:fields [(: x int)] :answer int :tags {:context "per-call" :role "intent"}})
       Inner)
     ```
4. **比べ**: `git apply -R violation.diff` で外して同じ script を走らせると、[3] で生き残る型は 0 でした(`demo_baseline.log`)。確かめた後に差分は当て直してあります。

## 5. 確かめていない事・足りない情報

- **推測**: 設計者が M3 の「module の globals」を定義を書いた module に限ると読むなら、この例は M3 の字面ではなく、M4 の「定義して名に束ねるだけ」と S4 に反する例として数えることになります。
- **推測**: 索引の機能要求そのものは仮定で、実在する依頼ではありません。
- **未実測**: 並べて呼んだ時の更新の取りこぼし(dict の tuple を読んで書き直す間に鍵が無い)。
- **未実測**: module の読み直し(reload・`_eval_module` の繰り返し)で、module の段の defeffect も重複して載ること。
- **未実行**: 型検査のための展開でも `register-effect` の呼びが残ります。`declarations.pyi` に宣言が無いため pyright がどう扱うかは確かめていません。この repo の決まりでは .pyi は static_stub で作り直すものですが、作り直していません。
- **走らせていない物**: 提示の 2 file 以外の検(tests の他の file・test_generated_stubs・doeff-hy-check・linter・semgrep・全数の回)。これらが通るかどうかは主張しません。
- **実測で確かめた事**: DOEFF_HY_CODE_STORE の古い bytecode に邪魔されていないこと。変更ありの回では EFFECT_INDEX が在り、外した回では無いことを script の [mode] の行で見ました。

置き場の file(`/tmp/lt0YB1-blind-b/` の中):
- violation.diff
- demo_violation.py
- demo_after_change.log
- demo_baseline.log
- checks_after_change.log

WAIT: work — 依頼元の次の依頼
