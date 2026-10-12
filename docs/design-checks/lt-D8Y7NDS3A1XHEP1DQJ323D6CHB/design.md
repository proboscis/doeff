# 設計(盲検の前に固定した主張)— coordinator の process 自身の行を Pod の入れ替えの後も読む

依頼 lt-D8Y7NDS3A1XHEP1DQJ323D6CHB・card ki-0b0ab40048b9。完了の範囲: **実装完了まで**(doeff a1d17b0dd・agora 09e8fd51・doeff dc3385b9a の宣言は main に入った。cluster への当ては配備の担当が resume する — 当たった後の受入の確かめは本文の「予定」)。
基準の版: doeff a1d17b0dd3047d1111f442e5a1ba4348ea3eab05(実装)。宣言は dc3385b9a(a1d17b0dd の子孫)。

## 要件(受入)
- coordinator の Pod が入れ替わった後も、前の Pod の coordinator の行を会話から読める事を、版上げ 1 回の前後で確かめて card に書く。
- 背景: coordinator の Pod の log(kubectl logs)は Pod の入れ替え(版上げ — 30 時間に 11 回)で消え、10-12 08:12・08:45〜09:05 JST に全部の worker が coordinator に届かなかった窓の coordinator の側を後から読めなかった。Loki に namespace agent-worker は入っていない。

## 形
worker の process 自身の行を送った 6c385dbb7(card ki-35923a5895b2)と同じ部品で、coordinator も自分の Python の出力(sys.stdout・sys.stderr)を、job の log の取り決め job-log/1(OTLP/HTTP の logs → claude-telemetry の otel-collector → ClickHouse の job_logs → agora job-log の読み)へ送る。

## 責務(module)
| id | 置き場 | 持つ物 | 隠す物 |
|---|---|---|---|
| M-stamp | doeff `packages/doeff-cluster/src/doeff_cluster/worker/entry/line_stamp.py` | 刻の付け方・OTLP の body の形・溜めと捨て方・送りの thread・送り先の決め(job_log_plan / process_log_plan / worker_log_plan / coordinator_log_plan)・Python の流れの差し替え(process_lines・stamped_streams・merged) | OTLP の JSON の形、送りの上限、属性の名(doeff.worker 等)、流れの差し替えの仕方 |
| M-entry | doeff `packages/doeff-cluster/src/doeff_cluster/coordinator/entry/main.hy` | 起動の時に 1 度、名の組 COORDINATOR-LOG-ENV-NAMES を ReadEnvironment で読み(log-environment)、process-lines を :merged True で呼ぶ事・世代 = 起動の刻 | どの名を読むかは M-stamp の組に任せる。送り先の値・形は知らない |
| M-core | doeff `packages/doeff-cluster/src/doeff_cluster/coordinator/core/program.hy` | 調停ループと、出す行(slog — 例 heartbeat の返事の遅れの行) | 行がどこへ送られるかを知らない(slog-handler が sys.stderr へ書く) |
| M-decl | doeff `packages/doeff-cluster/deploy/k8s/coordinator/coordinator.yaml` | coordinator の版・Pod の名乗り POD_NAME・POD_NAMESPACE(downward API)・envFrom coordinator-env | 上に載る系の語(送り先の値)を持たない(test_declarations_name_no_upper_system) |
| M-upper | agora `deploy/agent-worker/kustomization.yaml` の ConfigMap coordinator-env | 送り先 DOEFF_JOB_LOG_OTLP(cluster の中の otel-collector の名)・名乗り DOEFF_JOB_LOG_ATTRIBUTES=agora.tenant=personal | doeff の code の形を知らない(名だけの取り決め) |
| M-store | agora-controllers `deploy/claude-telemetry`(collector の振り分け・job_logs の表)と agora job-log の読み | 取り決め job-log/1(service.name=doeff.job-log で振り分け・agora.tenant で口座・job の名と刻で引く) | 送り手が worker か coordinator かを区別しない |

公開契約:
- `coordinator_log_plan(environ: Mapping[str,str], instance: str) -> JobLogPlan`(JobLogTarget | JobLogOff | JobLogRefused)。送り先が無ければ JobLogOff。送り先が在り POD_NAME・POD_NAMESPACE が欠ければ JobLogRefused(欠けた名を挙げる)。それ以外は job の名 `coordinator/<POD_NAMESPACE>`・doeff.worker = POD_NAME・doeff.attempt = 0・doeff.instance = instance。
- `process_lines(plan, merged: bool = False) -> RawLines | StampedStreams`。merged が真なら stdout と stderr が別の file でも差し替え、刻つきの行を stdout の file へ書く。fd 1・2 は替えない。終わりは atexit。
- `COORDINATOR_LOG_ENV_NAMES = (DOEFF_JOB_LOG_OTLP, DOEFF_JOB_LOG_ATTRIBUTES, POD_NAME, POD_NAMESPACE)`。
- 読み手: `agora job-log coordinator/agent-worker --since 6h [--worker <Pod の名>]`。

不変条件:
- 送りは coordinator の書きを待たせない(上限つきの溜め・溢れは捨てて数える・送りは別の thread)。止まりの行は送りの上限(ShipLimits.end_seconds)の内で送ってから終わる。
- 送り先の無い coordinator は出力の向きを替えない。
- 宣言(M-decl)は上に載る系の語を持たない。

## 将来の変更のシナリオと主張(盲検の前に固定)
| id | 軸 | 変更 | 主張 | 予想の範囲 |
|---|---|---|---|---|
| S-store | storage | 行の置き場(受け手の collector・店)を替える | 値 DOEFF_JOB_LOG_OTLP(M-upper)と M-store だけが変わり、doeff の code(M-stamp・M-entry・M-core)と宣言(M-decl)は変わらない。使えない URL は送らずに理由を出し、coordinator は走り続ける | M-upper・M-store |
| S-dist | distribution | 別の namespace の coordinator(例 lab)も送る | その宣言に POD_NAME・POD_NAMESPACE と送り先の ConfigMap を足すだけで、job の名が namespace で分かれ、code は変わらない。名乗りが欠ければ共有の名で混ぜずに送らない | M-decl(相手の宣言)・M-upper(相手の ConfigMap) |
| S-conc | concurrency | 送り先が遅い・答えない・落ちている | coordinator の書きと止まりは送りに待たされない(M-stamp の溜めと上限が持つ)。M-entry・M-core は変わらない | なし(M-stamp の不変条件) |
| S-eff | effects | coordinator に新しい行(例 歩の遅れの区間の名指し)を足す | M-core の slog を足すだけで送られる(slog-handler が sys.stderr へ書き、差し替えた流れが拾う)。M-stamp・M-entry は変わらない | M-core |
| S-hw | hardware | (除外) | coordinator の置き場の node は M-decl の nodeSelector だけが持ち、送りは node に依らない(送り先は cluster の中の名) — 今回の変更は node の知識を足していない | — |
| S-sim | simulation | (除外) | 手元の模擬の cluster(sim)は coordinator の Program(run-coordinator)を入口 main を通さずに走らせるので、送りの経路の外。模擬の coordinator は送らない | — |

## 強制方法
| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 送り先の決めは M-stamp が持ち、名乗りの欠けは送らない | 判別できる union JobLogPlan と match の網羅(pyright)・純粋な検 | line_stamp.py・tests/test_job_log_ship.py::test_the_coordinator_names_itself_after_its_pod | マージの列の検・日次の全体 | 名の値の意味(namespace が正しいか)は検べない |
| 本物の入口が送る(配線) | 本物の coordinator の入口を stdout と stderr を別の file にして起こす検 | tests/test_job_log_ship.py::test_the_coordinator_ships_its_own_lines_from_two_pipes_until_it_stops・test_a_coordinator_without_the_destination_keeps_its_two_streams | 同上 | 本物の k8s の pipe ではなく 2 つの file |
| 宣言に上に載る系の語が無い・版の向き | tests/test_k8s_declarations.py(semgrep の規則の語) | test_declarations_name_no_upper_system・test_no_worker_is_newer_than_the_coordinator | 同上 | — |
| 送り先の値は ConfigMap が持つ | agora deploy/agent-worker/test_handover_to_doeff.py の coordinator-env の検 | test_the_coordinator_env_configmap_has_a_fixed_name_and_carries_only_the_broker_and_the_log_destination | agora のマージの列 | 値に届くかは cluster の中で確かめた(POST 200)だけ |
| coordinator → worker/entry の import を許す | code-quality の module_extensions(doeff-cluster-coordinator → doeff-cluster-line-stamp) | .agents/code-quality.d/doeff-cluster-line-stamp.json | code-quality の full の段 | 手元では機体の負荷で未実行 |

---

# 盲検の後(design-pre-blind.md の上の節は盲検の前の主張のまま — sha256 93eda1c81401fe3f9173e3c3580c1f1033943275d06dc65f304a7c10e060456e)

盲検 A・B の起動は blind-meta.md、未加工の返答は blind-A-response.md・blind-B-response.md。

## 盲検 A(S-conc)— 成立した・境界を直した
- 失敗例: 止まる時に残りの行を送る上限 ShipLimits.end_seconds は全部の送り手で共通の 0.5 秒(shim の掃除の余裕 1.5 秒から導いた値)。送り先が遅い時、POST の答えを待つ間に coordinator が止まると、止まりの行が送られずに消える。coordinator だけ値を変える口が M-stamp の公開契約に無かった。
- 再現(設計者): tests/test_job_log_ship.py::test_a_slow_destination_still_gets_the_stop_line_of_the_coordinator(POST ごとに 2 秒かかる受け手・最初の POST を受けた後に SIGTERM)を直す前の code で走らせ、止まりの行が届かず赤(evidence/before-fix-A-B.log.txt)。
- 予想との差: S-conc の予想の範囲は「なし」だったが、実際は M-stamp の公開契約(limits を受ける口)と M-entry(値を選ぶ)が変わった。原因は漏洩 — 「止まる時に送りへ何秒使えるか」は載っている process の止め方の知識(shim は worker の掃除の余裕・coordinator は Pod の止めの猶予)なのに、M-stamp が shim の値 1 つに固めていた。
- 直し: process_lines・stamped_streams・shipping_of が limits(既定 SHIP_LIMITS)を受け、coordinator の入口が `(replace SHIP-LIMITS :end-seconds LOG-END-SECONDS)`(10 秒 = 1 回の POST の待ちの上限 5 秒 × 2・Pod の止めの猶予 30 秒の内)を渡す。M-stamp の既定は shim のまま(shim の時間は変えない)。
- 再検: 同じ検が緑(evidence/after-fix.log.txt — 45 passed)。
- 残る共同の不変条件: coordinator の LOG-END-SECONDS + 止まる時間 ≤ Pod の止めの猶予(宣言に書かない既定 30 秒)。値の註に書いた(main.hy)。検では見張らない(未確認事項ではなく、既定の猶予を変える宣言が来た時に読む註)。

## 盲検 B(S-dist)— 成立した・検を足した
- 失敗例: 入口(M-entry)が名乗りの欠けを HOSTNAME と既定の namespace `agent-worker` で補うと、送るかの判断が coordinator_log_plan から入口へ移り、名乗りを置き忘れた別の namespace の coordinator が本番の job の名 coordinator/agent-worker で行を混ぜる。提示した検は全部通った。
- 再現(設計者): B の差分(evidence/blind-B-fallback.patch.txt)を main.hy に一時的に当て、既存の coordinator の検 3 本が緑のまま、足した検 test_a_coordinator_without_its_pod_names_ships_nothing_and_names_why の 2 つ(POD_NAME だけ・HOSTNAME だけ)が赤になる事を見た(evidence/blind-B-applied.log.txt)。差分は git apply -R で戻した。
- 予想との差: 漏洩(名乗りの出どころ = M-decl の知識が M-entry へ写る)を、本物の入口を通す検が見ていなかった(既存の検は名乗りを必ず渡していた)。
- 直し: 本物の入口で「送り先は在るが名乗りが欠けた」2 つの形を起こし、送らない訳の 1 行が出て何も送らない事を見る検を足した。
- 副の所見(B の 5): code-quality の module doeff-cluster-coordinator は core の program.hy と入口の main.hy を 1 つに持ち、入口のための line_stamp の許しが core にも効く。semgrep の規則 doeff-cluster-coordinator-core-does-not-know-where-its-lines-go(.semgrep.yaml)で core からの import を断る。陽性対照の fixture と検 tests/semgrep/test_coordinator_core_lines_rule.py(fixture の 2 行目だけ当たる・入口と本物の core は緑)。本物の program.hy に import を 1 行足した変異で規則が当たる事を見た(evidence/core-import-mutant.log.txt)。

## 実装の証拠(直した後の版)
| 強制 | 実装 | 正常例 | 失敗例 | 経路 |
|---|---|---|---|---|
| 送り先の決め・名乗りの欠けは送らない | line_stamp.coordinator_log_plan | evidence/plan-scenarios.log.txt(S-store・S-dist の正) | 同(使えない URL・名乗りの欠け) | tests/test_job_log_ship.py(マージの列・日次) |
| 本物の入口の配線・名乗りを補わない | coordinator/entry/main.hy・test_a_coordinator_without_its_pod_names_ships_nothing_and_names_why | after-fix.log.txt | blind-B-applied.log.txt | 同上 |
| 止まりの行を送ってから終わる | process_lines の limits・main.hy の LOG-END-SECONDS・test_a_slow_destination_still_gets_the_stop_line_of_the_coordinator | after-fix.log.txt | before-fix-A-B.log.txt | 同上 |
| core は送り先を知らない | .semgrep.yaml の規則・tests/semgrep/test_coordinator_core_lines_rule.py | after-fix.log.txt(入口と core は緑) | core-import-mutant.log.txt | commit の hook(semgrep Hy architectural rules)・tests/semgrep |
| 本番で新しい Pod の行が読める | a1d17b0dd・agora 09e8fd51・dc3385b9a(10-12 17:52 JST に当たった) | evidence/live-new-pod-ships.log.txt(agora job-log coordinator/agent-worker が kubectl logs と同じ行) | — | 本番 |

## 予定(この報告の範囲の外 — 受入の残り)
- 直した版(盲検 A の limits と B の検)を main に入れ、coordinator の版をその版へ上げる入れ替えを配備の担当に頼む。その入れ替えの後に、前の Pod(coordinator-797dcbcbdb-msblk)の行を `agora job-log coordinator/agent-worker --worker coordinator-797dcbcbdb-msblk` で読み、止まりの行まで在るかを card ki-0b0ab40048b9 に書く(受入の条 cfb963afa)。
- code-quality の full の段は手元で機体の負荷により未実行(マージの列の段が走らせる)。

## 版
- 基準: doeff dc3385b9a4bb105937d9dad6671545f7b34c80f2(a1d17b0dd の実装と宣言を含む main)。evidence の「作業木の直し」= 同じ branch の commit e1361b759(盲検 A・B の直し — マージの列の rebase で sha は変わりうる。題で引く)。
