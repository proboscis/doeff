# 閉じの解析の読みの重なりを減らす設計(盲検の前の主張)

- 依頼: lt-KCVEBRK93NEE8BDA9C0JFZE9XZ(class investigate)・card ki-5a5f6bd26d5e
- 対象の repo と版: doeff `646a3e9866cbb1e09cdadd865fe96a26ccd4a4a0`(この設計の実装が main に入った版)。直す前の版は `915aa1b963946102b3ba5947262f5218c8ed6d59`(main の親)。
- 完了の範囲: **実装完了まで**(実装は doeff main 646a3e986・マージの列 L2068)。この file は盲検 A・B の前に書いた主張で、盲検の後に書き換えない。

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
| clause-emits | 節が出す effect(emits)を、初めて引かれた時に 1 回だけ読んで覚える | emits がもう読まれたか | 読む関数の中身(どの reader・どの node・どの scope で読むか) | `Clause(handles, emits, location)` — `emits` は `ProgramEffects` か、それを返す引数なしの関数。`clause.emits` は常に `ProgramEffects`。等しさ・hash・pickle は emits を読んでから比べる・運ぶ | 引かれた時の読みが source を読む(下の source-io を通す) | Clause と同じ。読む前は reader を握る | 読むのは高々 1 回。読んだ値は、作った時に読んだ値と同じ |
| clause-reading | handler の dispatcher の節を見つけ、節ごとに emits を読む関数を作る | 節の枝(`_dispatch_of`・def の node ごとに 1 回)・節を作った時の `_READING` | 枝の見つけ方・`_READING` の写し | `analyze_handler(target, name=)` → `HandlerEffects`(形は変えない) | 枝の見つけは AST だけ。emits の読みは引かれた時 | handler の読み 1 回 | 節は作った時の `_READING` の下で読む。同じ handler を入れ直す節はそこで unread になる |
| report-walk | reader が読んだ事実から、Program の報告(`ProgramEffects`)を辿って作る。同じ報告を覚えて使い回す | 辿りが照らした呼ばれ先の鍵(checked)と、その上の active | 覚えの表(`_KeptReport`・reader ごと) | `_report_facts(reader, start, label=, active=)` → `ProgramEffects`(内部) | なし(事実の読みは reader に任せる。新しい読みを起こさない) | reader と同じ | 使い回すのは、同じ事実・同じ label で、checked の上の active が同じ時だけ。使い回した報告は、辿り直した報告と等しい |
| fact-reader | 関数の本体を読んで事実(`_Facts`)にする | (function, 束縛) ごとの事実 | AST の歩き方・名の解き方 | `_Reader.facts`・`_Reader.collect` | source の読み(source-io) | 解析 1 回(analyze_program 1 回・handler の読み 1 回) | 同じ鍵の事実は 1 回だけ読む |
| source-io | module の source と展開の木を読み、読んだ module を数える | `_MODULE_CACHE`・展開の木の置き場 | 置き場の形・Hy の展開 | `reading_sources(compute)` → `SourcesRead(value, read)`・`module_ast` | file の読み・展開の木の置き場の読み書き | process | `reading_sources` の中で読んだ module は、どこで読んでも数える |
| coverage | handler の組に effect を通し、残りを答える | effect の届いた節 | 節の emits の中身 | `pass_through`・`check_coverage`・`residual_with`(形は変えない) | 届いた節の emits を引く(clause-emits の読みを起こす) | 呼び 1 回 | 引くのは effect の届いた節だけ |
| result-cache | 解析の答えを、読んだ module の中身を鍵にして disk に置く | 鍵の数え方(読んだ module と、その依る module) | 置き場の形 | `cached_result(identity, compute)` | disk の読み書き | 答え 1 つ | 答えは pickle でき、compute が読んだ物だけを持つ |

## 3. 公開契約と内部

- 公開契約(変えない形): `analyze_program`・`analyze_handler`・`analyze_env`・`check_coverage`・`residual`・`pass_through` の引数と答えの型、`HandlerEffects`・`Clause`・`ProgramEffects` の欄、`reading_sources` の答え。
- 公開契約の拡張: `Clause` の `emits` に、`ProgramEffects` を返す引数なしの関数も渡せる。
- 内部で自由に決める物: `_walk`・`_KeptReport`・`_Reader.kept_report`/`keep_report`・`_dispatch_of`・`_emits_read_later`。
- 組み立て点から無くなる判断: handler の読みが「全部の節の emits を今読む」判断を持たない。読む時は coverage が effect を通す時に決まる。

## 4. 変更シナリオと事前の主張

| id | 軸 | 適用 | 変える要求 | 主張 | 事前の予想(変わる module / 変わらない module) |
|---|---|---|---|---|---|
| S1 | hardware | 対象外 | — | 解析は CPU の上で source と AST を読むだけで、機体に依る分岐を持たない。読む前の節が reader を握る分の memory は、解析 1 回の寿命の内 | — |
| S2 | storage | 適用 | 解析の答えを disk に置く形を変える(例: 置き場に `HandlerEffects` を入れる・答えを pickle で運ぶ) | 遅延の読みは clause-emits の中に閉じる。運ぶ側は pickle で読んだ後の emits を受け取る。読んだ source の数えは reading_sources が持ち、置き場の鍵の数え方は変えずに済む | 変わる: result-cache(置く答えの形だけ)/ 変わらない: clause-emits・clause-reading・report-walk・coverage・source-io |
| S3 | effects | 適用 | source と展開の木を読む I/O の場所・形を変える(例: 展開と置き場を別の module へ移す) | 読む「時」を決めるのは clause-emits と coverage、読む「口」は source-io と fact-reader。I/O の場所を変えても遅延の読みと報告の覚えは変わらない | 変わる: source-io / 変わらない: clause-emits・clause-reading・report-walk・coverage |
| S4 | distribution | 対象外 | — | この設計の覚えは process の中(reader ごと)だけで、process をまたがない。process をまたぐのは答えの置き場(result-cache)で、その鍵の数えは S2 の主張に入る | — |
| S5 | concurrency | 適用 | 解析を thread で並べる・節を作った thread と別の thread で emits を引く(free-threaded の Python 3.14t) | 報告の覚えは reader ごとで、別の解析と混ざらない。節を読む時の `_READING` は節が持ち、引いた thread の context に依らない。process の表(`_DISPATCHES` ほか)は AST だけで決まる値の覚え | 変わる: なし / 変わらない: 全部 |
| S6 | simulation | 適用 | 同じ入力から決定的に同じ答えを出す — 節を引く順(pass_through の順)と、報告を覚えるかどうかに依らない | 答えと ProgramEffects の木は、節を引く順と覚えの有無に依らない。覚えを使い回すのは、辿りが照らした鍵の上の active が同じ時だけ | 変わる: なし / 変わらない: 全部 |

主張の前提:
- P1: 事実(`_Facts`)は reader の中で一度読んだら変わらない。報告を作る間、同じ reader の事実の読みは途中で止まっていない(報告は読み終えた事実から作る)。
- P2: 節の emits を引くのは、解析の後の `pass_through`(と `to_dict`・等しさ・pickle)だけで、事実の読みの途中では引かない。
- P3: 答えを置き場へ置く解析は、答えに要る emits を `reading_sources` の中で引く(今の使い手は doeff-cluster の foundation_check の 1 つで、答えは文字列の組)。

## 5. 強制の方法

| 守る責務 | 強制の方法 | 実装の箇所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| clause-emits: effect が届くまで節を読まない・読んだ source を引いた所で数える | 契約の検(reading_sources で読んだ module を見る) | `packages/doeff-effect-analyzer/tests/python/test_read_when_reached.py::test_a_clause_is_read_when_an_effect_reaches_it_and_counted_where_it_is_read` | doeff の `make test-packages`(マージの列の packages の段と日次) | 型では表せない(いつ読むか)。検の fixture は Python の handler 1 つ |
| clause-emits: pickle で emits が残る | 契約の検(pickle の往復と等しさ) | 同 `test_a_pickled_handler_keeps_what_its_clauses_emit` | 同上 | 置き場の答えに HandlerEffects を入れた時の数え漏れは docstring の約束だけ(`result_cache.cached_result`) |
| clause-reading: 遅れて読む節も `_READING` で止まる | 既存の検(handle の節が自分を入れ直す Program を走らせる) | `test_inference_gaps.py::test_residual_through_an_env_and_recursion_through_a_scope` | 同上 | 入れ直しの形は handle の inline の 1 形 |
| report-walk: 覚えを使い回すのは active が同じ時だけ | 契約の検(同じ scope を active の違う所から届かせる) | `test_read_when_reached.py::test_a_scope_reached_where_a_call_in_it_is_being_reported_is_walked_again` | 同上 | 実コードの全部の形は 19 組の実測で見る(検ではない) |
| report-walk: 同じ scope を辿り直さない | 契約の検(使い回した報告が同じ物か) | 同 `test_a_scope_reached_again_under_the_same_lookups_is_the_report_walked_before` | 同上 | 速さそのものは検にしない(時間の予算で隠さない) |
| 答え(R1) | 実測: 閉じの検の 19 組で答えと木全体の digest を直す前と比べる | 測りの script(repo の外・agora-controllers の作業木で撃つ) | 手で撃つ(日次の経路には無い) | 19 組以外の Program は検の答えでしか見ない |
| 層・依存 | なし(handler_effects が program_effects の private な名を import する既存の形のまま) | — | — | analyzer の package は code-quality の対象に登録されておらず、pyright の include にも無い(未導入) |
