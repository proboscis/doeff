# 設計(盲検の前に固定した主張)— coordinator の process 自身の行を Pod の入れ替えの後も読む

依頼 lt-D8Y7NDS3A1XHEP1DQJ323D6CHB・card ki-0b0ab40048b9。基準の版: doeff a1d17b0dd3047d1111f442e5a1ba4348ea3eab05(実装)。宣言は dc3385b9a(a1d17b0dd の子孫)。

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
| 守る責務 | 強制方法 | 実装箇所 | 実行経路 |
|---|---|---|---|
| 送り先の決めは M-stamp が持ち、名乗りの欠けは送らない | 判別できる union JobLogPlan と match の網羅(pyright)・純粋な検 | line_stamp.py・tests/test_job_log_ship.py::test_the_coordinator_names_itself_after_its_pod | マージの列の検・日次の全体 |
| 本物の入口が送る(配線) | 本物の coordinator の入口を stdout と stderr を別の file にして起こす検 | tests/test_job_log_ship.py::test_the_coordinator_ships_its_own_lines_from_two_pipes_until_it_stops・test_a_coordinator_without_the_destination_keeps_its_two_streams | 同上 |
| 宣言に上に載る系の語が無い・版の向き | tests/test_k8s_declarations.py(semgrep の規則の語) | test_declarations_name_no_upper_system・test_no_worker_is_newer_than_the_coordinator | 同上 |
| 送り先の値は ConfigMap が持つ | agora deploy/agent-worker/test_handover_to_doeff.py の coordinator-env の検 | test_the_coordinator_env_configmap_has_a_fixed_name_and_carries_only_the_broker_and_the_log_destination | agora のマージの列 |
| coordinator → worker/entry の import を許す | code-quality の module_extensions(doeff-cluster-coordinator → doeff-cluster-line-stamp) | .agents/code-quality.d/doeff-cluster-line-stamp.json | code-quality の full の段 |

## 読んでよい実コードと検査(読み取りだけ — 書き換え・git の操作をしない)
作業木(doeff main・dc3385b9a): /home/ubuntu/.worktrees/doeff-wt-design-check-0b0a
- packages/doeff-cluster/src/doeff_cluster/worker/entry/line_stamp.py(M-stamp — 頭の註・coordinator_log_plan・process_log_plan・process_lines・stamped_streams・JobLogShipper)
- packages/doeff-cluster/src/doeff_cluster/coordinator/entry/main.hy(M-entry — log-environment・main の頭)
- packages/doeff-cluster/src/doeff_cluster/coordinator/core/program.hy(M-core — note-heartbeat-lags など)
- packages/doeff-cluster/deploy/k8s/coordinator/coordinator.yaml(M-decl)・packages/doeff-cluster/deploy/boot.sh(ROLE=coordinator の起こし方)
- packages/doeff-cluster/tests/test_job_log_ship.py(検 — 末尾の coordinator の節)・packages/doeff-cluster/tests/test_k8s_declarations.py
- .agents/code-quality.d/doeff-cluster-line-stamp.json・.agents/code-quality.d/doeff-cluster-1541.json(module の許し)
上に載る系(読み取りだけ): /home/ubuntu/repos/agora の origin/main の deploy/agent-worker/kustomization.yaml・deploy/agent-worker/test_handover_to_doeff.py(`git -C /home/ubuntu/repos/agora show origin/main:<path>` で読む)。
取り決め job-log/1 の定義元: /home/ubuntu/repos/agora-controllers の origin/main の deploy/claude-telemetry/claude-telemetry.yaml の頭・deploy/claude-telemetry/job_logs.sql。読み手: agent-env/cli/agora_cli/job_log/core/job_log.hy。

検査の実行(試すなら 1 本ずつ・一時 file は mktemp -d の下): 作業木の根で `uv run --group dev pytest packages/doeff-cluster/tests/test_job_log_ship.py -q -k coordinator`(本物の process を起こすので 1 本 30 秒ほど)。
