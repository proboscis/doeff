## 盲検 B の結果: 検査は通るが責務と契約を侵す実装

出した失敗例は、`<-` が何を束ねられるかという判断を doeff-hy の外の `doeff_core_effects/outcomes.py` に足す実装です。その判断を使うのは例だけです。足した後でこの判断を変える差分を入れても、入口は例の検を選ばず rc 0 で通りました。例の検を手で走らせると赤です。

### 1. 機能要求と最小差分

機能要求: 「`<-` で async 関数の呼び(coroutine)を、`Await` を書かずに束ねたい。今は `Await` を書き忘れると VM の分かりにくい誤りになる」。

実装は実験の作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-B-6ntw` に未 commit で残しています(3 file、+12/−6)。

```diff
--- packages/doeff-core-effects/doeff_core_effects/outcomes.py
-from types import GeneratorType, NoneType, UnionType
+from types import CoroutineType, GeneratorType, NoneType, UnionType
-from doeff_core_effects.effects import Absent, Raise
+from doeff_core_effects.effects import Absent, Await, Raise
 def _opened(expr, token):
     ...
     if isinstance(expr, (Absent, Raise)):
         return _perform_unresumable(expr)
+    if kind is CoroutineType:
+        return Await(expr)
     return expr
 (_open_bind の docstring に「coroutine → Await(expr)」の 1 行を足す)
--- examples/ex05_parallel_timing.hy・ex08_tqdm_progress.hy
-    (<- result (Await (_async-impl x)))
+    (<- result (_async-impl x))    ;; coroutine — <- が Await にして出す
 (どちらも import から Await を外す)
```

選んだ理由は、`<-` の実行時の振り分けが既に `_opened` に在り、#844・#2449 で何度も手が入った所だからです。普通の実装者がまずここに書きます。

### 2. 本来の持ち主と、侵す契約

- **持ち主:** `<-` の意味(何を束ねられ、どう開くか)は、design.md §2 の macro-owner(`packages/doeff-hy/src/doeff_hy/`)の「macro の名と展開」です。
- **doeff-hy 側の公開契約:** `static_types.pyi` の `_doeff_perform(effect: Program[_T, object]) -> _T` と、その註「Program でない値を `<-` に渡すと赤になる」。
- **移る判断:** 「coroutine は Await にして出す」という判断が core-effects の実行時に置かれます。同じ `<-` について、doeff-hy の型の宣言は「赤」、core-effects の実行時は「受ける」と、二つの持ち主が食い違います。この判断を使うのは ex05 と ex08 だけです。
- **I1 との関係:** 新しい判断は I1 が守る範囲(例の file と `packages/doeff-hy/src/doeff_hy/`)の外に在ります。
- **差分の前から在る穴(実測):** defk の本体の `(<- y (foo x))` は `_doeff_outcomes.open_bind(foo(x))` に展開されます。つまり `<-` の実行時の半分は、df9e627 の時点で既に doeff-hy の外に在ります。
  - 内訳は core-effects の outcomes、doeff-vm の `BindOpener`、根の `doeff/do.py` です。
  - このため §4 の「他の module は接頭辞が同じなので同じ行が拾う」と、S2(a) は成り立っていません。

### 3. 提示された検が拒否しない理由(すべて実測)

| 検 | 拒否しない理由 |
| --- | --- |
| table-guard の例の検 | 見る入力は `EXAMPLES_DIR` の `*.hy` と `expected/*.txt`、それに `macros.hy` だけで、outcomes.py は入っていません。機能の差分の状態でも probe(後の変更)の状態でも、本物の表の検と合わせて 15 passed でした。 |
| 表の読み(`read_contract_table`・`_parse_row`) | 表は変えていません。入口は rc 2 にならず rc 0 でした。 |
| 表と package の増減 | package は増減していません。上の 15 passed に含まれます。 |
| 本物の表が読める | 上の 15 passed に含まれます。 |
| 例の検 | 機能の差分では ex05 と ex08 を変えたので doeff-traverse の行で選ばれましたが、負荷のため入口の中では「未測」でした。手で走らせると 31 passed・1 skipped です(skip は BROKEN が空の既存の分)。probe の差分では一度も選ばれず、出力の中に 0 回しか現れません。 |
| commit の時の検査 | `pre-commit run --files` で semgrep、semgrep-hy、doeff-linter が Passed でした。基点の script を直に呼ぶと rc 0 で、「測れない」の表示はありません。 |

### 4. 検査通過と責務違反を確かめる最小の手順と結果

**A. 機能の差分を当てた状態**
- table-guard と本物の表の検: 15 passed。
- 例の検(手で走らせる): 31 passed・1 skipped。
- 入口 `run_changed_tests.py --base HEAD --linter …`: rc 0。`selected=136 ran=2 red=0 unmeasured=134`(load average 100 前後)。
- pre-commit: Passed。

**B. 責務違反の確かめ**
1. 本物の表と `select_targets` で、変えた path ごとに例の検を選ぶかを調べました(scratch の `select_probe.py`)。
   - outcomes.py と `doeff/do.py`: 選ばない。
   - `macros.hy` と `outcome_forms.hy`: 選ぶ。
2. `S=$(git stash create)` で、機能の状態を ref に載らない commit `a57ccabf` にしました。
3. outcomes.py だけを HEAD の版に戻しました。「暗黙の Await は型の宣言と食い違うので外す」という後の変更を模したものです。`Await` という語が呼び手に出ないので、grep で使い手を探しても見つかりません。
4. その状態で入口を `--base $S` で走らせました。
   - 変えた file は 1 本で、例の検は一度も名指されません。
   - rc 0。`selected=134 ran=5 red=0 unmeasured=129`。
   - 走った中には table-guard(14 本)と ADR core_effects_003(19 本)があり、どちらも緑でした。
5. 例の検を手で走らせると `2 failed, 29 passed`(ex05 と ex08)でした。
   - ex05 は終了コード 0 のまま `async + sequential:   0 in 0.02s` と印字します(正しくは 550)。
   - stderr には `coroutine … was never awaited` が出ます。

期待どおり、マージの前の入口は緑のまま通り、赤になるのは日次だけ、という形が実測で確かめられました。

### 5. 未確認の前提と不足

- **推測:** 負荷が低い機体なら入口は逆依存の検をもっと走らせます。ただ、選びは path で決まるので、例の検が選ばれないことは変わらないと見ています。逆依存の 128 本の中にも例の検はありませんでした(実測)。
- **未実測:** doeff-hy-check(pyright)で coroutine の束ねが赤になるかは走らせていません(型検査を手で回さない規則のため)。「型の宣言では赤」という主張は `static_types.pyi` を読んだ結果です。例が型検査の対象かどうかも確かめていません。
- **未実測:** 日次の全体の検証は走らせていません。
- **推測:** 実装者が core-effects に単体の検を足していれば、分岐をそのまま消す probe はその検に捕まる可能性があります。ただ、例でしか見えない後退は残り、outcomes.py が I1 の外に在るという穴そのものは変わりません。
- **表を読んだだけ:** doeff-vm の行に例の検が無いことは表を読んで確かめましたが、`BindOpener` を変える probe は走らせていません。
- **読んだ範囲の逸脱 1:** design.md は全文(§0〜§4)を読みました。指示は §1〜§4 だけでした。
- **読んだ範囲の逸脱 2:** grep の出力に、読まないよう言われた他の設計検証の dir(lt-JQVA…・lt-QV9F…)の行が数行映りました。結論には使っていません。
- **作業の跡:** 実験の作業木には機能の差分が未 commit で残っています。`git stash create` で作った commit は ref に載らない物で、`refs/stash` は変えていません。共有の source は変えておらず、push も連絡もしていません。
- **置いた file:** 確かめの script と入口の出力は `/work/agent-workspaces/a-EMMA5DKA98SHR3XZXHKHMJ4TRS/scratch/` に在ります(`select_probe.py`、`expand_bind.py`、`entry-feature.txt`、`entry-probe.txt`)。

WAIT: work — 盲検 B を頼んだ会話の次の依頼
