# 閉じの解析の読みの重なりを減らす設計 — 節の emits を要る時だけ読み、報告を reader に覚える

- 依頼: lt-KCVEBRK93NEE8BDA9C0JFZE9XZ(class investigate)・card ki-5a5f6bd26d5e(親 ki-d7f2e023c0d4)
- 書き手: 会話 c-QRFPES57Q2FZHQY9X69AEZCEFW・claude-opus-5-5
- 完了の範囲: **実装完了まで**。
  - 実装 1: doeff `646a3e9866cbb1e09cdadd865fe96a26ccd4a4a0`(main・マージの列 L2068)。
  - 実装 2: 盲検 A・B の失敗例の直し(commit の題「閉じの解析の遅れて読む節を、thread をまたいでも reader の読みかけを見ずに 1 回だけ読み、:absent の受け手を解析ごとに読む」・作った時の sha `3b141537a69f28203cdb677d5c2c8874b5c0b41e`)。この報告と同じ branch で main へ登録する(rebase で sha は変わりうる)。下の測りの「3b141537a」はこの commit。
- 盲検の前の主張: `design-before-blind.md`(sha256 `c699211ff394bf968a2b53b1dfc8d0178e0af72833bfdc513dcd062f42333179`)。下の 5 節で、事前の予想と実測を並べる。

## 1. 何が遅かったか(調べた事)

下流(agora-controllers)の閉じの検 10 本が読む 19 組を、検と同じ形で analyzer に渡して測った。条件は、温まった展開の木・答えの置き場 off。

| 重なり | 測った数 |
|---|---|
| handler を読むと、全部の節の emits を読む | deliver の組で、作った節 452 のうち引かれたのは 118 |
| handled の scope と運ばれた Program を、届いた walk ごとに報告し直す | 報告 7,193〜17,540 回に対し、別の事実 775〜1,325(遅延読みの後の数え)。辿り直しだけで組あたり 0.3〜2.1 秒 |
| handler を読むたびに、節の枝(`_branches`)を作り直す | 5 組で 924 回の読みに対し、`_branches` を 544,643 回 |
| handler ごとに本体を読み直す | 遅延読みの後は、facts の外れが別の (関数, 束縛) の 1.0〜1.33 倍まで減った |

## 2. 固定した要件

R1〜R6(`design-before-blind.md` の 1 節のまま): 答えとその形を変えない・節の emits を要る時だけ読む・遅れて読んだ source も数える・`_READING` の守りを保つ・2 つの重なりを答えを変えずに減らす・検を外さず時間の予算で隠さない。

## 3. 責務(盲検の後の最終形)

| id | 責務 | 盲検の後に変えた所 |
|---|---|---|
| clause-emits | 節の emits を、初めて引かれた時に 1 回だけ読んで覚える(`program_effects._ClauseEmits`)。等しさ・hash・pickle は読んでから | 2 つの thread が同時に引いた時も同じ値を答える、と口の説明に書いた |
| clause-reading | 節の枝を def ごとに 1 回見つけ(`handler_effects._dispatch_of`)、節ごとの読む関数を作る(`_emits_read_later`)。読む関数は、節を作った時の `_READING` の下で、reader の lock を持って 1 回だけ読む | lock の下で 1 回だけ読む形にした(盲検 A) |
| report-walk | 事実から報告を辿り、reader に覚える(`_walk`・`_KeptReport`)。使い回すのは、同じ事実・同じ label で、checked の上の active が同じ時だけ | 変えない |
| fact-reader | 本体の事実を読む(`_Reader`)。reader は lock(RLock)と、`:absent` の受け手(reader ごとに 1 回読む)を持つ | lock を足した。受け手を process の覚え(`functools.cache`)から reader の覚えへ移した(盲検 A・B) |
| source-io | source と展開の木を読み、読んだ module を数える(`reading_sources`) | 変えない |
| coverage | effect の届いた節だけの emits を引く(`pass_through`) | 変えない |
| result-cache | 答えを、読んだ module の中身を鍵にして置く(`cached_result`)。答えは compute が読んだ物だけを持つ(口の説明に書いた) | 変えない |

公開契約は `design-before-blind.md` の 3 節のまま。拡張は 1 つで、`Clause` の `emits` に引数なしの関数も渡せる。盲検の後の約束も 1 つ足した。`HandlerEffects` を thread の間で共有して別の節を引いても、直す前と同じ答えを出す(直す前の版は全部読み終えた値だったので、共有できた)。

## 4. 盲検 A・B と再現(設計者が撃った記録)

起動の記録は `blind/blind-meta.md`、返答は `blind/blind-a-return.md`・`blind/blind-b-return.md`。

| 失敗例 | 盲検の主張 | 設計者の再現(対象の版・結果) | 成立 |
|---|---|---|---|
| A-1: 1 つの HandlerEffects の別の節を、2 つの thread が同時に引く | 片方が、もう片方の読みかけ(空)の事実を辿って Far を落とす。S5 の「変わる module: なし」が崩れる | `evidence/R-A1-race-stress-main-vs-before.log`: main 646a3e986 で 200 回中 173 回が誤り、直す前の版は 200 回とも正しい | 成立 |
| A-2: 独立の 2 つの解析が、`:absent` の受け手を通して reader を共有する | `functools.cache` で受け手が process に 1 つ | `evidence/R-A2-absent-share-main.log`: main で、受け手の節も `_Reader` も同じ object | 成立 |
| B: handler の読みを process で覚える差分(`evidence/blind-b-scripts/variant.patch`) | 検 122 本と ruff を通るのに、答えと読んだ source が、同じ process で前に走った解析に依る | `evidence/R-B1-order-main-vs-variant.log`: 差分を当てた写しで、warm の後の job の答えが gap Z・unknown handler_a に変わり、読んだ module が `programs` だけになる。main では順に依らない | 成立(検の穴) |

予想と実測の差の訳:
- A は「隠すはずの知識の漏洩」。clause-emits が隠すと宣言した「どの reader で読むか」が、実際は handler の全部の節(`_absent_receiver` では process の全部の解析)で共有されていた。その読みかけの状態が、公開の値 `HandlerEffects` を通して別の thread に見えた。前提 P1 の後半(報告を作る間、同じ reader の読みは途中で止まっていない)は、1 つの thread の中でしか成り立っていなかった。
- B は、検が「解析は process の履歴に依らない」を見ていなかった穴。設計は process の覚えを入れない事を決めていたが(`design-before-blind.md` には書いていない — card の発言の「決めて入れなかった形」)、それを拒む検が無かった。しかも `:absent` の受け手が、実際にその形で覚えられていた。

## 5. 修正と再検証

| 版 | 直し | 再検証 |
|---|---|---|
| R1(3b141537a) | `_Reader.lock`(RLock)。遅れて読む節は、その lock の下で 1 回だけ読む。別の handler の節は並べて読める | `evidence/R-fixed-blind-repros.log`: A-1 は 200 回とも正しい |
| R2(3b141537a) | `:absent` の受け手を reader ごとに 1 回読む(`_Reader.absent_receiver`)。process に覚えない | 同 log: A-2 で受け手の節も reader も別の object、答えは両方 Raise。B の順でも答えと読んだ module が変わらない |
| R3(3b141537a) | `test_read_when_reached.py` に 3 本足した: 2 つの thread が別の節を引いても両方が Far・2 つの解析がそれぞれ受け手の source を数える・解析の答えと読んだ source が、先に走った解析に依らない(両方の順で比べる) | `evidence/C-new-tests-reject-main-variant-nolock.log`: 直した版は 7 本とも緑。直す前の main では前の 2 本が赤、B の差分では後の 2 本が赤、lock を外した写しでは 1 本目が赤 |

修正前の失敗例(main 646a3e986 での再現)は上の log に残した。盲検の前の主張(`design-before-blind.md`)は書き換えていない。

## 6. 強制の方法(最終)

| 守る責務 | 強制の方法 | 実装の箇所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| clause-emits: effect が届くまで節を読まない・読んだ source を引いた所で数える | 契約の検 | `packages/doeff-effect-analyzer/tests/python/test_read_when_reached.py::test_a_clause_is_read_when_an_effect_reaches_it_and_counted_where_it_is_read` | doeff の `make test-packages`(マージの列の packages の段・日次の全体の回) | 型では「いつ読むか」を表せない |
| clause-emits: pickle で emits が残る | 契約の検 | 同 `test_a_pickled_handler_keeps_what_its_clauses_emit` | 同上 | 置き場の答えに HandlerEffects を入れた時の数え漏れは、口の約束(`result_cache.cached_result` の説明)だけ |
| clause-reading: 遅れて読む節も `_READING` で止まる | 既存の検 | `test_inference_gaps.py::test_residual_through_an_env_and_recursion_through_a_scope` | 同上 | 入れ直しの形は inline の handle の 1 形 |
| clause-reading / fact-reader: thread をまたいでも読みかけを見ない | 契約の検(12 回・Barrier で同時に引く) | `test_read_when_reached.py::test_two_threads_asking_two_clauses_of_one_handler_both_read_what_they_emit` | 同上 | 競合は確率で起きる。lock を外した写しでは 12 回とも赤だった(`evidence/C-new-tests-reject-main-variant-nolock.log`)が、赤になる事は保証できない |
| fact-reader: 解析の答えと数えが process の履歴に依らない | 契約の検(両方の順) | 同 `test_an_analysis_answers_and_reads_the_same_after_other_analyses_in_the_process`・`test_two_analyses_each_count_the_absent_receiver_they_read` | 同上 | 入れ直しの形が違う process の覚えは、見つけられないかもしれない |
| report-walk: 使い回すのは active が同じ時だけ・同じ scope を辿り直さない | 契約の検 | 同 `test_a_scope_reached_where_a_call_in_it_is_being_reported_is_walked_again`・`test_a_scope_reached_again_under_the_same_lookups_is_the_report_walked_before` | 同上 | 実コードの全部の形は、19 組の実測で見る |
| 答え(R1) | 実測: 19 組で、答えの digest と ProgramEffects の木全体の digest を直す前と比べる | `evidence/measure/measure.py`(repo の外の測りの写し) | 手で撃つ(日次の経路には無い) | 19 組以外の Program は、下流の検の答えでしか見ない |
| 層・依存 | なし | — | — | analyzer の package は code-quality の対象に登録されておらず(fast の段は「未導入」と出す)、pyright の include にも無い |

## 7. 答えと秒の記録

- 答え: `evidence/C-answers-and-trees.txt`。直す前・646a3e986・3b141537a のどれも、19 組の答えの digest と木全体の digest が同じ。
- 秒: `evidence/T-timing-summary.txt`(19 組の analyze と coverage の和。直す前と交互に測った)。

| 版 | 負荷の帯(1 分平均) | 直す前 → 直した版 |
|---|---|---|
| 646a3e986 | 20〜24 | 64.1 → 23.4(37%)・52.5 → 23.0(44%) |
| 3b141537a | 28〜37 | 75.0 → 38.7(52%)・63.6 → 29.8(47%) |
| 3b141537a | 87〜110 | 121.9 → 53.9(44%)・119.5 → 54.9(46%) |

- 646a3e986 と 3b141537a の仕事の量は同じ: facts の外れ・handler の読みの数が、3 組とも一致した(`evidence/T-work-count-646-vs-3b14.txt`)。
- 交互に撃った秒の差(31.7 → 35.4 秒・負荷 30)は、負荷の揺れの内と読む。ただし lock の取り外しの費用を、この差から切り分けて測ってはいない。

## 8. 未確認事項

- 19 組以外の Program の答え(下流の閉じの検の日次で見る)。
- 日次の回で速くなるかどうか。agora-controllers の日次が使う doeff が、この版を含んでからの回で数える(親 card ki-d7f2e023c0d4 の受入)。
- process の表(`_MODULE_CACHE`・`_BODY_NODES` ほか)を、thread が同時に書く時の振る舞い。これは直す前からの形で、この設計の範囲の外。盲検 A の 200 回の再現では誤りが出なかった。
