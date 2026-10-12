## 結論

検査を通るのに責務を侵す実装を 1 つ作りました。doeff-hy-check が、正本の宣言にある「1 枠の中で並べてよい process の数」(processesPerSlot)を、`take` が返した持ち物から直に読みます。提示された 4 つの検査のうち、code-quality は私の私用の写しで通ることを確かめました。pytest の 3 つは実行しておらず、通るというのは予想です。

## 1. 機能の要求と差分

**要求**: 正本 dotfiles 142108b9f(card ki-f8107ed7ccc8)は宣言に processesPerSlot = 2 を足し、頭の註で「中で file を並べる runner の入口がそれを並走の上限にする」と求めています。doeff-hy-check は 1 枠の中で展開の子を最大 4 本(EXPANDER_LIMIT = 4)と pyright を起こすので、「展開の子を宣言の数までに絞る」という直しは自然に頼まれます。正本自身の `run_held` も `holding.policy` を読んでいるので、それを真似た形です。

差分の全文は `/work/agent-workspaces/a-Q4J090JDNZ7QGHGYNAX1E646PZ/blind-violation.patch` に置きました。対象は c8fd2c26a、変わる file は 2 つです。

```diff
--- a/packages/doeff-hy/src/doeff_hy/check_slot.py
+class SlotPolicy(Protocol):
+    """取った枠の宣言のうち、1 枠の中で並べてよい process の数(宣言の processesPerSlot・…)。"""
+    processes_per_slot: int
+
 class Holding(Protocol):
-    """正本の take が返す持ち物(正本の Holding)— 解く口だけを読む。"""
+    """正本の take が返す持ち物(正本の Holding)— 解く口と、取った枠の宣言を読む。"""
+    policy: SlotPolicy
     def release(self) -> None: ...
 ...(class SlotHeld の中)
+    def width(self, wanted: int) -> int:
+        """… wanted を、取った枠の宣言の processesPerSlot までに絞る(素通しでは wanted)。"""
+        if self.holding is None:
+            return wanted
+        return max(1, min(wanted, self.holding.policy.processes_per_slot))
--- a/packages/doeff-hy/src/doeff_hy/static_check.py(main)
-        closure = project_closure(root, roots, report, cache_dir, expander_limit(expanders_setting()))
+        # 展開の子は枠の中で走る — 宣言の 1 枠の並走の数(processesPerSlot)を越えて起こさない(card ki-f8107ed7ccc8)。
+        closure = project_closure(root, roots, report, cache_dir, slot.width(expander_limit(expanders_setting())))
```

## 2. 本来の持ち主と、侵す契約

- **正本の中身の知識が doeff へ移る**: 正本の Policy の欄の名 `processes_per_slot` と、正本の Holding の属性 `policy` が M-slot に入ります。
  - M-slot は「隠す知識: 正本の方策の中身」と決めており、check_slot の頭の註も「方策の欄の名をここに置かない」と書いています。
  - M-canon は「宣言の鍵と検め — doeff からは中を読まれない」と決めています。
  - code-quality の契約の断片 doeff-hy-check-slot の責務も「read_policy と take を呼ぶだけで、枠の値は持たない」です。
- **判断が 2 か所に分かれる**: 1 枠の中の並走をどう配るか(入れ子を含めて)は、正本の `width_for_child` と env `AI_CHECK_SLOT_PROCESSES` が持っています。正本の規則は「木全体の並走 ≤ 宣言」です。doeff の `width` は別の規則を持ち、入れ子では宣言で絞りません。S5 の「doeff の M-slot は take を呼ぶだけ」も破ります。
- **「口が揃わない時は名乗って素通し」の契約を破る**: 新しく読む 2 つの名は CANON_CALLS・CANON_ERRORS に無いので、`unfit_names` が検めません。正本でこの名が変わると、doeff は素通しせずに落ちます。
- **S2 の主張が崩れる**: 「正本の宣言の形が変わっても、doeff で変わるのは M-slot-scene の宣言だけ」が成り立たなくなります。

## 3. 各検査が拒まない理由

- **M-place-guard**: 足した行に `__file__`・`__cached__` が無いので、拒む行がありません。この検の判定式を写し、変えた後の doeff_hy の全 file に当てると、表 PLACE_LINES の外の行は 0 件でした(式の写しは実測、pytest としては未実行)。
- **M-budget-test**: doeff-hy-pytest には触れず、doeff_hy にも import の時の振る舞いを変えない class と method を足すだけです。結果は c8fd2c26a と同じになる見込みです(未実行)。
- **M-slot-scene**:
  - AST の検 `test_the_canon_policy_goes_straight_into_take` は、`read_policy` の答えが `take` の第 1 引数へ直に渡るかという「入る向き」だけを見ます。`take` の答えから方策を読み戻す「出る向き」は見ません。判定式を写して当てると `module.read_policy()` は直に渡っていました(式の写しは実測)。
  - 正本を使う 3 本は、宣言 K = 1・processesPerSlot = 1 なので width は 1 か素通しの 4 になり、probe.hy 1 file では並べないので通る見込みです。
  - 口だけの正本(INTERFACE_ONLY_CANON)を使う検は `take_slot` だけを呼び、`width` を呼ばないので変わりません。
  - 以上は未実行の予想です。
- **code-quality(実測)**: 私用の作業場に c8fd2c26a を detached で取り出し(alternates で object を読むだけで、共有の repo は変えていません)、差分を未 commit のまま当てて `--scope changed --base c8fd2c26a --no-cache` で走らせ、`passed`・rc 0 でした。
  - fast の段の規則は、Any や cast・動的 import・型注釈の欠け・抑制コメント・import の依存の方針だけで、どれにも当たりません。
  - import は足していません。static_check.py は include の外なので登録の案内が出ただけで、check_slot.py は測られています。

## 4. 最小の再現

c8fd2c26a の作業木に `git apply <上の patch>` を当てた状態で行います。

**検査が通る側**
1. `uv run pytest packages/doeff-hy/tests/test_static_cache.py::test_doeff_hy_sources_do_not_read_their_place` → passed(予想)
2. `uv run pytest packages/doeff-hy-pytest/tests/test_budget.py::test_report_mode_warns_and_lists_but_stays_green`(`-W ignore::pytest.PytestWarning` を付けた形でも)→ c8fd2c26a と同じ結果(予想)
3. `uv run pytest packages/doeff-hy/tests/test_static_check_slot.py`(~/dotfiles の正本が在る機体)→ 8 本とも passed(予想)
4. code-quality の changed の走り → passed(私用の写しで実測)

**責務違反の側(pytest は要りません)**

準備として、一時の home T に正本 machine_check_slots.py の写しと、宣言 `{"slots":1,"processesPerSlot":2,"dir":"<T>/slots","waitLimitSeconds":5}` を置きます。次の probe.py は `take_slot(Path.home(), …)` を呼び、`AI_CHECK_SLOT_PROCESSES` と `width(4)` を印字します。
- (a) `HOME=T python probe.py` → `width(4) = 2`。doeff が宣言の値を `Holding.policy` から読んでいます(実測)。
- (b) `HOME=T python T/dotfiles/agent/tests/machine_check_slots.py run --label outer -- python probe.py` → 正本が子へ配った値は `2` なのに、holding は None で `width(4) = 4`(実測)。包みの内側の doeff-hy-check は、宣言が 2 の枠で展開の子 4 本と pyright を走らせます。
- (c) 正本の写しを `sed s/processes_per_slot/processes/g` で変えます。宣言の JSON の鍵は変えず、正本自身の `run` は子へ 2 を配って動きます。
  - この宿で (a) を撃つと `AttributeError: 'Policy' object has no attribute 'processes_per_slot'`(実測)。
  - main ではこの呼びが `except (OSError, RuntimeError, ValueError)` の try の中にあるので、AttributeError は抜けて exit 1 になるはずです。これは「新しい赤」と見分けがつかない終わり方です(code を読んだ推測で、main は未実行)。
  - c8fd2c26a の doeff には `processes_per_slot` を読む所が 0 か所なので、同じ正本の変更では何も起きません(git grep で実測)。

## 5. 未確認の前提・推測と実測の区別

- **pytest は実行していません**(依頼どおり)。3 つの pytest の検が通るというのは予想です。そのうち 2 つは、検の判定式を写して当てた結果で、pytest そのものの結果ではありません。
- **M-budget-test が c8fd2c26a で緑であることは確かめていません**(design-pre-blind.md の主張に依っています)。正本の無い機体では M-slot-scene は skip になります。
- **code-quality と ruff について**: blind-input は code-quality に「ruff ほか」が含まれると書いていますが、入っている code-quality の source に ruff を撃つ所は見つかりませんでした。doeff の契約の tools も pyright だけで、fast の段では撃ちません。参考までに ruff 0.15.21(共有の venv)を変更の前後で当てると、どちらも main に元からある PLR0912・PLR0915 の 2 件だけで、新しい所見はありませんでした。
- **再現 (a)〜(c) の環境**: 共有の `~/repos/doeff/.venv` の python に `PYTHONPATH`(私用の写し)と `PYTHONDONTWRITEBYTECODE=1` を付けて実行しました。ほかの package の版は c8fd2c26a と揃っていないかもしれませんが、この道は標準 library と正本の写しだけを使います。正本は dotfiles fed74e9fe の時点の物です。
- **評価していない物**: 提示に無い pyright(日次)と doeff-linter は推測していません。

**探索した範囲**: ほかに 2 つの候補を見ましたが、具体化・検証はしていません。
- budget.py の `_record` で `catch_warnings` と `simplefilter("always", BudgetWarning)` を使い、利用者の警告の filter を上書きする形。M-budget の「filter に触れない」を侵し、M-budget-test は通りそうです。
- `module.__spec__.origin` や `inspect.getfile` で正本の置き場を読む形。強制方法の表が限界として既に挙げており、自然な機能の追加にもならないので選びませんでした。

私が作った作業場の写しと再現用の dir は消しました。残したのは上の patch だけで、共有の作業木と dotfiles は変えていません。

WAIT: work — 依頼側の次の指示
