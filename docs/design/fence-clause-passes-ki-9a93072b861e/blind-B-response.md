<!-- 盲検 B の返答(agentId a8842983290ce9284)。Agent tool の返却文をそのまま保存した。harness が各行に付けた 2 文字の字下げだけ外している。 -->
失敗例を 1 つ作りました。番の読みを def の node ごとに 1 回だけ覚えるようにする、性能の修正です。この修正では、Program の引数に束ねた番の class が、最初に読んだ呼び出しの値のまま固まります。提示された検査は 1 つも実行していません。下の「通る」は、すべてコードを読んで推論した結果です。

## 1. 機能要求と差分

**要求(性能の修正)**: 番を読む変更の後、`_read_clauses` は handler を読むたびに `_sent_on_guards`・`_pass_test`・`_classes` を回し直しています。handler は install される所ごとに読み直されます(`_dispatch_of` の docstring にある「5 本の閉じの検で 924 回の読み」)。そこで、既存の 4 つの cache(`_DISPATCHES`・`_CLAUSE_FUNCTIONS`・`_BODY_NODES`・`_BODY_FACTS`)と同じく、def の node ごとに 1 回だけ読みたい、という要求です。

**差分**: 変えるのは `/home/ubuntu/.worktrees/doeff-wt-dc-ki9a93/packages/doeff-effect-analyzer/python/doeff_effect_analyzer/handler_effects.py` だけです。`_pass_test` の後に足し、`_read_clauses` を書き換えます。

```diff
+@dataclass(frozen=True)
+class _GuardReading:
+    """A dispatcher's guards, read: the ``if`` of each guard that sends the effect on (by node —
+    not a clause) and, by the branch's node, the test under which it does (``Clause.passes``)."""
+
+    ifs: frozenset[int]
+    tests: Mapping[int, PassTest]
+
+
+_GUARD_READINGS: dict[int, tuple[FunctionNode, _GuardReading]] = {}
+
+
+def _guard_reading(node: FunctionNode, dispatch: _Dispatch, scope: _Scope) -> _GuardReading:
+    """The guards of ``node``, read once per def node like its branches (``_dispatch_of``): a
+    handler is read again wherever it is installed, and its guards never change — reading them
+    at every read ran ``_sent_on_guards`` and ``_pass_test`` once per install."""
+    cached = _GUARD_READINGS.get(id(node))
+    if cached is not None and cached[0] is node:
+        return cached[1]
+    sent = _sent_on_guards(dispatch, scope)
+    reading = _GuardReading(
+        ifs=frozenset(id(guard) for guard in sent.values()),
+        tests={at: _pass_test(guard.test, dispatch.param, scope) for at, guard in sent.items()},
+    )
+    _GUARD_READINGS[id(node)] = (node, reading)
+    return reading
+
+
 def _read_clauses(...) -> _ReadClauses:
     dispatch = _dispatch_of(node)
-    guards = _sent_on_guards(dispatch, scope)
-    guard_ifs = {id(guard) for guard in guards.values()}
+    reading = _guard_reading(node, dispatch, scope)
     ...
-        if id(branch.at) in guard_ifs:
+        if id(branch.at) in reading.ifs:
             continue  # a guard's ``if`` sends the effect on: it is not a clause
     ...
-        guard = guards.get(id(branch.at))
-        passes = None if guard is None else _pass_test(guard.test, dispatch.param, scope)
+        passes = reading.tests.get(id(branch.at))
```

既存の 4 つの cache と同じ書き方で、理由づけも同じ「the tree never changes」なので、レビューでも自然に見えます。

## 2. 本来の所有者と、違反する契約

- **所有者は M2(番の読み)です。** 設計では M2 の寿命は「1 回の読み」です。S4 はさらに「番の読みは 1 回の `_read_clauses` の局所値」「`Pass` かの判定は scope ごと(覚えない)」と主張しています。
- **移る判断:** 次の 2 つは、node と scope の組で決まります。
  - その番が何の class を見ているか(`_classes` が scope の `bound` から解く)
  - その if が Pass の番か(`scope.resolve(call.func)`)

  差分は、この答えを node だけを鍵にした process 全体の状態へ移します。最初の呼び出しの束縛で作った `PassTest` を、後のすべての呼び出しに当てることになります。判断の持ち主が「1 回の読み」から、誰も持たない大域の memo に変わります。
- **破れる契約と、その影響:**
  - M2 の公開の形 `_read_clauses(reader, node, scope, …) -> _ReadClauses` の答えが、渡した scope ではなく「先に何を読んだか」で決まるようになります。
  - M3 は変えていませんが、誤った `passes_on` を受け取ります。`pass_through` は `handles`・`passes_on`・`emits` しか問わないので、M3 側からは違反が見えません。
  - M5 の `cached_result` は、読む順で決まったこの答えを module の中身の digest で保存します。そのため誤りが process をまたいで残りえます。
  - S4(複数の thread)では、どの束縛が勝つかが thread の順で変わります。
- 定義(defk)については、同じ種類の契約が既存の検 `test_a_definition_in_a_body_reads_the_bindings_of_each_call` で固定されています。番についてはその検がありません。

## 3. 提示された各検査が拒否しない理由(すべて推論・未実行)

- **pytest(146 本)**: 答えが変わるのは、同じ dispatcher の node を、番の中の名が別の値に解ける scope で 2 回以上読む時だけです。
  - `make_package` は検ごとに uuid の新しい package を作ります。そのため検と検の間で node は共有されません。
  - 番を持つ fixture はどれも、毎回同じ値に解けます。
    - `test_guarded_clauses.py`: module の定数か、束縛の無い factory の引数(`fence-of` は毎回 `ClassTest((), complete=False)`)
    - `test_program_effects.py`: `write-fence [write-types]` を束縛なしで読む
    - `test_definitions_in_function_bodies.py`: module の `Nap`
  - 検の中で読む外部の handler(`doeff_core_effects.scheduler:scheduled` など)も、module または factory の経路から束縛なしで読まれます。
  - 束縛ごとの読みを確かめる唯一の検(`run_nested`)には番がありません。
  - 以上から、146 passed のままと推論します。
- **semgrep**: analyzer の path に当たる規則の中身を YAML で読みました。どれも doeff を実行時にどう使うかを見る規則です。差分はどの規則の型にも当たりません。
  - 例: `yield Pass($ARG)`、@do handler の中の `return Pass()`、`from __future__ import annotations`、doeff_vm の import を try/except で囲む書き方、`doeff.<名> =`、`Finally(...)`、`WithHandler` の shim、`claude -p`
- **ruff(13 件を増やさない)**: 新しいコードは、同じ file に既にある書き方だけでできています(注記つきの module の dict、frozen dataclass、内包表記)。
  - `global` 文(PLW0603)、可変の既定値(B006)、新しい import はありません。`Mapping` は既に import 済みです。
  - E501 と ARG001 は設定で無視されています。
- **pyright(7 件を増やさない)**: 内包表記の型 `dict[int, ClassTest|…|UnknownTest]` は `Mapping[int, PassTest]` に代入できます。`.get` の結果の `PassTest | None` は `Clause.passes` の型と合います。
- **着地の門**: focused 検は上の pytest と同じ範囲になると推論します。衝突の印は無く、依存も変えないので follow-install にも影響しません。ただし、門が実際にどの検を選ぶかは確かめていません。
- **`code-quality --scope changed`**: 「fast の段・型検査なし」以上の中身が示されていないので、通るかどうかは推測しません。

## 4. 確かめる最小の入力・手順・期待結果

作業は、共有の checkout ではなく e76b005 で切った使い捨ての worktree で行います。

1. 差分を当てる前と後で、提示の 4 つの命令を、変えた file を対象に回します。期待は、前後とも 146 passed・semgrep 0 件・ruff 13 件・pyright 7 件です(推論)。
2. 次の使い捨ての検を、その worktree の `packages/doeff-effect-analyzer/tests/python/` に置きます。`EFFECTS_PY` は `test_guarded_clauses.py` と同じ Tick・Nap・Zap です。

```python
import importlib
import pytest
from doeff_effect_analyzer.program_effects import analyze_program
pytest.importorskip("hy"); pytest.importorskip("doeff_hy")

EFFECTS_PY = ...  # test_guarded_clauses.py の EFFECTS_PY と同じ
PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [handle])
(import doeff [EffectBase])
(import {pkg}.effects [Tick Nap Zap])
(defk three [] {:pre [] :post [(: % int)]} (<- (Nap 1.0)) (<- (Tick)) (<- (Zap)) 1)
(defk sandboxed [passable] {:pre [(: passable type)] :post [(: % int)]}
  (<- r (handle (three) (EffectBase [] :when (not (isinstance effect passable)) (raise (ValueError "fenced")))))
  r)
(defk both [] {:pre [] :post [(: % int)]}
  (<- a (sandboxed Nap)) (<- b (sandboxed Tick)) (+ a b))
"""

def _left(pkg, program, **bindings):
    report = analyze_program(f"{pkg}.programs:{program}", bindings=bindings or None)
    return {t.__name__ for t in report.effect_types}

@pytest.mark.parametrize("first", ["Nap", "Tick"])
def test_each_call_reads_its_own_guard(make_package, first):
    pkg = make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})
    effects = importlib.import_module(f"{pkg}.effects")
    for name in [first, "Tick" if first == "Nap" else "Nap"]:
        assert _left(pkg, "sandboxed", passable=getattr(effects, name)) == {name}, name

def test_one_program_two_calls(make_package):
    pkg = make_package({"effects.py": EFFECTS_PY, "programs.hy": PROGRAMS_HY})
    assert _left(pkg, "both") == {"Nap", "Tick"}
```

3. 期待結果:
   - **差分の前**: 3 本とも通る(推論)。
   - **差分の後**: 3 本とも落ちる。
     - 2 回目の読みが 1 回目の答えを返します。Nap を先に読むと、passable=Tick でも `{"Nap"}` になります。
     - `both` は、書いた順に読むなら `{"Nap"}` だけになります。
   - 意味: 実行時には `sandboxed Tick` から `Tick` が外へ出ます。analyzer はそれを「柵が答えた」と読みます。そのため Tick を答えない env の閉じの検は、この Tick を gap として出しません(検が誤って緑になる)。
   - 手順 2 の検は、提示された検査の外に足す新しい入力です。主張は「提示された検査は変えずにそのまま通り、この入力で初めて違反が見える」です。

## 5. 未確認の前提・不足、推測と実測の区別

**実測(読んだ・数えたもの)**
- 対象の file と `evidence/impl-change.patch` を読みました。
- grep の結果:
  - 検の中の `:when` は上の 3 つの file だけです。
  - 本体のコードで、番の class を引数から取るのは次の defhandler の factory だけで、どれも束縛なしで読まれます。
    - `/home/ubuntu/.worktrees/doeff-wt-dc-ki9a93/packages/doeff-cluster/src/doeff_cluster/shared/core/semaphore_handlers.hy:229,262`
    - `/home/ubuntu/.worktrees/doeff-wt-dc-ki9a93/packages/doeff-cluster/src/doeff_cluster/sim/local.hy:1560`

    このことから、今の repo のコードの答えはこの差分では変わらないと推論します。つまり、この誤りは今は表に出ず、新しく書くコードで出ます。
  - `.semgrep.yaml` の `- id:` は 278 行ありました。analyzer の path に当たる規則を、repo の外に置いた一時 script(削除済み)で数えると約 37 で、うち 3 つは rust でした。入力書の「35 規則」との差は確かめていません。

**推測(未実行)**
- 4 つの検査と着地の門の結果。
- 差分の前に、`sandboxed` の番が束縛から正しく解けること。根拠は 3 つです。この前提が外れると、手順 2 の入力では違いが出ません。その場合は、本体の中の defhandler や手書きの dispatcher で同じ形を作り直す必要があります。
  - `_Scope._resolve_name` が `bound` を引く
  - `_scope_of` が親の `bound` を受け継ぐ
  - `_call_bindings` が class の引数を束ねる
- `both` の中で、書いた順に読むこと。
- `{:pre [(: passable type)]}` が defk で展開できること。

**不足している情報**
- `code-quality --scope changed` の規則の中身。
- 着地の門の focused 検の選び方。doeff-cluster の検も analyzer を import しているので、それが選ばれるかどうか。
- ruff 13 件と pyright 7 件の内訳。

**探索したが採らなかった候補**
- S2 の `type(effect) is X` を、`_pass_test` で `ClassTest` に写す。部分類で誤りますが、今の読みより見落としが増えることはありません。
- CLI や `to_dict` で `PassTest` の形を pattern match して、通す class を表示する。誤るのは表示だけです。
- VM の `__doeff_passes__` の thunk を analyzer から呼ぶ。今の出力は変わりません。

害の大きさと、実際に書かれやすいかどうかで、この 1 つを選びました。

WAIT: work — 依頼元の次の指示
