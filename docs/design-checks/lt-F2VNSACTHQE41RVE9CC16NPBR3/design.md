# 設計検証 lt-F2VNSACTHQE41RVE9CC16NPBR3 — package の loop の検の一巡の共有と sh の偽の runner(盲検の後の設計本文)

この文書の目的: card acp:kanban-issue:ki-e8f54a86b0a8 の直し(doeff の root の検 2 本が `make test-packages` を 2 巡させ、
Python を 70 回起こしていた)について、責務の分け方・変更シナリオごとの主張と実測・強制方法・盲検 A・B の失敗例と直しを、
読み手が版と証拠の file まで辿れる形で残す。盲検の前の主張は design-pre-blind.md(書き換えていない)。

## 1. 完了の範囲と版

完了の範囲 = 実装完了まで。

| 版 | 中身 | 状態 |
|---|---|---|
| 10addc647(10addc6473923419ef6acdb18dcb9044b87c7602) | 変更の前(基準の版) | — |
| 4bb2e090c | 2 本がこの repo の一巡を module の fixture で共有し、偽の runner を sh にした | main に着地(L2181) |
| c854bccaf | 設計者の自己点検 G1 の直し: 全部緑の rc 0 を模型の木で見る検・make を回す口 `_run_test_packages` | branch wt/dc-make-test-packages-once |
| e61d80c39 | 盲検 A・B の直し: runner が rc を記録・型 `_RunnerCall` と口 `dirs()`・訪ねの検が共有の一巡の結末を確かめる・緑の検が解いた dir で比べる | 同上(この記録と一緒に登記する) |

設計検証は 4bb2e090c の着地の後に始めた(依頼 lt-F2VNSACTHQE41RVE9CC16NPBR3 は class=investigate で設計検証が必須だった事を、
着地の後に終わりの検査で知った)。主張は c854bccaf の版について盲検の前に固定した(design-pre-blind.md)。

## 2. 要件

| id | 要件 | 出所 |
|---|---|---|
| R1 | 2 本(`test_make_test_packages_visits_every_package_after_a_red`・`test_make_test_packages_lists_every_slow_test`)は本物の `make test-packages` を偽の runner で回す一巡を 1 回だけ回し、記録を module の fixture で共に読む | card 本文 |
| R2 | 偽の runner は sh。path は cd と `pwd -P` で解く(`realpath -m` は Mac に無い) | card 本文 |
| R3 | 2 本の合計が load 65〜70 で 2 秒以下(直す前と並べる) | 受入 ca541182b |
| R4 | 赤を見逃さない(package を 1 つ飛ばす・秒の flag を落とす変異が赤) | 受入 c89cb0106 |
| R5 | 一巡の共有の前に検が見ていた性質を 1 つも失わない | 設計者が足した |

## 3. 責務(e61d80c39)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effects | 寿命 |
|---|---|---|---|---|---|---|
| M-loop | Makefile の target `test-packages`(検の対象・この変更では変えない) | 母集団の選び・赤の後も続ける・`test-packages failed:` の名指し・PACKAGE_PYTEST_DURATIONS | session の起こし方(PACKAGE_UV_RUN に委ねる)・根の綴り | make の変数・rc・要約の行 | session ごとの process | make の 1 回 |
| M-expect | 期待の導出(`_expected_packages`・`_package_extra_test_roots`・`_DAILY_DURATIONS_FLAGS`・`_stage_run`・`_named_failed_packages`) | 期待を file system から・外の根は Makefile に聞く・要約の行の読み方 | なし | 関数 | file system を読む・make 1 回 | 検ごと |
| M-runner | 偽の runner `_FAKE_RUNNER`(sh) | 赤の dir に物理の path で当たる引数を受けた時だけ rc 1・返す rc と cwd と引数を 1 行で記録 | path の解き方・記録の形 | env DOEFF_FAKE_RUNNER_RECORD・DOEFF_FAKE_RUNNER_FAIL・rc | 記録の file へ追記 | session の 1 回 |
| M-make | `_run_test_packages`・`_MakeRound`・`_RunnerCall` | 本物の Makefile を `-f` で `-C <木>` に回す・記録を `_RunnerCall(rc, cwd, argv)` に読む・引数の dir を物理の path に解く口 `dirs()` | 記録の形・path の解き方(検は `dirs()` を使う) | `_run_test_packages(repo, tmp, fail, *overrides) -> _MakeRound` | make を 1 回・tmp に書く | 呼びごと |
| M-round | fixture `packages_round`・`_PackagesRound` | この repo の一巡を module で 1 回・模擬の結末は「名前順で最初の package だけが赤」の 1 つ | なし | `_PackagesRound(expected, first, made)` | M-make を 1 回 | module |
| M-checks | 検 3 本 | visit: 0 以外を返したのが最初の package の 1 つだけ・訪ねた集合 = 期待・外の根・rc≠0・名指し = [first] / flags: 全 session に秒の flag / green: 模型の木の 3 根を 1 度ずつ・rc 0・名指し無し | なし | pytest の検 | 記録を読む(green は M-make 1 回) | 検ごと |
| M-route | 実行経路 | contract-tests prefix ""(make test-changed がどの変更でも走らせる)・日次の段 root | なし | 設定 | なし | 設定 |

## 4. 設計者の自己点検(盲検の前)— G1: 全部緑の rc 0 の検が抜けた

- 所見: 4bb2e090c で共有の一巡は赤を含むので、「どの session も緑なら rc 0」を見る検が無くなった。直す前は秒の flag の検が全部緑の
  一巡で `exit_code == 0` を見ていた(R5 に反する)。
- 再現(変異 and-summary — 要約の行を `[ -n "$$failed" ] && { …; exit 1; }` に書き換え、緑の時に recipe の最後の命令が 1 を返す):
  4bb2e090c の検で 2 passed(見逃し — evidence/logs/G1-and-summary-on-4bb2e090c.txt)、10addc647 の検で秒の flag の検が
  「緑で終わらなかった」で赤(evidence/logs/G1-and-summary-on-10addc647.txt)。
- 直し(c854bccaf): 模型の木(package a・b と外の根 extra/sim)を全部緑で回す検 `test_make_test_packages_is_green_when_every_package_is_green`。
  この repo の一巡は 1 回のまま。再検証: e61d80c39 で and-summary は緑の検が赤(evidence/validate/and-summary-on-e61d80c39.txt)。

## 5. 盲検 A — 緑の検が根の綴りを知っていた(隠すはずの知識の漏洩)

- 起動と入力: blind-meta.md・blind-input.md(sha256 e3b64f20…)。返答(未加工): blind-A-response.md。
- 失敗例: package の根も Makefile の変数 1 つで回す(`$(wildcard packages/*/tests/)` が末尾に `/` を付ける)。最小の差分は
  `pytest "$${dir}tests/"`。日次の性質は何も変わらないのに、緑の検だけが argv の文字列で比べていたため赤になる。
- 再現(c854bccaf・変異 trailing-slash): 緑の検が「session を全部訪ねていない: ['extra/sim']」で赤、訪ねの検と秒の flag の検は緑
  (evidence/blind-A/A-trailing-slash-on-c854bccaf.txt)。成立。
- 分類: 隠すはずの知識(M-loop の根の綴り)の M-checks への漏洩。同じ判断(訪ねた根)が M-checks の中に 2 つの規則で在った。
- 直し(e61d80c39): 引数を物理の path に解く口を `_RunnerCall.dirs()` 1 つにし、訪ねの検・外の根の検・緑の検が同じ口を使う。
  緑の検は解いた dir の並び(重ねて訪ねたら赤)で比べる。
- 再検証(e61d80c39): trailing-slash で 3 passed(evidence/validate/trailing-slash-on-e61d80c39.txt)。
- A の副次の観察と答え:
  - S-sim の予測の外れ: 新しい模擬の結末は runner の env を `_run_test_packages` が組むので M-make も変わる。認める — 予測
    「M-runner・新しい検」は狭すぎた。分類は M-make の公開の口の意図した拡張(引数 1 つ)で、漏洩ではない(§8 の sim-own-round で実測)。
  - S-eff の「要約の行の書き方」: 行の文言は M-loop の公開の口で、変えれば M-expect も変わる(公開の契約の変更)。予測「M-loop だけ」が
    指すのは if の書き方など中の形だけ。文を分ける。
  - S-st の「記録を fd へ」: `RunProcess` に fd を子へ渡す欄が無く、依存先の契約の拡張が要る。予測の外れとして認める(file の
    区切りを変える形は §8 の st-sep-both で M-runner と M-make だけに収まった)。

## 6. 盲検 B — 共有の一巡に別の模擬の結末を相乗りさせると、rc≠0 の判定の意味が黙って変わる

- 起動と入力: blind-meta.md・blind-input.md。返答(未加工): blind-B-response.md。
- 失敗例: 収集 0 件(rc 5)の session を赤と分けて名指す機能を、新しい検が自分の一巡を回さず fixture `packages_round` に相乗りして
  足す(最後の package を rc 5 にする)。提示の検と ruff は通り、その上で「赤だけの日に rc 0」の変異(Makefile の最後の行を
  `[ -z "$$empty" ]`)を入れても通る — 訪ねの検の rc≠0 が rc 5 の session から来るため。
- 再現(c854bccaf・evidence/blind-B/B-repro-on-c854bccaf.txt — 差分は evidence/blind-B/apply.py): 手順 1 で 4 passed(ruff の 1 件は
  手を付けていない行の既存の PT018)・手順 2 で 4 passed(見逃し)・手順 3 の使い捨ての検で「赤を名指したのに rc 0」・手順 4(対照: 差分なしで
  `exit 1` を消す)は訪ねの検が赤。成立。
- 分類: 共有の状態(M-round の模擬の結末)に 2 人目の書き手が入り、M-checks の判定の前提(赤は最初の package だけ)が黙って崩れる。
  S-sim の主張「新しい結末は自分の一巡で」を強制する物が無かった。
- 直し(e61d80c39): runner は返す rc も記録する。訪ねの検は rc≠0 と名指しを判じる前に「0 以外を返した session がちょうど 1 つで、
  最初の package の tests」を確かめ、外れたら「別の模擬の結末は `_run_test_packages` で自分の一巡を回す」の文で赤。
- 再検証(e61d80c39・evidence/blind-B/B-repro-on-e61d80c39.txt): 手順 1・2 とも訪ねの検が「この一巡で 0 以外を返した session が最初の
  package doeff-adr の 1 つでない」で赤(相乗りを拒む)・手順 4 は「rc が 0」で赤。手順 3 の使い捨ての検は Makefile の変異そのものの
  性質(模型の木での rc 0)を見る物で、検の組の外。主張どおりに自分の一巡で足す形は §8 の sim-own-round で緑、同じ変異は赤。
- B の副次の観察と答え:
  - 緑の検の綴りの比べ = 盲検 A と同じ(直した)。
  - 秒の flag の検は段 packages の形を部分文字列 `make test-packages PACKAGE_UV_RUN=` でしか見ず、段の命令行で他の変数
    (PACKAGE_EXTRA_TEST_ROOTS= など)を上書きされても気づかない。この性質はこの変更の前から在る(10addc647 の同じ行)。この変更の
    範囲の外の限界として §10 に残す。

## 7. 変更シナリオ — 予測と実測

| id | 軸 | 予測(盲検の前) | 実測(e61d80c39) | 差の理由 |
|---|---|---|---|---|
| S-hw | hardware | なし | なし — runner を dash と `bash --posix` で symlink の下の cwd から走らせ、綴り 3 通りで赤の dir を rc 1・他は rc 0・記録の cwd は物理の path。`pwd`(論理)の変種は赤を見落とす | 予測どおり。Mac の実機では走らせていない(限界) |
| S-st | storage | M-runner・M-make | 記録の区切りを \037 に替える変更は M-runner と M-make だけで 3 passed。runner だけ替えると読み手が ValueError で赤(黙って緑にならない)。rc の欄を足した e61d80c39 では `_MakeRound.calls` の型が tuple → `_RunnerCall` に変わり検の読み方も変わった | 区切り(中の形)は予測どおり。欄を足す・型を替えるのは M-make の公開の口の変更。fd へ出す形は依存先の拡張が要る(A) |
| S-eff | effects | M-loop だけ | 根を `tests/` で渡す形(A)は M-loop だけで 3 passed(直す前は緑の検が偽の赤)。退行 6 種(1 つ飛ばす・赤で止める・赤を飲む・外の根を飛ばす・秒の flag を落とす・緑で rc≠0)はどれも M-checks のどれかが赤 | c854bccaf では A の漏洩で M-checks も変わる必要が在った → e61d80c39 で直した。要約の行の文言は公開の口(A) |
| S-conc | concurrency | (a) なし (b) M-loop だけ | (a) xdist 3 worker で 3 passed (b) package の loop を並べ pid ごとに wait して赤を集める Makefile で 3 passed(M-loop だけ)・素の wait で赤を落とすと訪ねの検が「rc が 0」で赤 | 予測どおり |
| S-sim | simulation | M-runner・新しい検 | rc 5 の結末を自分の模型の木の一巡で見る形は M-loop(機能の本体)・M-runner・M-make(env の配線と引数 1 つ)・新しい検で 4 passed、M-round と既存の検は不変。同じ機能の「赤だけの日に rc 0」の書き損じは訪ねの検が赤。共有の一巡に相乗りする形(B)は訪ねの検が赤 | M-make の配線の変更は予測の外(A の指摘)— 意図した口の拡張。相乗りの拒みは e61d80c39 で入れた |
| X-dist | distribution | 対象外 | — | 検は 1 機体の 1 process で自分の checkout を読み、機体をまたぐ状態を持たない |

## 8. 正常例と失敗例の実行記録(e61d80c39)

| 記録 | 変種 | 結果 |
|---|---|---|
| evidence/focused-tests-on-e61d80c39.txt | 変更なし・file 全体 | 15 passed(load 90)・ruff は既存の PT018 の 1 件 |
| evidence/validate/none-on-e61d80c39.txt | 変更なし・make の検 | 3 passed |
| evidence/validate/trailing-slash-on-e61d80c39.txt | 盲検 A の差分 | 3 passed |
| evidence/validate/skip-one-package-on-e61d80c39.txt | doeff-hy を飛ばす | 訪ねの検が赤 |
| evidence/validate/stop-at-first-red-on-e61d80c39.txt | `|| exit 1` | 訪ねの検が赤 |
| evidence/validate/swallow-red-on-e61d80c39.txt | `|| true` | 訪ねの検が赤 |
| evidence/validate/skip-extra-roots-on-e61d80c39.txt | 外の根を飛ばす | 緑の検と訪ねの検が赤 |
| evidence/validate/drop-durations-min-on-e61d80c39.txt | `--durations-min=10` を落とす | 秒の flag の検が赤 |
| evidence/validate/and-summary-on-e61d80c39.txt | 緑の時に rc 1(G1) | 緑の検が赤 |
| evidence/blind-B/B-repro-on-e61d80c39.txt | 盲検 B の相乗りと変異 | 訪ねの検が赤(相乗りを拒む) |
| evidence/scenarios/st-sep-both-on-e61d80c39.txt | 区切りを両側で替える | 3 passed |
| evidence/scenarios/st-sep-runner-only-on-e61d80c39.txt | 区切りを runner だけ | 3 本とも赤(ValueError) |
| evidence/scenarios/conc-parallel-ok-on-e61d80c39.txt | 並べて pid ごとに wait | 3 passed |
| evidence/scenarios/conc-parallel-lost-on-e61d80c39.txt | 並べて素の wait | 訪ねの検が赤 |
| evidence/scenarios/conc-xdist-on-e61d80c39.txt | `-n 3` | 3 passed |
| evidence/scenarios/sim-own-round-on-e61d80c39.txt | rc 5 を自分の一巡で | 4 passed |
| evidence/scenarios/sim-own-round-bug-on-e61d80c39.txt | 同じ + 赤だけの日に rc 0 | 訪ねの検が赤 |
| evidence/scenarios/hw-on-e61d80c39.txt | dash・bash --posix・symlink の下の cwd・論理 pwd の変種 | 正しい runner は赤の dir だけ rc 1・論理 pwd の変種は見落とす |

変異と変種の当て方は evidence/scripts/(mutate.sh・scenarios.py・run_scenarios.sh・hw.sh)と evidence/blind-B/(apply.py・repro.sh)。
どれも作業木の Makefile と検の file を写しから戻し、戻った事を記録の終わりの行(`== Makefile restored` / `== restored`)に残した。

## 9. 時間(R3)— zeus・`--durations=0` の setup + call

| 版 | load(1 分) | 対象の 2 本 | 緑の検(足した物) |
|---|---|---|---|
| 10addc647 | 51〜62 | 8.34 秒 | — |
| 10addc647 | 100〜107 | 18.36 秒・16.87 秒 | — |
| 4bb2e090c | 64.9 | 1.43 秒 | — |
| e61d80c39 | 90 前後 | 1.17 秒(fixture 1.11 + 0.06 + 0.00) | 0.09 秒 |

load 65〜70 のちょうどの帯は未測。それより重い load で 2 秒を切る。

## 10. 強制方法と限界

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 記録の形・path の解き方は M-runner と M-make だけが知る | frozen dataclass `_RunnerCall` と口 `dirs()`(検は calls を型で受け、解きは 1 か所) | tests/test_daily_test_population.py | make test-changed(contract-tests prefix "")・日次の段 root | 検が record の file を自分で開く形・自分で `Path(...).resolve()` する形を静的に拒む規則は無い(tests/ は品質検査の対象に未登録) |
| 共有の一巡の模擬の結末は「最初の package だけ赤」の 1 つ | 訪ねの検が 0 以外を返した session を数える | `test_make_test_packages_visits_every_package_after_a_red` | 同上 | 共有の一巡に相乗りした新しい検が rc 0 の結末(例: 遅い session の模擬)だけを足す形は数えない |
| M-loop の退行が赤になる | 振る舞いの検 3 本 | 3 本 | 同上 | session を PACKAGE_UV_RUN の外で起こす形は見えない(P1)。段 packages の命令行の変数の上書きは見ない(B の副次・この変更の前から) |
| 一巡は module で 1 回・runner は sh | fixture の scope="module"・sh の script | `packages_round`・`_FAKE_RUNNER` | 同上 | 2 つ目の一巡を回す・runner を Python に戻す費用の退行を拒む検は無い。2 秒の予算は load に依り検で守らない(make test-changed の 60 秒の枠 — card ki-d4b90a1d7577 — が観測の口) |
| Mac で同じに振る舞う | POSIX の sh の builtin だけ | `_FAKE_RUNNER` | — | Mac の実機では走らせていない(dash と bash --posix で代えた) |

## 11. 未確認事項

- Mac の実機での実行(S-hw は Linux の dash と bash --posix で代えた)。
- 盲検の実際の model と effort(blind-meta.md — 要求は Opus 5.5・xhigh、観測は未確認)。
- 範囲の外: root の conftest.py の session の終わりの .pyc の走査(file 全体で最後の検の片付けに 9 秒 — card ki-16954373dfe0)。
