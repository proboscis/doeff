# 設計検証 — worker の process 自身の行を job の log の置き場へ送る(lt-8PQM3S5DDHCSJ92Q7X259K9HAW・card ki-35923a5895b2)

## この文書の目的

読み終えた人が次の 3 つを判じられるようにする。

1. 機体の worker(zeus-native・mac-native)の process の行を会話から読むための変更が、どの責務に閉じているか。
2. 盲検で見つかった 2 つの失敗例が、どう直り、どの検が次の違反を止めるか。
3. 何が未確認で、どこで測るか。

## 0. 全体の図(直す前 6c385dbb7 ⇒ 直した後 64cd93dfa)

```
直す前(6c385dbb7 — fd 1・2 を pipe へ dup2)
  worker の process(hy -m doeff_cluster.worker.entry.main)
    sys.stderr(slog・print・traceback)──┐
    fd 2 へ直に書く物(Rust の panic)────┼──▶ fd 1・2 = pipe の書き口 ──▶ 書き手の thread(pumped)──▶ 元の file(~/.local/state/doeff-agent-worker.log)
    子孫(lifeline の見張り — fd 2 を継ぐ)─┘                                     └──▶ 送りの thread(JobLogShipper)──▶ collector ──▶ job_logs
    ※ worker が終わると pipe の読み手が居なくなり、子孫の書きは消える(盲検 A)

直した後(64cd93dfa — Python の流れだけを差し替える)
  worker の process
    sys.stdout・sys.stderr(slog・print・traceback)──▶ 流れ(os.fdopen の pipe の書き口)──▶ 書き手の thread(pumped)──▶ 元の file(刻つき)
                                                                                                     └──▶ 送りの thread(JobLogShipper)──▶ collector ──▶ job_logs(job = worker/<名>)
    fd 2 へ直に書く物・子孫(fd 1・2 = 元の file を継ぐ)────────────────────────────────────────▶ 元の file(刻なし・送らない)
```

## 1. 完了の範囲

| 段 | 状態 |
|---|---|
| 設計 | 済み(盲検の前の主張 = design-pre-blind.md・sha256 95fdc07d61bcb12867de2eb96c6620e8618dd699cd098068a7de5cc90d456eb5・commit 89a260cfc) |
| 実装 | 済み。doeff 6c385dbb7(L2173)・agora-controllers 48dfe18a(L6371)。盲検の直しは doeff 64cd93dfa(この記録と同じ branch・着地はこの記録と一緒) |
| 配備 | 範囲の外。coordinator の版上げ → dotfiles の WORKER_DOEFF_COMMIT → 機体の worker の起こし直し(起こし直しの決まりに従う)。受入の条 ccd8d5cc8 は配備の後に読んで確かめる |

## 2. 責務(module)

| id | 置き場 | 持つ判断・知識 | 隠す物 |
|---|---|---|---|
| M-writer | line_stamp.py の pumped・StampedLines(job)・StampedStreams(worker) | pipe を読み、行の頭に刻を足して元の file へ書く。終わりに読み切る | pipe と fd・刻の綴り |
| M-shipper | line_stamp.py の JobLogShipper・logs_body・ShipLimits と otlp_post.py | OTLP の JSON・溜めの上限・束ね・POST・捨てた行の数え | 送りの thread・上限・HTTP |
| M-plan | line_stamp.py の job_log_plan・worker_log_plan・WORKER_LOG_ENV_NAMES | 送るか・どこへ・何と名乗るか(job の名 worker/<名>・試み 0・世代 boot) | 環境変数の名・名乗りの決まり |
| M-process | line_stamp.py の process_lines・stamped_streams | worker の process で何を差し替えるか(Python の流れだけ)と、終わりを atexit に置くこと | 流れの差し替え・same_file・atexit |
| M-entry | worker/entry/main.hy | 組み立て: 名の組を ReadEnvironment で読み、boot を決め、process-lines を 1 度呼ぶ | — |
| M-contract | agora-controllers deploy/claude-telemetry と test_job_log_route.hy | 取り決め job-log/1(振り分け・列・1 行の上限・印の名) | 店の種類・口座の DB |
| M-reader | agora-controllers daily_verify の job_log_store・API・CLI agora job-log | Job・Worker・刻で引いて返す | SQL・店の接続 |

## 3. 盲検と、その再現

起動の記録は blind-meta.md。返答は blind-A-response.md・blind-B-response.md(加工なし)。

### 3.1 盲検 A — worker より長く生きる子孫へ pipe が漏れる

| 項 | 内容 |
|---|---|
| 失敗例 | lifeline の見張り(doeff-core-effects)は stderr を指定せずに起こされ、worker の fd 2 を継ぐ。6c385dbb7 では fd 2 が pipe なので、worker が終わった後の見張りの書きは読み手の居ない pipe へ行く。見張りに記録の 1 行を足すだけで、行が消え、見張りが KILL の前に止まり、TERM を無視する子が残る |
| 前提 | 主張の前提の内(機体の worker・送り先あり・同じ file) |
| 漏れた知識 | 「この process の fd 2 は worker の中の thread だけが読む pipe」— M-writer・M-process が隠すはずの事 |
| 再現(直す前) | evidence/A-repro-before-fix.log.txt — case E(今の見張り)は見張りの fd 2 が `pipe:[…]`。case C(1 行を足した見張り)は子が残り(child_group_still_alive true)、行は file に無い。case B(送り先なし)は行が在り子も止まる |
| 成立 | 成立。見張りの終了コード 120 の原因(BrokenPipeError)は推測のまま |

### 3.2 盲検 B — 書き手が送りを待つ変更が、どの検も通る

| 項 | 内容 |
|---|---|
| 失敗例 | 「worker の行だけは溜めが満ちたら最大 2 秒待ってから捨てる」— ShipLimits に待ちを足し、process_lines が worker 用の上限を選ぶ。判断が M-shipper から M-process へ移り、書き手が POST の進みを待つ |
| 破れる主張 | 公開の契約「送りは worker の write を止めない」・S3・S5 |
| 再現(直す前) | evidence/B-repro-before-fix.log.txt。2 万行では差が出なかった(この機体では溜めが間に合った)。4 万行で、今の版は process 1.1〜1.3 秒、差分の版は 27.5〜30.4 秒・heartbeat 役の 1 回の write の待ちが最大 4.05 秒 |
| 検を通った理由 | worker の道で溜めを満たす検が無かった(溢れの検は ShipLimits を明示して作る)。module 契約は line_stamp.py を 1 つの module として見るので、file の中の判断の移動は見えない |
| 成立 | 成立 |

## 4. 直し(doeff 64cd93dfa)

| 失敗例 | 直し | 置き場 |
|---|---|---|
| A | worker の差し替えを Python の流れ(sys.stdout・sys.stderr)だけにし、fd 1・2 は元の file のまま。pipe の fd は継がれない(os.pipe の既定)。終わりに流れを戻し、書き口の fd を元の file へ付け替えて pipe を EOF にしてから読み切る | line_stamp.py の StampedStreams・stamped_streams・process_lines |
| A(参考の候補) | worker の環境から読む名を 2 か所に並べない: WORKER_LOG_ENV_NAMES を worker_log_plan と入口が読む | line_stamp.py・main.hy |
| A の検 | worker より長く生きる子孫の行が file に在る | tests/test_job_log_ship.py の test_a_descendant_that_outlives_the_worker_still_writes_to_the_file |
| B の検 | worker の道(本番の上限)で、答えない送り先の下で 4 万行を書いても 10 秒の内に終わる | tests/test_job_log_ship.py の test_a_flooding_worker_does_not_wait_for_a_silent_destination |

直しの代わりに失ったもの: fd へ直に書く物(Rust の panic など)と子孫の行は、元の file へ刻なしで書かれ、送らない。worker の Python の出力(slog・print・traceback)は送る(slog は呼ぶ時の sys.stderr へ書く — doeff_core_effects/handlers.py)。

## 5. 事前の予測と実測

| id | 軸 | 事前の予測(design-pre-blind.md) | 実測 | 差の訳 |
|---|---|---|---|---|
| S1 | hardware | 機体を足すのは配備だけ | 予測どおり。eos-native の名乗りは worker/eos-native(C-S1-P)。stdout と stderr が別の file の機体では差し替えない(C-S1-N) | — |
| S2 | storage | 口座を替えるのは配備の属性と M-reader の口座の側だけ。配備の属性は doeff の名乗りを書き換えられない | 予測どおり(C-S2-P・C-S2-N) | — |
| S3 | effects | 送り先が落ちても worker の write と終わりは待たされず file は全部残る。何も変えない | 6c385dbb7 の code では成り立った(C-S3-P)。ただし破る変更を止める検が無かった(盲検 B) | 漏れではなく検の穴。B の検を足した(C-S3-N) |
| S5 | concurrency | 子孫と thread の書きは 1 本の pipe に入り、M-entry と Program は知らない | 6c385dbb7 では子孫が pipe を継ぎ、見張りの 1 行の変更が M-writer・M-process(と見張り)を変えないと直らない形だった(盲検 A) | 隠す知識の漏洩。64cd93dfa で fd を替えない形にし、同じ見張りの変更は見張りの中で閉じる(C-S5-P・A-repro-after-fix.log.txt) |
| S4 | distribution | 除外(Pod の worker の行は kubectl logs で読める) | — | — |
| S6 | simulation | 除外(差し替えは入口の層の OS の扱い・模擬の worker は main を通らない) | — | — |

盲検の前の主張は書き換えていない(design-pre-blind.md は commit 89a260cfc のまま)。盲検 B が指摘した数の食い違い(「worker の 4 本と既存の 27 本」)は誤りで、版 b85e20d81 の test_job_log_ship.py は合わせて 27 本(worker 4・既存 23)。直した後は 29 本。

## 6. 強制方法(直した後)

| 守る責務 | 強制方法 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| line_stamp を使えるのは shim・warm_child・worker の入口・検だけ | code-quality の module 契約 | .agents/code-quality.d/doeff-cluster-line-stamp.json | commit の hook・日次 | file の中の判断の移動は見えない(盲検 B)— 下の検で補う |
| 入口は os.environ を直に読まない | doeff-linter DOEFF106(Hy だけ) | main.hy は ReadEnvironment で WORKER-LOG-ENV-NAMES を読む | commit の hook・日次 | line_stamp.py の中の os の呼びは見ない |
| 子孫は元の file を継ぐ(pipe を継がない) | 本物の process の検 | test_a_descendant_that_outlives_the_worker_still_writes_to_the_file | 日次の packages の段 | Linux だけで測った |
| worker の write と終わりは送りに待たされない | 本物の process の検(時間の上限 10 秒) | test_a_flooding_worker_does_not_wait_for_a_silent_destination | 日次の packages の段 | 時間で判じる。待ちを 10 秒より短く刻む変更(例: 4 万行で計 10 秒未満)は通る |
| 名乗りと file の完全さと traceback | 本物の process の検 | test_job_log_ship.py の worker の 6 本 | 日次の packages の段 | Mac(launchd)は未実測 |
| 送り手の 1 行の上限・印の名が取り決めと同じ | 契約の検 | agora-controllers test_job_log_route.hy | agora-controllers の日次 | job の名の綴り worker/<名> は検しない |
| 機体の worker の版 ≦ coordinator の版 | dotfiles の検 | dotfiles agent/tests/test_doeff_host_worker.py | dotfiles の全体検証 | 起こし直しの時期は縛らない |

静的に表せない条件: 「worker より長く生きる子孫の行を送る」ことはこの設計の外(子孫の行は file にだけ在る)。

## 7. 実装の証拠

| 証拠 | 中身 |
|---|---|
| evidence/negative-controls.log.txt | 直す前の版(6c385dbb7 の line_stamp.py)は子孫の検で赤・溢れの検で緑。直した版に盲検 B の変更を当てた写しは溢れの検で赤(27.3 秒)・子孫の検で緑。写しが読まれた事も確かめた |
| evidence/A-repro-after-fix.log.txt | 直した版で盲検 A の再現: 見張りの fd 2 は worker.log。1 行を足した見張り(C)と worker の SIGKILL(D)でも、行は file に在り子は止まった |
| evidence/S3-and-tests.log.txt | 送り先が落ちた時の worker(200 行と traceback が file に在り終了コード 1・0.32 秒)と、検の名ごとの結末(12 本 PASSED) |
| evidence/S1-S2-plan.log.txt | 名乗りの正と負の確かめ |
| evidence/tests-after-fix.log.txt | ruff 緑・test_job_log_ship.py 29・test_line_stamp.py 8・worker の入口を読む検 4 file 15 passed |
| evidence/lint-after-fix.log.txt | doeff-cluster の linter(HEAD の鍵の binary・warning の基点の比べ)rc 0・semgrep 0・pyright の触った 2 file は error 0(warning 1 は検の Hy の module の import で、前から在る) |
| evidence/hy-crash-after-fix.log.txt | `hy -m` で落ちる worker の traceback が file と送り先に在る・worker の入口が読み込める |

## 8. 未確認の事

| 事 | 測る所 |
|---|---|
| Mac(launchd)で stdout と stderr が同じ file と判じられ、行が届くか | 配備の後の受入の条 ccd8d5cc8(agora job-log worker/mac-native) |
| 本番の機体の worker の行の量と、溜めが溢れる頻度 | 配備の後の店の行(番号の飛び) |
| 盲検 A の見張りの終了コード 120 の原因 | 推測のまま(直した後は見張りの fd 2 が file なので起きない) |

盲検 B は doeff の木と ~/repos/agora-controllers に `__pycache__` を書き、~/repos/agora-controllers で `git status` を 1 度実行したと申告した(source は変えていない)。
