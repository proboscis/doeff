## 盲検 B: 静的検査を通る責務違反の失敗例 — Traverse を止める `failure_budget`

結論から書きます。作った変更は、`ItemOutcome` を受ける消費者 `failure_budget(max_failed)` を 1 つ足すものです。提示された検査は、走らせたもの全部で拒否されませんでした。その一方で、実行の方針の判断(残りの件を走らせるか、Traverse が `Collection` を答えるか例外を上げるか)が M3 から M2 へ移ることを実測で確かめました。共有の作業木 `/home/ubuntu/.worktrees/doeff-blind-3293bc4cc` は変えていません(git status は空)。

### 1. 機能の要求と変更の中身

**要求**(実在の依頼ではなく、本番の job でありそうな形として私が想定したもの): 「本番の job で backend が落ちると、1000 件が全部失敗するまで Traverse が回り続ける。失敗が N 件を超えたら止めたい。sequential でも parallel でも同じように使いたい。」

**実装**: S2 の形(handler 1 つと stack の 1 要素を足し、注記で `ItemOutcome` を名指す)で、`progress_bars` の隣に置きます。差分の全体は `/tmp/kie3a9-blind-b/failure_budget.diff` です(`handlers.py`・`handlers.pyi`・`__init__.py` の export と、実装者が書きそうな検 `tests/test_failure_budget.py`)。要の部分は次のとおりです。

```python
def failure_budget(max_failed: int):
    """ItemOutcome handler: stop a Traverse once more than ``max_failed`` of its items failed. ...
    Install it inside the Traverse handler, like progress_bars."""
    failed_by_run: dict[object, int] = {}

    @do
    def handler(effect: ItemOutcome, k):
        if isinstance(effect, ItemOutcome):
            if effect.outcome == "failed":
                failed = failed_by_run.get(effect.traverse, 0) + 1
                failed_by_run[effect.traverse] = failed
                if failed > max_failed:
                    failed_by_run.pop(effect.traverse, None)
                    return (yield ResumeThrow(k, FailureBudgetExceeded(effect.label, failed, effect.total)))
        yield Pass(effect, k)

    return _program_handler(handler)
```

実装者が書く検は、sequential と parallel(2) で `FailureBudgetExceeded` が上がること、上限の内なら `Collection` を答えることだけを確かめます。この検は 4 件とも通ります(実測)。

### 2. 本来の持ち主と、侵す契約

- **移る判断**: 残りの件を走らせるか、Traverse が `Collection` を答えるか例外を上げるか。これは M3 の「件をどう走らせ」で、止める方針は M3 の要素 `parallel_fail_fast` が持っています。`failure_budget` は、知らせ(`EffectBase[None]`)の出された所へ例外を投げ込みます。そのため、この判断が M2 の消費者へ移ります。
- **移る知識**: 正しく止めるには、M3 が隠している次の知識が要ります。M2 の行に書かれた「M3 の内部の規則を持たない」に反します。
  - sequential は、件ごとの try の外で知らせる → Traverse から例外が抜ける。
  - parallel は、task の中で Try と ReleaseSemaphore の後に知らせる → 投げてもその task が落ちるだけで、他の task は走り続け、最後に Gather が上げる。
  - parallel_fail_fast は `except` の中で知らせる。
  - 入れ子の時は、内の Traverse の例外が、外の handler の例外の捕まえ方で「外の件の failed」に変わる。
- **侵す主張と約束**:
  - S1: 方針だけを替えても数える件と bar の閉じ方は同じ、という主張。sequential と parallel(1) で、走る件が 4 と 8、bar が 3/8 と 7/8 に分かれました。
  - S2: 消費者を足しても M3 の答えは変わらず、他の消費者にも影響しない、という主張。Traverse の答えが例外に変わり、外の記録係と progress_bars は投げられた知らせ(件 3)を受けません。
  - README の「数える handler は failed の件も含めて total に届く」。限りに挙がっているのは parallel_fail_fast だけですが、sequential と parallel でも bar が閉じなくなります。
- **状態**: parallel で計算済みの ok の 5 件(M3 が答える `Collection`)が捨てられます。

### 3. 各検査が拒否しない理由(走らせたものは実測、理由の一部は推測)

| 検査 | 結果 | 理由 |
|---|---|---|
| 漏れない(`test_doeff_traverse_item_outcomes_stay_inside_the_handler`) | 通過(実測) | parametrize の表は 3 つの Traverse の handler だけで、それらは変えていない。analyzer は `failure_budget` を `progress_bars` と同じく「ItemOutcome を扱い、何も出さない」と読む(`exp/analyze.py` で実測)。`ResumeThrow` で投げる例外は効果の出力として数えられない(出力が emits=[] である事は実測。analyzer が一般に例外を追わない事は推測) |
| 中身が答えと同じ・bar が total で閉じる・名指す handler が無い時は今のまま(`test_item_outcomes.py` 全部、`test_sequential_reads_handlers_once.py`、`test_traverse_effect_type_filter.py`) | 通過(実測) | 消費者を 2 つ組む検も、Pass か None 以外で答える消費者の検も無い。effect_type_filter の表にも新しい handler は入らない |
| 型 | 変更による型の誤りは無い(実測) | `handlers.pyi` と新しい検を pyright で読んだ。出たのは `pytest` を解けない誤りだけで、既存の `test_item_outcomes.py` にも同じものが出る(写しの環境の事情)。答えの型 `None` と `ResumeThrow` は型の上で結びつかないので、「投げるな」は型で表せない(推測) |
| 着地: semgrep(基点比較) | 通過(実測) | hook の入口 `hook_finding_baseline.py check semgrep` が exit 0。semgrep を直に走らせても .py 3 file で 0 件、.pyi で 30 規則 0 件 |
| 着地: doeff-linter(基点比較) | 通過(実測) | 入口が exit 0。断面の置き場の binary で測れており(「測れない」ではない)、error は 0 で基点も 0。出た所見は warning と info(DOEFF009 など、既存の `progress_bars` と同じ種類)だけで、hook は止めない |
| 着地: 衝突の印の走査・uv sync・pre-commit の枠組みそのもの | 未実行 | hook の入口の命令を直に走らせただけ |

### 4. 最小の手順と期待する結果

1. 写しを作る: `git -C /home/ubuntu/.worktrees/doeff-blind-3293bc4cc archive HEAD | tar -x -C /tmp/kie3a9-blind-b/tree` に `/tmp/kie3a9-blind-b/failure_budget.diff` を当てる(作り済み)。
2. 検: `cd /tmp/kie3a9-blind-b/tree && PYTHONPATH=$PWD/packages/doeff-traverse:$PWD/packages/doeff-effect-analyzer/python uv run --project /home/ubuntu/.worktrees/doeff-blind-3293bc4cc pytest -q packages/doeff-traverse/tests/test_item_outcomes.py packages/doeff-effect-analyzer/tests/python/test_carried_builders.py packages/doeff-traverse/tests/test_sequential_reads_handlers_once.py packages/doeff-traverse/tests/test_traverse_effect_type_filter.py packages/doeff-traverse/tests/test_failure_budget.py` → **42 passed**(実測)。新しい検は写しにしか無い `failure_budget` を import して通るので、写しの module が読まれていることも確かめられる。
3. 違反: 同じ env で `uv run --project … python /tmp/kie3a9-blind-b/exp/violation.py`。出力は `exp/violation.out` に保存済み(実測)。8 件のうち 2・3・5 が落ちる段 1 つで、stack を `[failure_budget(1), 記録係, progress_bars, fail_handler, try_handler]` に固定し、方針だけを替えた結果:

| 方針 | 結果 | f が走った件数 | 記録係が受けた知らせ | bar |
|---|---|---|---|---|
| budget なし(sequential / parallel 全部) | Collection(5 ok・3 failed) | 8 | 8 | 8/8 閉じる |
| sequential | FailureBudgetExceeded | 4 | 3(件 3 が無い) | 3/8 開いたまま |
| parallel(1)・(2)・(10) | FailureBudgetExceeded | **8**(止まらない) | 7 | 7/8 開いたまま |
| parallel_fail_fast(10) | ConnectionError | 8 | 7 | 7/8(budget なしなら 8/8 で閉じる) |

   入れ子(外 2 件 × 内 3 件、sequential)では、内の budget の例外が外の件の failed に変わって外は続き、外の budget で初めて上がります。bar は inner0 2/3・outer 1/2・inner1 2/3 で、どれも閉じません(実測)。

### 5. 未確認の前提と、足りない情報

- 公開の約束の文面(`ItemOutcome` の docstring と README)は、消費者が投げることを明文では禁じていません。違反という判断は、責務の表(M2 = 数えて見せる、M3 = どう走らせるか)、S1・S2 の主張、README の「total に届く」に拠った私の解釈です。
- parallel(1) で、投げた後も 8 件全部が走る仕組み(Gather と兄弟の task の扱い)は、測っただけで scheduler の中は読んでいません。
- 走らせていない検査: 日次の全体の pyright、pre-commit の枠組みそのもの、衝突の印の走査、doeff-cluster の foundation closure を本物の job で(`analyze_handler` の読みだけ確かめた)、Hy の defhandler で書いた消費者、agora-controllers 側での使われ方。
- 環境について:
  - pytest は、借りた .venv に PYTHONPATH を前に足して写しを読ませました。
  - semgrep・linter・pyright は写しの中で走らせました。
  - 写しの中に `uv run` が `.venv` を勝手に作りました(私の置き場の中だけ)。
  - 写しの file の文字の置き換えに、機体の python3 を 1 度使いました(doeff の実行ではありません)。
- 他の候補として、消費者が `effect.item` を書き換える形(持ち越しの件は前の段の `Collection` と同じ物なので、前の段の答えまで変わる)と、終わった順を件の番号の順と思い込む消費者を考えました。どちらも検証していません。

実験の file はすべて `/tmp/kie3a9-blind-b/` の下に在ります。
- 差分: `failure_budget.diff`
- 写し: `tree/`
- 実験: `exp/violation.py`・`exp/violation.out`・`exp/analyze.py`・`exp/linter_counts.py`・`exp/show_linter.py`

WAIT: work — 依頼した会話の次の指示
