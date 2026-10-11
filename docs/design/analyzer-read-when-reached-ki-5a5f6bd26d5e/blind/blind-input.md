# 盲検の材料: doeff-effect-analyzer の「節の emits を要る時だけ読む・報告を覚える」設計

## 対象

- repo: doeff。作業木 `/work/toolset/worktrees/doeff-wt-ki-5a5f-design-check`(版 `646a3e9866cbb1e09cdadd865fe96a26ccd4a4a0` — この設計の実装が入った版)。
- 変更の差分: 同じ dir の `change.patch`(直す前の版 `915aa1b963946102b3ba5947262f5218c8ed6d59` からの差分)。
- 実コード: `packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py`・`handler_effects.py`・`result_cache.py`。
- 検: `packages/doeff-effect-analyzer/tests/python/`(特に `test_read_when_reached.py`・`test_inference_gaps.py`)。

背景: doeff-effect-analyzer は doeff の Program を実行せずに source から読み、Program が出す effect が handler の組で全部答えられるか(閉じているか)を答える。下流(agora-controllers)の「job が本番の土台で閉じているか」の検 10 本が、この解析を 1 組 1 回呼ぶ。解析の答えは `doeff_effect_analyzer.result_cache.cached_result` で disk に置かれ、鍵は解析が読んだ module(`reading_sources`)とその依る module の中身。

## 1. 固定した要件

| 番 | 要件 |
|---|---|
| R1 | 閉じの解析の答え(gaps・unknown・unresolved・origin)を変えない。答えの形も変えない |
| R2 | handler の節の emits(節が中で出す effect)は、要る時だけ読む |
| R3 | 読んだ source の記録(`_SOURCES_READ` — 答えの置き場の鍵に入る)が、遅れて読んだ分も数える |
| R4 | 節が自分の handler を入れ直す時に止める守り(`_READING`)を保つ |
| R5 | 同じ呼ばれ先を範囲ごとに辿り直す重なりと、handler ごとに本体を読み直す重なりを、答えを変えずに減らす |
| R6 | 触らない: 閉じの検を外す・答えの形を変える・時間の予算で隠す |

## 2. 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effects | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| clause-emits | 節が出す effect(emits)を、初めて引かれた時に 1 回だけ読んで覚える | emits がもう読まれたか | 読む関数の中身(どの reader・どの node・どの scope で読むか) | `Clause(handles, emits, location)` — `emits` は `ProgramEffects` か、それを返す引数なしの関数。`clause.emits` は常に `ProgramEffects`。等しさ・hash・pickle は emits を読んでから比べる・運ぶ | 引かれた時の読みが source を読む(source-io を通す) | Clause と同じ。読む前は reader を握る | 読むのは高々 1 回。読んだ値は、作った時に読んだ値と同じ |
| clause-reading | handler の dispatcher の節を見つけ、節ごとに emits を読む関数を作る | 節の枝(`_dispatch_of`・def の node ごとに 1 回)・節を作った時の `_READING` | 枝の見つけ方・`_READING` の写し | `analyze_handler(target, name=)` → `HandlerEffects`(形は変えない) | 枝の見つけは AST だけ。emits の読みは引かれた時 | handler の読み 1 回 | 節は作った時の `_READING` の下で読む。同じ handler を入れ直す節はそこで unread になる |
| report-walk | reader が読んだ事実から、Program の報告(`ProgramEffects`)を辿って作る。同じ報告を覚えて使い回す | 辿りが照らした呼ばれ先の鍵(checked)と、その上の active | 覚えの表(`_KeptReport`・reader ごと) | `_report_facts(reader, start, label=, active=)` → `ProgramEffects`(内部) | なし(事実の読みは reader に任せる。新しい読みを起こさない) | reader と同じ | 使い回すのは、同じ事実・同じ label で、checked の上の active が同じ時だけ。使い回した報告は、辿り直した報告と等しい |
| fact-reader | 関数の本体を読んで事実(`_Facts`)にする | (function, 束縛) ごとの事実 | AST の歩き方・名の解き方 | `_Reader.facts`・`_Reader.collect` | source の読み(source-io) | 解析 1 回(analyze_program 1 回・handler の読み 1 回) | 同じ鍵の事実は 1 回だけ読む |
| source-io | module の source と展開の木を読み、読んだ module を数える | `_MODULE_CACHE`・展開の木の置き場 | 置き場の形・Hy の展開 | `reading_sources(compute)` → `SourcesRead(value, read)`・`module_ast` | file の読み・展開の木の置き場の読み書き | process | `reading_sources` の中で読んだ module は、どこで読んでも数える |
| coverage | handler の組に effect を通し、残りを答える | effect の届いた節 | 節の emits の中身 | `pass_through`・`check_coverage`・`residual_with`(形は変えない) | 届いた節の emits を引く(clause-emits の読みを起こす) | 呼び 1 回 | 引くのは effect の届いた節だけ |
| result-cache | 解析の答えを、読んだ module の中身を鍵にして disk に置く | 鍵の数え方(読んだ module と、その依る module) | 置き場の形 | `cached_result(identity, compute)` | disk の読み書き | 答え 1 つ | 答えは pickle でき、compute が読んだ物だけを持つ |

公開契約(変えない形): `analyze_program`・`analyze_handler`・`analyze_env`・`check_coverage`・`residual`・`pass_through` の引数と答えの型、`HandlerEffects`・`Clause`・`ProgramEffects` の欄、`reading_sources` の答え。公開契約の拡張: `Clause` の `emits` に、`ProgramEffects` を返す引数なしの関数も渡せる。内部で自由に決める物: `_walk`・`_KeptReport`・`_Reader.kept_report`/`keep_report`・`_dispatch_of`・`_emits_read_later`。

## 3. 将来の変更と主張

| id | 軸 | 変える要求 | 主張 | 予想(変わる module / 変わらない module) |
|---|---|---|---|---|
| S1 | hardware | (対象外)解析は CPU の上で source と AST を読むだけ | — | — |
| S2 | storage | 解析の答えを disk に置く形を変える(例: 置き場に `HandlerEffects` を入れる・答えを pickle で運ぶ) | 遅延の読みは clause-emits の中に閉じる。運ぶ側は pickle で読んだ後の emits を受け取る。読んだ source の数えは reading_sources が持ち、置き場の鍵の数え方は変えずに済む | 変わる: result-cache(置く答えの形だけ)/ 変わらない: clause-emits・clause-reading・report-walk・coverage・source-io |
| S3 | effects | source と展開の木を読む I/O の場所・形を変える(例: 展開と置き場を別の module へ移す) | 読む「時」を決めるのは clause-emits と coverage、読む「口」は source-io と fact-reader。I/O の場所を変えても遅延の読みと報告の覚えは変わらない | 変わる: source-io / 変わらない: clause-emits・clause-reading・report-walk・coverage |
| S4 | distribution | (対象外)覚えは process の中(reader ごと)だけ。process をまたぐのは答えの置き場で、S2 に入る | — | — |
| S5 | concurrency | 解析を thread で並べる・節を作った thread と別の thread で emits を引く(free-threaded の Python 3.14t) | 報告の覚えは reader ごとで、別の解析と混ざらない。節を読む時の `_READING` は節が持ち、引いた thread の context に依らない。process の表(`_DISPATCHES` ほか)は AST だけで決まる値の覚え | 変わる: なし / 変わらない: 全部 |
| S6 | simulation | 同じ入力から決定的に同じ答えを出す — 節を引く順(pass_through の順)と、報告を覚えるかどうかに依らない | 答えと ProgramEffects の木は、節を引く順と覚えの有無に依らない。覚えを使い回すのは、辿りが照らした鍵の上の active が同じ時だけ | 変わる: なし / 変わらない: 全部 |

主張の前提:
- P1: 事実(`_Facts`)は reader の中で一度読んだら変わらない。報告を作る間、同じ reader の事実の読みは途中で止まっていない。
- P2: 節の emits を引くのは、解析の後の `pass_through`(と `to_dict`・等しさ・pickle)だけで、事実の読みの途中では引かない。
- P3: 答えを置き場へ置く解析は、答えに要る emits を `reading_sources` の中で引く。

## 4. 検査の実体と検出範囲

| 守る物 | 検査 | 場所 |
|---|---|---|
| effect が届くまで節を読まない・読んだ source を引いた所で数える | 契約の検 | `tests/python/test_read_when_reached.py::test_a_clause_is_read_when_an_effect_reaches_it_and_counted_where_it_is_read` |
| pickle で emits が残る | 契約の検 | 同 `test_a_pickled_handler_keeps_what_its_clauses_emit` |
| 遅れて読む節も `_READING` で止まる | 既存の検 | `tests/python/test_inference_gaps.py::test_residual_through_an_env_and_recursion_through_a_scope` |
| 覚えを使い回すのは active が同じ時だけ | 契約の検 | `test_read_when_reached.py::test_a_scope_reached_where_a_call_in_it_is_being_reported_is_walked_again` |
| 同じ scope を辿り直さない | 契約の検 | 同 `test_a_scope_reached_again_under_the_same_lookups_is_the_report_walked_before` |
| 答え(R1) | 下流の閉じの検の 19 組で、答えの digest を直す前と比べる実測 | repo の外(手で撃つ) |

この package を見る検査は、`make test-packages` が撃つ pytest(`packages/doeff-effect-analyzer/tests`)と ruff(`uv run --no-sync ruff check <file>`)だけ。pyright の include と code-quality の検査の対象には入っていない。

撃ち方(この作業木の中で):

```
cd /work/toolset/worktrees/doeff-wt-ki-5a5f-design-check
uv run --no-sync pytest packages/doeff-effect-analyzer/tests -q -p no:cacheprovider
```

## 5. 実験の制限

- 作業木の追跡されている file を書き換えない。実験は `/tmp/ki5a5f-blind-<A か B>/` の下に写しを置いて行う(例: `python/doeff_effect_analyzer` を写し、`PYTHONPATH=<写し>` を付けて同じ pytest を撃つと、検はその写しを読む)。
- 読まないもの: git の log と commit の本文・`docs/design/` の下・agora の card や会話。
- 撃ってよいのは analyzer の package の検と、自分で書いた小さな script だけ(repo 全体の検は撃たない)。他の会話へ連絡しない。
- 返答の先頭で、自分の model の名を名乗る。
