;;; 手元の runner sim-cluster — 系(defsystem の関数を sim の土台で呼んだ System の値)を、本物の coordinator と本物の worker の上で、
;;; 1 process・仮想の時計で走らせる(ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5・5b)。同じ物を壁の時計で走らせる入口が wall-sim-cluster。
;;;
;;;   (<- answer (sim-cluster (lab sim-foundation) (scenario) :workers #((SimWorker :name "w1" :provides #{"net"} :task-reserve 0)) :environ {"tally" {"STEP" "3"}}))
;;;   (<- answer (wall-sim-cluster (lab sim-foundation) (scenario) :workers #((SimWorker :name "w1" :provides #{"net"} :task-reserve 0))))
;;;
;;; 時計(入口が選ぶ — 内側の仕組みは同じ):
;;;   sim-cluster       仮想の時計(doeff-time の sim-time-handler・起点 start-ms)。時計は scheduler の中の task が全部止まった時だけ進む
;;;                     (検は時間の長い筋書きを実時間の一瞬で回せる)。外の thread・本物の socket の相手とは時刻が合わない。
;;;   wall-sim-cluster  壁の時計(doeff-time の async-time-handler と await-handler — 今の時刻から始まり、Delay は実時間で待つ)。外の
;;;                     thread の客・本物の待ち受け・実時間の遅れを確かめる検と、手元で系を実時間で回す道具のため(#908・#1086)。
;;;                     筋書きは Await を出してよい。job の Await は柵を通らないので、本物の I/O を持つ job は本番の土台と同じく自分の土台に
;;;                     await-handler を並べる(cluster_foundation.hy の見本)か、その I/O を SimOutside の handler に置く。
;;;
;;; 中身(写しを作らない — 起こし直しの間隔・readiness の窓・handoff の期限・needs ⊆ provides の置き方・lease・fence は本物が決める):
;;;   - coordinator の Pod = 本物の run-coordinator を coordinator_handler_sets.emulated-handlers(要求の列 RequestQueue・memory の
;;;     置き場・偽の k8s)の上で回す。置き場は入口の store(MemoryWalStore の値を作る関数)で差し替えられる — 置き場の欠陥が不変条件を
;;;     破るのを確かめる反例の壊れた置き場のため(#989)。止まれば(止めの合図・Persist の失敗)返事の無い要求に接続の失敗を返し、止まっている秒の後に同じ
;;;     置き場から load-state で読み直して作り直す。一番内側の見張り(observe-requests)が、届いた ReportReady / ReportMetrics を世界へ
;;;     記録し、網の切れた worker から届いた要求を落とし(送り手には接続の失敗)、筋書きの止まり・落ちを注入する。
;;;   - worker = 本物の run-worker を、worker ごとの偽の宿(sim-host)の上で回す(worker-keeper が node の一生を持つ — 死んだ・止めた
;;;     worker は StartWorker で新しい世代として起き直す)。heartbeat・状態の報告・lease を返す要求・温める表の行の読み・root の名乗りは
;;;     本番の coordinator への口 と同じ形(handlers.heartbeat-body・status-report・desired-when-unreachable・env-report・env-heartbeat-part・
;;;     warm-env-of-row)。コードの木の準備は SimWorker の prepare-seconds・実行環境の root の準備は env-prepare-seconds(None なら
;;;     prepare-seconds)の後に揃う(既定 0 = 即座・#2879)・env-failure を
;;;     持つ worker の実行環境の準備はその失敗で終わる。入口の検めは通る(準備の層そのものは env_world と丁寧な模擬の検が持つ)。
;;;   - 偽の宿の StartJob は、coordinator の /programs/<sha> から取った詰めた文字列を decode し(検の物と object を共有しない・運べる値か
;;;     を検める)、「柵(fence)→ クラスタの約束の答え(coordinator-answers)→ 宿の答え(host-answers)→ Program」の順に包んで、宿の
;;;     handler の節の中から Spawn する。Spawn は節の外側の handler(世界・時計)だけを持ち運ぶので、run-worker の中の handler も、他の
;;;     job の handler も混ざらない(service ごとの別のスコープ)。終わり(値・例外・取り消し)は世界へ書き、ObserveWorld が exit-code として
;;;     返す。process の中で Spawn した task は柵が tracked-child で包み直して(元の継続のまま — 子の task は柵より内の handler を持ち
;;;     運ぶ)把手を世界に覚えさせ、process の終わり(値・例外・止めの合図)で一緒に取り消し、殺された時(Crash・worker の死)は
;;;     一緒に捨てる(Discard — 巻き戻さない。本番は子 process ごと消える)。task の process は終わりを書く前に、結果を
;;;     coordinator へ直に届ける(本番の job_entry.run-task と同じ要求 —
;;;     task_result.task-result-request・#1387。届かなければ worker の heartbeat が運ぶ)。
;;;   - 宿の答え(host-answers — process ごと)= host_contract.HOST-CONTRACT の 3 つ(run-context・Program の path・宣言の environ の名の
;;;     Ask — environ は本番の土台の os-environ-reader と同じ答え方の host_contract.environ-table-reader を子の spec.environ の上に並べる:
;;;     値は字面どおり)と ReportReady・ReportMetrics。クラスタの約束の答え(coordinator-answers — 送り手の口 SimLink ごと)= ReadShared / WriteShared・
;;;     LeaseOp・AwaitLeaseFree・RemoteJob・SubmitDetached / AwaitDetached / CancelDetached / ReleaseDetached / ReadRunners / ReadServices / AwaitRunnersChange・WarmRuntimeEnv /
;;;     ReadWarmState。
;;;     本番では土台の HTTP の handler が coordinator へ送る物で、要求の形は本番の送り手と同じ関数(service_report.report-request・
;;;     shared_handlers.board-*-request / lease-request・remote.task-submit-body / outcome-of / settled-value・detached.detached-path /
;;;     detached-submit-body / detached-refusal / awaited-answer / warm-request-body)。何度送っても同じ意味の要求(読み・lease の claim と
;;;     renew・切り離した task の口・温める表)は、本番の宛先の部品(resent-request)と同じ期限と間で、sim の時計で送り直す。どちらの答えも柵の内側に
;;;     在るので世界の effect を出さず、要求の列を値で受けて scheduler と時計の effect だけで coordinator と話す。
;;;   - 柵(fence)= host_contract.SIM-PASSABLE(scheduler と doeff-time の時計の effect)だけを外へ通し、それ以外を本番の子と同じ
;;;     doeff.UnhandledEffect で Program へ投げ返す — sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返さない。
;;;   - 止めの合図(process-signals — process ごと・柵のすぐ外)= 本番の子 process の SIGTERM の代役(#3145)。止めの問い StopRequested・
;;;     止めの待ち AwaitStop に、本番の os-signal-stop-handler(doeff-core-effects)と同じ意味で答える: 問いを 1 度でも受けた process
;;;     (本番なら信号の受け手を据えた process)には、worker の SignalJob TERM が止めの理由("signal 15")を立て、待ちを起こす — process は
;;;     止めの節を回して自分で終わる。問いを受けていない process(受け手の無い process — 本番では既定の動きで終わる)と、KILL(猶予切れ)は、
;;;     今までどおり取り消す(exit -15)。外の世界(SimOutside の process ごとの組)が止めの問いを通すなら、合図が立つまでは外の答え手に
;;;     渡し(問いは外の答え・待ちは外の答えと TERM の先に来た方)、外の筋書きの止めもそのまま効く。
;;;   - 筋書き(scenario)は検の側の呼び手(本番の detached-cluster などを積む機体の外の process)として、同じ coordinator-answers(送り手 sim-client)
;;;     の下で走る。別の送り手(実行環境の宣言・版の違う呼び手)が要る筋書きは ClientLink の値を置き換えて coordinator-answers を自分で被せる。
;;;
;;; 検の effect(sim の世界が答える — scenario の中で出す。service の Program が出すと柵で落ちる)。Crash・Redeclare・ReadinessOf・
;;; KillWorker・StopWorker・StopCoordinator・CrashCoordinator は契約の effect(shared/intent/cluster_control.hy — どの cluster の handler も
;;; 答える物・ADR-DOE-CLUSTER-001 R8 の追補 (1))で、ここでは sim の世界が答える。残りは sim だけの観測と操作:
;;;   Crash 名                  動いている process を exit 1 で落とす(答え = 落とした数)。worker が本物の判断で起こし直す。
;;;   Redeclare 系              宣言し直す(本番の declare と同じ順で Program を置いてから Service の行を書く — update に従い recreate / handoff)。
;;;   DeclareRollout 名 spec     POST /resources/Rollout で作る(本番と同じ検証・所有者・重複検査)。
;;;   KubeCalls                 偽の k8s が受けた書きの履歴の写し(tuple)。
;;;   KubeReads                 偽の k8s の見張りが coordinator へ伝えた Deployment の「ns/名」の列(伝えた順の tuple — 時間で読みに行く数の検・#3868)。
;;;   SettleDeployment ns 名     Deployment の Pod を宣言の台数へ進める(ready で準備済み台数を指定できる)。見張りが変化を伝えるので
;;;                             待っている coordinator を起こす。
;;;   NodeReads                 偽の k8s が coordinator へ伝えた node の名の列(伝えた順の tuple — 時間で node の label を読みに行く数の検・#4070)。
;;;   RelabelNode 名 labels      node の label を替える(本番の Node の変化の出来事に当たる — 待っている coordinator を起こす)。
;;;   ReportsOf 名             coordinator に届いた ReportReady / ReportMetrics の列(SimReport)。
;;;   ReadinessOf 名            coordinator の Service の status の ready(ServiceReadiness — Ready / NotReady / Unknown / Missing)。
;;;   ProcessesOf 名            その job の process の列(SimProcess — 世代・worker・始まり・終わり・exit-code)。task は task/<id>。
;;;   AwaitProcessStarted 名    その job の最初の process が起きるまで待ち、その記録を返す(世界が process を記録した時に起きる)。
;;;   AwaitProcessEnded 名      契約の effect(process_model.hy — 本番は detached-cluster が coordinator を読んで答える)。sim では世界が
;;;                             答え、job の今の最後の process の終わりを世界が書いた時に待ち手の Promise を満たす(読み直さない)。
;;;   AwaitReadiness 名 状態 秒  契約の effect(cluster_control.hy)。ReadinessOf と同じ読みを、模擬の coordinator が書き終えた時(Persist)
;;;                             にだけ 1 回し直し、状態になるか秒を過ぎたら答える(時計の刻みでは読み直さない・#3053)。
;;;   AwaitJobProcess job 除く 秒 契約の effect。job の process のうち pid が除く物の外の最初の物を、世界が process を記録した時に答える。
;;;   SharedRows 頭            coordinator の盤の行(鍵が頭で始まる物)。
;;;   ReadCoordinator path      coordinator の口の GET の本文(/state・/workers/<名>・/metrics など)。
;;;   StopCoordinator 秒        coordinator の Pod を優雅に止め(次の拍の止めの合図)、秒の間止めてから作り直す(置き場から読み直す)。
;;;   CrashCoordinator 秒       次の Persist を失敗させる(返事をせずに落ちる — 取った要求の送り手には接続の失敗)。秒の後に作り直す。
;;;   FailRoute 型 path 状態 秒  coordinator の口 型 path(完全一致)への要求に、秒の間 状態(5xx など)で答える(本番の coordinator の前の
;;;                             ingress や作り直しの最中の答え — 要求は調停ループに届かず、状態を変えない)。
;;;   CoordinatorRuns           coordinator の Pod の一生の列(SimCoordinatorRun — 始まり・終わり・止まり方)。
;;;   ReplaceCoordinatorEnviron 環境  coordinator の Pod の環境変数(EnvEntry の tuple)を差し替える。次の一生から効く(本番の Deployment の
;;;                             env を変えて Pod を作り直す形 — 今の一生は起動の時に読んだ値のまま)。一生の始めに入口と同じ読み
;;;                             (coordinator.entry.main の with-running-commit)で走っている doeff の版を読む(#3772)。初めは空。
;;;   KillWorker 名             worker が node ごと死ぬ: 子 process は全部 exit -9 で止まり(中で Spawn した task も)、heartbeat が止まる。
;;;                             答え = 止めた process の数。
;;;   StopWorker 名             worker を優雅に止める(本番の SIGTERM — 全 job を止めの手順で回収して抜ける)。抜けるまで待つ。
;;;   StartWorker 名            死んだ・止めた worker を新しい世代(boot)で起こす(答え = 起こしたか — 動いている worker には偽)。
;;;   CutWorker 名 秒           worker の網を秒の間切る: その worker と子 process の要求は coordinator に届かない(接続の失敗)。子は
;;;                             動き続け、fence を越えると本物の worker_policy の判断で lease を持たない job を止める。
;;;   StallWorker 名 秒         worker の処理を秒の間止める(heartbeat を送らない・送りの失敗が無いので fence も効かない・子は動き続ける —
;;;                             本番の worker の処理が I/O で止まった形・#2804)。
;;;   DrainWorker 名 [ttl]      本番の preStop と同じ要求(drain_client.drain-request — 今の世代の boot を載せる)で drain を頼む。
;;;                             答え = 本番の CoordinatorCall と同じ形 {status body} / {error}。
;;;   PreparationsOf 名         worker が起こした準備の列(SimPreparation — コードの版か env-<キー>・先読みか・始まり・終わり・失敗)。
;;;   WatchFailuresOf 名        worker の名指しの待ちの task が思わぬ例外で止まった記録の列(SimWatchFailure — 本番の「待ちの thread が
;;;                             止まった」の 1 行に当たる。止まった世代は拍ごとの heartbeat に戻る — #1933)。
;;;   ClientLink                筋書きの送り手の口(SimLink — 置き換えて coordinator-answers を被せれば別の送り手になる)。
;;; 仮想の時計の時間を進めるのは scenario の Delay(doeff-time — 壁の時計では実時間で待つ)。scenario が終われば worker を止め(全 job を
;;; 止めの手順で回収)、coordinator を止める。
;;;
;;; 本番との既知の差(検めない):
;;;   - 1 process なので、import した module の大域の状態は job の間で共有されうる(改訂 1 の Q)。effect 以外の共有は機械で全部は断れない。
;;;   - environ は宿が宣言の :environ の名の Ask にだけ答える(本番の土台の os-environ-reader は子の os.environ を読むので、PATH などの宣言の
;;;     外の名にも答える)。宣言の名の値は、どちらも同じ答え方(host_contract の os-environ-reader と environ-table-reader)で字面どおり返る。
;;;   - 土台の関数の :needs が中の handler の :needs を漏らしていても見つからない(計画 9 の P — doeff-linter の照合は別便)。
;;;   - sim の土台は scheduler と時計を含まないので、本番の土台に scheduler を入れ忘れてもここでは見つからない(計画 7)。
;;;   - coordinator に届かない・断られた時の例外の型は RemoteJobFailed(本番は httpx の例外)。書きの要求は 1 回だけ送る(本番の
;;;     宛先の部品の routed-request は接続の段の失敗だけを間を置いて 4 回まで送り直す)。
;;;   - 実行環境の root は準備の中身(git・uv・disk)を模擬しない(env-prepare-seconds の後に揃うか env-failure で終わる)。disk は常に ok。
;;;   - process の中で Spawn した task は 1 段の包み(tracked-child)の task として起きる(Program が受ける把手は包みの物 — 取り消し・待ち・
;;;     答えは同じ)。
;;;   - AwaitProcessEnded は筋書きにだけ答える(世界の真実で答える — 本番の答えは coordinator の信念で、heartbeat の分だけ遅れる)。
;;;     job の Program が出すと柵で落ちる。
;;;   - AwaitDetached は読み直さず、模擬の coordinator がその task の終わりの phase を書いた時(Persist)に起きる(本番の detached-cluster は
;;;     poll-seconds ごとに読む — 答えの意味は同じ)。
;;;   - 時間の設定の既定(#3865 — 本番には無い・模擬の境界だけ): :timing を渡さない筋書きは、本番の既定の窓を全部 SIM-TIMING-RATIO 倍に
;;;     延ばした設定で走る(heartbeat と待ちの送り直しの歩を減らす)。その世界では worker の死の判断が起きない前提で、出たら筋書きを待たずに
;;;     SimLivenessError で終わる。生死・fence・停止と、heartbeat に載る報告の速さを試す筋書きは本番の値を :timing で明示する。
;;;   - 行き止まりの見張り(#3078 — 本番には無い・模擬の境界だけ): 業務の task が全部 出来事(WaitForEvent・WaitForEvents)を待って止まり、
;;;     筋書きの本体も期限なしで待ち、coordinator の task の行が落ち着いていれば、仮想の時計を進め続けずに SimDeadlockError で終わる。数える
;;;     のは WaitForEvent・WaitForEvents の待ちだけ(Delay・記録の Watch の待ちは数えない)。process の中で Spawn した子は、終わっても process の終わりまで
;;;     task の数に入る(その間は行き止まりと判じない — 見落としの向き)。
;;;
;;; 状態の置き場(ADR-DOE-HY-007): 世界の状態は世界の handler(sim-world)の session var に置き、値は defrecord、変化は effect で書く。
;;; 要求の列・memory の置き場・停止の合図・偽の k8s は coordinator_handler_sets の既存の資源(世界の session val が 1 回だけ作る)。
;;; 世界の節は session の書きを scheduler の切り替わる effect(Spawn・Wait・CompletePromise)より前に済ませる(切り替わりの間に他の task
;;; が書いた値を、節の古い写しで上書きしない)。
(require doeff-hy.macros [defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import copy [deepcopy])
(import signal)
(import dataclasses [dataclass replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import doeff [with-handlers EffectBase UnhandledEffect DoExpr Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [state :as session-store await-handler slog-handler slog-discard-handler])
(import doeff_core_effects.scheduler [scheduled CreatePromise CompletePromise Wait Spawn Gather Cancel Discard Promise Task
                                      Future TaskCancelledError Race])
(import doeff_core_effects.stop_signal_effects [AwaitStop StopRequested])
(import doeff_events [ArmedTimer ArmedTimers ArmedTimersEffect WaitForEventEffect WaitForEventsEffect])
(import doeff_time [Delay GetMonotonic epoch-ms-of sim-time-handler async-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster.shared.core.timing_rules [scaled-timing])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request Reply CoordinatorStopRequested PlainText])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming ENDED-PHASES]
        doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.shared.intent.process_model [AwaitProcessEnded ProcessEnded ProcessWaitExpired])
(import doeff_cluster.coordinator.core.cluster_policy [fresh-task-prefix])
(import doeff_cluster.coordinator.core.program [run-coordinator])
(import doeff_cluster.coordinator.entry.main [load-state with-running-commit])
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_core_effects.scripted_process [ProcessScript scripted-process-handler])
(import doeff_cluster.foundation.coordinator_http [RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.foundation.coordinator_inbox [StopState] doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.entry.handler_sets [MemoryWalStore emulated-handlers])
(import doeff_events [MemoryBroker EventBus WaitForEvent subscribed-event-handler memory-notice-handler notice-events-handler])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerGone])
(import doeff_cluster.coordinator.protocol.worker_notices [WORKER-NOTICE-READS])
(import doeff_cluster.coordinator.protocol.store [Persist])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue enqueue-request nudge-takers await-answer])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory])
;; 宣言の本文を組む body-of は別名で受ける — coordinator の要求の本文の解き(request_bodies.body-of)と取り違えないため。
(import doeff_cluster.shared.protocol.declaration_requests [ServiceRead CONFLICT-REREADS service-read reread-after-conflict needed-programs
                                                             body-of :as service-body-of])
(import doeff_cluster.shared.protocol.detached [detached-path detached-submit-body detached-refusal submit-unreachable awaited-answer runner-facts-of-view
                   runners-unreachable warm-request-body warm-path absent-warm-state SERVER-ERROR warm-unconnected
                   warm-server-failure runners-change-of watch-query service-ready-of service-facts-of-view services-unreachable])
(import doeff_cluster.shared.core.capabilities [env-mapping])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached CancelDetached ReleaseDetached ReadRunners DetachedSubmitted
                         DetachedSubmitAnswer DetachedAwaited RunnersUnreachable WARMING-PHASE AwaitRunnersChange RunnersChangeAnswer
                         AwaitServiceReady ServiceReady RunnersChange RunnersWatchMissing ReadServices ServicesUnreachable])
(import doeff_cluster.worker.core.drain_client [DRAIN-DEADLINE-SECONDS DRAIN-TTL-MARGIN-SECONDS])
(import doeff_cluster.worker.protocol.drain_requests [drain-request])
(import doeff_cluster.worker.protocol.declared [DeclaredReply declared-reply-of-json declared-job-specs task-specs] doeff_cluster.worker.protocol.heartbeat [heartbeat-body status-report env-report env-heartbeat-part] doeff_cluster.worker.core.heartbeat_rules [desired-when-unreachable warm-env-of-row])
(import doeff_cluster.worker.core.beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-reading reply-revision
                      WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import doeff_cluster.worker.protocol.coordinator_link [watch-params with-bell RESEND-AFTER-MS])
(import doeff_cluster.worker.core.heartbeat_rules [keep-marks-held desired-after-silence])
;; 周の間の待ち(AwaitNextTick)は本番の答え手 tick-pauses が宿の内側で答える。宿は起きる物の組(WorkerWakes)に、本番の送り手の口と
;; 同じ関数で組んだ期限と、宿の呼び鈴を入れて答える(#3871 の単位 5)。
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.core.worker_due [beat-due fence-due due-after])
;; 期限の早い方は別名で受ける — 世界の次の刻の earliest-due(この file の下)と取り違えないため。
(import doeff_cluster.shared.core.due_policy [earliest-due :as earliest-wake])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT SIM-PASSABLE environ-table-reader])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [worker-context-environ process-context-environ context-of-environ runtime-env-of-context])
(import doeff_cluster.worker.entry.job_entry [decoded-program])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.protocol.remote [task-submit-body outcome-of settled-value])
(import doeff_cluster.shared.intent.remote_model [RemoteJob RemoteJobFailed TaskSucceeded TaskFailed])
(import doeff_cluster.shared.protocol.program_codec [encode-program encode-outcome])
(import doeff_cluster.shared.core.remote_rules [failed-from program-sha])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.shared.protocol.task_result [task-result-request task-id-of-job])
(import doeff_cluster.shared.protocol.service_report [report-request])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvFailure])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp LeaseAnswer AwaitLeaseFree SEMAPHORE-PREFIX])
(import doeff_hy.wire [parse :as parse-wire])
(import doeff_cluster.shared.core.lease_rules [drop-holders lease-holder holder-tokens-prefix])
(import doeff_cluster.shared.entry.service_build [system-declaration])
(import doeff_cluster.shared.intent.service_model [System Declaration])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness Redeclare ReadinessOf Crash KillWorker StopWorker
                                                     StopCoordinator CrashCoordinator AwaitReadiness ServiceFailed
                                                     ReadinessWaitExpired AwaitJobProcess JobProcessSeen JobProcessWaitExpired])
;; 準備の状態の読みと待ちの判断は本番の境界の handler と同じ 1 つ(写しを 2 か所に持たない — #3668 の (a))。
(import doeff_cluster.shared.protocol.coordinator_reads [readiness-of-body readiness-wait-answer])
(import doeff_cluster.shared.protocol.board_requests [board-read-request board-write-request lease-request lease-wait-request lease-wait-answer])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared ANY])
(import doeff_cluster.shared.intent.warm_model [WarmRuntimeEnv ReadWarmState WarmState WarmAnswer AwaitWarm WarmReady WarmFailed WarmWaitExpired])
(import doeff_cluster.shared.core.warm_rules [warm-state-of-json warm-wait-answer])
;; worker の世代は入口の組み立て(doeff_cluster.worker.entry.main の worker-on)を偽の宿の組の上で回す(本番の main と同じ口)。
(import doeff_cluster.worker.entry.main [worker-on])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView CodeView CodeState ProcessView ProbeView ProbeState
                       DesiredJobs DesiredUnreadable ReadDesired ObserveWorld PublishStatus
                       PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ProbeEntry ForgetProbes
                       ReleaseLeases EnvReport AwaitNextTick WakeSet WorkerWakes StopStage StopProgress WarmChildMark WarmChildView StartWarmChild StopWarmChild
                       ForgetWarmChild NoticeJob Retired HandoffAbandoned] doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.worker.intent.retirement_model [AwaitRetirement])

;; load-state は置き場がまだ無い時だけ以前の形の file を探す。sim は置き場(MemoryWalStore)が在る時だけ load-state を呼ぶので読まれない。
(val NO-STATE-FILE "/nonexistent/doeff-sim/coordinator/state.json")
(val SIM-URL "sim://coordinator")            ; 宿の契約の run-context の coordinator の URL(sim の宿は URL で話さない — 表示だけ)
(val SIM-START-MS 1767225600000)             ; 仮想の時計の起点(2026-01-01T00:00:00Z)
(val DECLARE-ACTOR "sim-declare")            ; 宣言と検の読みの送り手(資源の書きの X-Actor)
(val CLIENT-NAME "sim-client")               ; 筋書き(検の側の呼び手)の送り手の名と居る所
(val TASK-POLL-SECONDS 1.0)                  ; RemoteJob の問い合わせの間隔(本番の remote-cluster の既定と同じ)
(val TASK-LEASE-SECONDS 15.0)                ; RemoteJob の lease(同じ)
(val DETACHED-POLL-SECONDS 1.0)              ; 切り離した task の待ちの問い合わせの間隔(本番の detached-cluster の既定と同じ)
(val QUEUE-WAIT-SECONDS 0.01)                ; coordinator が受け付けを始める・worker が最初に名乗るのを待つ間隔
(val WORKER-WAIT-SECONDS 5.0)                ; 筋書きの前に worker が最初に名乗るのを待つ上限(名乗れない worker が在っても筋書きは始める)
(val DRAIN-TTL-SECONDS (float (+ DRAIN-DEADLINE-SECONDS DRAIN-TTL-MARGIN-SECONDS)))  ; drain の期限(本番の preStop の頼みと同じ)
(val REPORT-KINDS #("readiness" "metrics"))
(val TERM-REASON (+ "signal " (str (int signal.SIGTERM))))  ; 止めの合図の理由(本番の os-signal-stop-handler の StopBox と同じ綴り)
(val KILLED-CODE -9)                         ; node ごと死んだ worker の子 process の exit-code
(val PAUSE-STOP "stop")                      ; coordinator の止まりの種類: 優雅な停止
(val PAUSE-CRASH "crash")                    ;                         Persist の失敗(返事をせずに落ちる)
(val CUT-REASON "網が切れている(sim — 送り手の居る worker の網)")
(val FAULT-REASON "sim: 注入した故障(FailRoute — coordinator の口がこの状態で答える)")
;; 模擬の世界の時間の設定の既定の比: :timing を渡さない筋書きは、本番の既定の窓を全部この比で延ばした設定(scaled-timing)で走る。
;; heartbeat と待ちの送り直しは coordinator の本物の歩なので(#3865)、仮想の時間を長く回す筋書きの歩の数がこの比でほぼ反比例に減る。
;; 生死・fence・停止を試す筋書きは本番の値(ClusterTiming の既定)を :timing で明示する(Mac の調整役の決定 2026-10-07 05:1x の道 ホ)。
;; 200 → 350(#3871 の単位 5・cisco-c8 の決定 2026-10-07): worker の代役が本番と同じく heartbeat ごとに周を 1 回まわすようになり
;; (前は静かな周を眠り heartbeat を受付の列に預けた)、仮想の 1 日を回す日次の検証の歩が 158,366 → 317,374 に増えた。周 1 回を
;; 軽くした後(呼び鈴を task なしで競わせる・代役の読み直しを 1 つ省く — 271,329)も上限 170,000 を越えたので、heartbeat の周の数を
;; 減らす比を足した。測った比: 300 = 187,158(越える)・350 = 163,384・400 = 145,061。上限に入る最小の 350 を採る。
(val SIM-TIMING-RATIO 350)


;; --- 公開の値 --------------------------------------------------------------------------------------------

(defrecord SimWorker
  "sim の worker 1 台(本番の worker の --provides・--exclusive・--capacity・--task-reserve・node に当たる)。provides = 提供する能力の名・
   exclusive = 専用の能力(この能力を needs に持つ job だけを受ける)・task-reserve = capacity のうち task のために空けておく数(必ず渡す —
   本番の worker の --task-reserve と同じく既定の値は無い)・node = 置かれた k8s の node の名(空 = k8s の外)・
   versions = 名乗る版(None = 送り手と同じ筋の versions — 違えば版の合わない task は置かれない)・prepare-seconds = コードの木と
   実行環境の root(env-prepare-seconds が None の時)の準備にかかる仮想の秒・env-prepare-seconds = 実行環境の root の準備(PrepareEnv)に
   かかる仮想の秒(None = prepare-seconds と同じ — 本番の code-host と env-host のように 2 つの準備は別の操作で、別の時間がかかる。
   実行環境の宣言を持たない job の準備は env-prepare-seconds を待たない・#2879)・env-failure = 実行環境の root の準備がこの失敗で終わる
   worker(None = 揃う)・
   starts-down = 止まったまま始まる(StartWorker で起きる — 後から加わる node)・ignores-fence = 反例の世界だけの壊れた worker
   (coordinator に届かない間 fence を越えても job を止めない — 本番の worker_policy の判断を使わない)・beat-every-ms = 反例の世界だけの
   壊れた worker(heartbeat の間隔を本番の beat_policy.beat-interval-ms でなくこの値にする — None = 本番の判断)・retire-stops = 反例の
   世界だけの壊れた worker(入れ替えで旧を名から外す RetireJob の handler が、外すと同時に旧を止める — 条 W1 の反例)・ignores-keep-marks =
   途絶しても動かし続けてよい印(#2804)を知らない古い版の worker の代役(heartbeat に keptWhenCutOff を載せず、返事の keepWhenCutOff を
   読み捨てる — 新しい coordinator と古い worker の組を確かめるため)・silent-stop = 反例の世界だけの壊れた worker(宣言の読みの handler が
   止まり始めを heartbeat で名乗らない — 条 C3 の反例・#2819)・overstates-capacity = 反例の世界だけの壊れた worker(heartbeat で capacity の
   代わりにこの数を名乗る — None = capacity。本当に置ける数は capacity のまま — 条 C6 の反例・#1976)・claims-provides = 反例の世界だけの
   壊れた worker(heartbeat で provides の代わりにこの能力を名乗る — None = provides。本当に提供する能力は provides のまま — 条 C7 の反例・#1976)・
   claims-exclusive = 反例の世界だけの壊れた worker(heartbeat で exclusive の代わりにこの専用の能力を名乗る — None = exclusive。本当の
   専用の能力は exclusive のまま — 条 C10 の反例・#1976)・fresh-boot-every-beat = 反例の世界だけの壊れた worker(heartbeat ごとに新しい
   世代を名乗る — 本当の process の世代は変わらない。coordinator は別の世代の heartbeat で drain を解くので、drain が効かない — 条 C11 の
   反例・#1976)・hides-retired = 反例の世界だけの壊れた worker(観測の handler が入れ替えで名から外した旧の process を載せない —
   worker が旧を止める前に次の新を並べ、並ぶ数が増える — 条 C14 の反例・#1976)・claims-task-reserve = 反例の世界だけの壊れた worker
   (heartbeat で task-reserve の代わりにこの数を名乗る — None = task-reserve。本当に task のために空けておく数は task-reserve のまま —
   条 C17 の反例・#3489)・silent-notices = 反例の世界だけの壊れた worker(入れ替えで旧を名から外す RetireJob と取り消しの NoticeJob が、
   観測の notice を書くだけで process へ退きの知らせを送らない — 条 W2 の反例・#3672)・doeff-commit = この値で起きた worker が動いている doeff の版(本番の WORKER_DOEFF_COMMIT に当たる名札 —
   置き先の判断と版の突き合わせ versions には使わない。空 = 版を名乗らない筋書き・#3366)。"
  (#^ str name)
  (#^ frozenset provides)
  (#^ int task-reserve)
  (setv #^ str doeff-commit "")
  (setv #^ frozenset exclusive (frozenset))
  (setv #^ int capacity 10)
  (setv #^ str node "")
  (setv #^ (| dict None) versions None)
  (setv #^ float prepare-seconds 0.0)
  (setv #^ (| float None) env-prepare-seconds None)
  (setv #^ (| EnvFailure None) env-failure None)
  (setv #^ bool starts-down False)
  (setv #^ bool ignores-fence False)
  (setv #^ (| int None) beat-every-ms None)
  (setv #^ bool retire-stops False)
  (setv #^ bool ignores-keep-marks False)
  (setv #^ bool silent-stop False)
  (setv #^ (| int None) overstates-capacity None)
  (setv #^ (| frozenset None) claims-provides None)
  (setv #^ (| frozenset None) claims-exclusive None)
  (setv #^ bool fresh-boot-every-beat False)
  (setv #^ bool hides-retired False)
  (setv #^ (| int None) claims-task-reserve None)
  (setv #^ bool silent-notices False))


(defrecord SimProcess
  "sim の宿が起こした process 1 つ(ProcessesOf の答えの要素)。job = 起こした時の job の名(task は task/<id>)・instance = 世代の名・
   attempt = worker の試行の番号・pid = sim の中の番号・spec-hash = 起こした spec の指紋・ended-ms / exit-code = 終わった時だけ
   (exit-code: 0 = 値で終わった / task は結果を書いて終わった・1 = 例外か Crash・3 = Program を解けない・-15 = 止めの合図・
   -9 = worker が node ごと死んだ)・detail = 終わった理由の 1 行(例外の型と文 — 本番の子の log の最後の行に当たる。値で終わった時は空)・
   value = service の Program が値で終わった時のその値(本番では捨てる — 検が有限の周回の答えを読むための sim だけの観測。task と値で終わらなかった
   process は None)・root-task = process の根の task の id(StartJob の Spawn が返した把手の task id — 世界が把手を覚えた時に書く。
   把手を覚える前と、覚える前に殺された process は None。呼び手は task ごとの積算の表〔OpenTaskTally・#4188〕を、この id から親の結びで
   引いて job ごとの task の木を足す・#4194。sim だけの観測)。"
  (#^ str job)
  (#^ str worker)
  (#^ str instance)
  (#^ int attempt)
  (#^ int pid)
  (#^ str spec-hash)
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ (| int None) exit-code None)
  (setv #^ str detail "")
  (setv #^ object value None)
  (setv #^ (| int None) root-task None))


(defrecord SimReport
  "coordinator に届いた service の報告 1 つ(ReportsOf の答えの要素)。kind = readiness | metrics・instance = 送り手の process の世代・
   at = 届いた時刻(epoch ms)・ready / reason / role = readiness の欄(metrics では None / 空)・metrics = 計器(readiness では None)。"
  (#^ str job)
  (#^ str kind)
  (#^ str instance)
  (#^ int at)
  (#^ (| bool None) ready)
  (#^ str reason)
  (#^ str role)
  (#^ (| dict None) metrics))


(defrecord SimPreparation
  "worker の宿が起こした準備 1 つ(PreparationsOf の答えの要素)。key = コードの版か env-<キー>・env = 実行環境の root か・warm = 先読みか・
   started-ms / ready-ms = 始まりと揃う(か失敗で終わる)時刻(epoch ms)・failure = 実行環境の準備の失敗(揃うなら None)。"
  (#^ str worker)
  (#^ str key)
  (#^ bool env)
  (#^ bool warm)
  (#^ int started-ms)
  (#^ int ready-ms)
  (#^ (| EnvFailure None) failure))


(defrecord SimWatchFailure
  "worker の名指しの待ちの task が思わぬ例外で止まった記録 1 つ(WatchFailuresOf の答えの要素 — 本番の「待ちの thread が止まった」の
   1 行に当たる)。boot = 止まった世代・at = 止まった時刻(epoch ms)・reason = 例外の型と文。"
  (#^ str worker)
  (#^ str boot)
  (#^ int at)
  (#^ str reason))


(defrecord SimCoordinatorRun
  "coordinator の Pod の一生 1 つ(CoordinatorRuns の答えの要素)。outcome = 止まり方(stopped = 止めの合図・それ以外は落ちた理由の
   1 行)— まだ動いていれば ended-ms は None で outcome は空。"
  (#^ int started-ms)
  (setv #^ (| int None) ended-ms None)
  (setv #^ str outcome ""))


(defrecord RouteFaults
  "調停ループの 1 歩が取った要求を篩うための、網と口の故障の今(AdmitBatch の答え Admission の欄 — 世界へ 1 度だけ聞く・#2668)。cut = 今網の
   切れている worker の名・failing = 今故障を入れている coordinator の口 #(method path) → 答える status(欄に写像を持つ理由: 筋書きの
   FailRoute が口ごとに置く表をそのまま運ぶ — 引く側は口で引くだけ)・now-ms = 世界が答えた刻(epoch ms)。"
  (#^ frozenset cut)
  (#^ dict failing)
  (#^ int now-ms))


(defrecord Admission
  "調停ループの 1 歩が取った要求を世界が篩った答え(AdmitBatch の答え — 篩いと報告の記録と覚えを世界への問い 1 つで・#3054 の C-6)。
   faults = 篩った時の網と口の故障の今・kept = 調停ループへ渡す要求(網の切れた worker からの物と、故障を入れた口への物を除いた物)。"
  (#^ RouteFaults faults)
  (#^ tuple kept))


(defrecord CoordinatorStep
  "置き場へ書いた coordinator の歩 1 つの記録(期限だけで起きる走りと余計に起こす走りを、耐久の状態の変わり目とその刻で比べる基準 —
   #2670 の根 B・#3865): at = その歩の刻・writes = その歩の Persist の writes の列(書きの順 — 置き場の鍵の差分)。"
  (#^ int at)
  (#^ tuple writes))


(defrecord SimPauses
  "筋書きが頼んだ coordinator の止まり(世界の session の値 1 つ — PauseDue が 1 度の読みで判じる・#2668)。queued = 頼まれた止まりの
   #(kind 秒) の列・downtime = 最後に効いた止まりの止まっている秒(DowntimeOf が取り出す — None = 作り直さない)・restart-ms = 止まった
   coordinator を作り直す刻(epoch ms — DowntimeOf が取り出した時に書き、CoordinatorStarted が消す・None = 作り直しを待っていない。
   次の予定の刻の問い NextWorldDue が読む・#3094)。"
  (#^ tuple queued)
  (setv #^ (| float None) downtime None)
  (setv #^ (| int None) restart-ms None))


(defrecord SimIntake
  "coordinator の要求の受付まわりの世界の値(世界の session の値 1 つ — 調停ループの 1 歩の問い AdmitBatch が 1 度の読みで篩い・記録・覚えを
   済ませる・#3054 の C-6。節は本体で使う session の値を名ごとに節の頭で読むので、別々の名だと 1 歩で 5 度読む)。cuts = 網の切れている
   worker の名 → 切れが明ける刻(epoch ms)・failing = 故障を入れている口 #(method path) → #(答える status 明ける刻)(2 つの欄に写像を
   持つ理由: 筋書きの CutWorker・FailRoute が名ごと・口ごとに置く表をそのまま運ぶ — 引く側は名と口で引くだけ)・held = 取って返事を
   まだしていない要求(返事の前に落ちたら接続の失敗を返す相手)・reports = 届いた service の報告(SimReport)。"
  (#^ dict cuts)
  (#^ dict failing)
  (#^ tuple held)
  (#^ tuple reports))


(defrecord SimLink
  "coordinator へ話す送り手の口 1 つ(クラスタの約束の答え coordinator-answers の引数)。queue = coordinator の受け口(要求の列)・
   actor = 書きの送り手(X-Actor)・revision = 送り手の版(task の revision)・peer = 送り手の居る所(網の切断は worker の名で数える)・
   versions = 送り手の process の版の識別(Program を置く時に blob に添える — 本番の送り手が宿の契約の鍵 versions-key で読む値・
   sim では筋の versions。blob の JSON にそのまま載る値なので dict のまま持つ)・runtime-env = 送る task(RemoteJob と切り離した task)の実行環境の宣言(本番の TaskSender・DetachedSender の
   runtime-env — None = 送り手の版のコードだけ)・timing = 模擬の coordinator の時間の設定(本番の送り手が組み立ての時に ClusterTiming
   から作る返事の打ち切りと待ちの上限を、送り手の口でも同じ値で使う — sim-cluster の :timing・#3865)。"
  (#^ RequestQueue queue)
  (#^ str actor)
  (#^ str revision)
  (#^ str peer)
  (#^ dict versions)
  (#^ ClusterTiming timing)
  (setv #^ (| RuntimeEnv None) runtime-env None))


;; --- 検の effect(sim の世界が答える)----------------------------------------------------------------------

(defeffect DeclareRollout
  "検の effect: 本番の資源 API と同じ要求で Rollout を作る。答え = 資源の本文。拒否・接続失敗は RemoteJobFailed。"
  {:fields [(: name str) (: spec dict)]
   :answer dict
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KubeCalls
  "検の effect: KubeMemory.calls の写し(受けた順の dict の tuple)。dryRun の書きも含む。"
  {:answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KubeReads
  "検の effect: 偽の k8s の見張りが coordinator へ伝えた Deployment の「ns/名」の列(KubeMemory.reads — 伝えた順の tuple)。時間で k8s を
   読みに行く数を数える検が使う(#3868)。"
  {:answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect SettleDeployment
  "検の effect: 偽の Deployment の Pod を宣言の台数に揃える。ready = None は全台準備済み。worker は StartWorker で別に起こす。"
  {:fields [(: namespace str) (: name str) (: ready (| int None) None)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NodeReads
  "検の effect: 偽の k8s が coordinator へ伝えた node の名の列(KubeMemory.node-reads — 伝えた順の tuple)。時間で node の label を
   読みに行く数を数える検が使う(#4070)。"
  {:answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect RelabelNode
  "検の effect: 偽の k8s の node の label を labels(キー → 値)に替える(本番の Node の label の変化の出来事に当たる・#4070)。"
  {:fields [(: name str) (: labels dict)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReportsOf
  "検の effect: job name の process から coordinator に届いた ReportReady / ReportMetrics の列(SimReport の tuple・届いた順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessesOf
  "検の effect: job name の process の列(SimProcess の tuple・起こした順)。"
  {:fields [(: name str)]
   :answer (get tuple #(SimProcess ...))
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect AwaitProcessStarted
  "検の effect: job name の最初の process が起きるまで待ち、その記録(SimProcess)を返す(既に起きていればすぐ)。読み直さず、世界が
   process を記録した時(NoteProcess)に起きる。"
  {:fields [(: name str)]
   :answer SimProcess
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect SharedRows
  "検の effect: coordinator の盤の行のうち鍵が prefix で始まる物(鍵 → 値)。"
  {:fields [(: prefix str)]
   :answer dict
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReadCoordinator
  "検の effect: coordinator の口 path の GET の本文(/state・/workers/<名>・/metrics など — 届かない・断られたら RemoteJobFailed)。"
  {:fields [(: path str)]
   :answer (| dict PlainText)
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect FailRoute
  "検の effect: coordinator の口 method path(完全一致)への要求に、seconds 秒の間 status で答える(本番の coordinator の前の ingress・
   作り直しの最中の 5xx — 要求は調停ループに届かず、状態を変えない。本文は {\"error\" 理由})。網の切れた worker の要求は接続の失敗の
   まま(切断が先)。答え = None。"
  {:fields [(: method str) (: path str) (: status int) (: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReplaceCoordinatorEnviron
  "検の effect: coordinator の Pod の環境変数を environ(EnvEntry の tuple)に差し替える。次の一生から効く(本番の Deployment の env を
   変えて Pod を作り直す形 — 今の一生は起動の時に読んだ値のまま・#3772)。答え = None。"
  {:fields [(: environ (get tuple #(EnvEntry ...)))]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorEnvironOf
  "coordinator の Pod の一生の始めに、その Pod の環境変数(ReplaceCoordinatorEnviron で差し替えた値・初めは空)を読むため。"
  {:answer (get tuple #(EnvEntry ...))
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorRuns
  "検の effect: coordinator の Pod の一生の列(SimCoordinatorRun の tuple・起きた順)。"
  {:answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StartWorker
  "検の effect: 死んだ・止めた worker name を新しい世代(boot)で起こす。答え = 起こしたか(動いている worker には偽)。"
  {:fields [(: name str)]
   :answer bool
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReplaceWorker
  "検の effect: 止まっている worker name の値(SimWorker — 能力・枠・取っておく数・版)を worker に差し替える。次の StartWorker から新しい
   値で起きる(本番の Deployment の env を変えて Pod を作り直す Recreate の、作り直しの間に当たる — #3366)。答え = 差し替えたか(動いて
   いる worker・名の違う worker には偽 — 動いている間に値を変えない)。"
  {:fields [(: name str) (: worker SimWorker)]
   :answer bool
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WorkerOf
  "検の effect: worker name の今の値(SimWorker — ReplaceWorker で差し替えた後はその値)。"
  {:fields [(: name str)]
   :answer SimWorker
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CutWorker
  "検の effect: worker name の網を seconds 秒切る — その worker と子 process の要求は coordinator に届かない(接続の失敗)。子 process は
   動き続け、fence を越えると本物の worker_policy の判断で lease を持たない job を止める。答え = None。"
  {:fields [(: name str) (: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StallWorker
  "検の effect: worker name の処理を seconds 秒止める(#2804 — 2026-10-02 13:26 の形: worker の処理が I/O で止まり heartbeat が送られない)。
   その間 heartbeat を送らない(送りの失敗が無いので fence も効かない)・子 process は動き続ける・拍は前の宣言のまま回る。答え = None。"
  {:fields [(: name str) (: seconds float)]
   :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DrainWorker
  "検の effect: worker name の drain を、本番の preStop と同じ要求(drain_client.drain-request — 今の世代の boot を載せる)で頼む。
   ttl-seconds = drain の期限。答え = 本番の CoordinatorCall と同じ形({\"status\" int \"body\" dict} か {\"error\" 理由})。"
  {:fields [(: name str) (: ttl-seconds float DRAIN-TTL-SECONDS)]
   :answer dict
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PreparationsOf
  "検の effect: worker name が起こした準備の列(SimPreparation の tuple・起こした順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WatchFailuresOf
  "検の effect: worker name の名指しの待ちの task が思わぬ例外で止まった記録の列(SimWatchFailure の tuple・起きた順)。"
  {:fields [(: name str)]
   :answer tuple
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ClientLink
  "検の effect: 筋書きの送り手の口(SimLink — 送り手 sim-client)。値を置き換えて coordinator-answers を被せれば、実行環境の宣言や版の
   違う送り手として話せる。"
  {:answer SimLink
   :tags {:context "doeff-cluster" :role "intent"}})


;; --- sim の中の値 ---------------------------------------------------------------------------------------

(defrecord SimOutside
  "sim の外の世界(本番では job の土台の handler が外の系 — 業務の store・外部の API — と話して答える effect に、sim では系の外側で
   答える物)。handlers = sim の全部の job と筋書きの外側に置く handler の組(外側が先 — 時計の内側)・effects = それが答える effect の型
   (柵が外へ通す — isinstance で数えるので基底の型でよい)。job は effect を通してだけ外の世界を共有する(object を共有しない)。
   per-process = process ごとの外の世界を作る関数 (job の名 worker の名) → ProcessOutside(handler と、その process の柵だけが通す型 —
   None = 無し)。宿が process を起こす
   時に 1 回呼び、柵の外側・sim の世界の内側に並べる — 本番で job ごと・機体ごとに違う外の口(記録の service の身元の token・預かり所の
   借り手・機体の session の置き場)を、共有の外の世界(handlers)の手前で答えるため(#833 の条件「sim-cluster は担い手ごとに
   handler の組を持つ」・#834)。作る handler も effects に載った型にだけ答える(柵がそれ以外を通さない)。"
  ;; 欄の型は型の引数まで書く(素の list / tuple / Callable だと、欄を読む使い手の strict で中身が Unknown になり、型逃げ無しには
  ;; 消せない赤が出た — #4254)。per-process の答えの型 ProcessOutside は下で定める値(この class を作る時に名がまだ
  ;; 無い)なので文字列の注記。
  (#^ (get list (get Callable #(... object))) handlers)
  (#^ (get tuple #(type ...)) effects)
  (setv #^ "Callable[[str, str], ProcessOutside] | None" per-process None))


(defrecord SimPlan
  "sim の 1 回の走りの筋(sim-cluster が引数から作る)。declaration = 最初の宣言(environ の上書きを重ねた行)・environ = job 名 →
   上書きの環境変数(Redeclare にも重ねる)・passable = 柵が外へ通す effect の型(SIM-PASSABLE と外の世界の effects)・
   per-process = process ごとの外の handler の組を作る関数(SimOutside.per-process — None = 無し)・store = coordinator の置き場を作る
   関数(引数なし → MemoryWalStore の値 — 派生の class をそのまま渡せる。None = MemoryWalStore)。deployments = 偽の k8s の
   初期観測(「namespace/名」→ dict)。nodes = 偽の k8s の node の名 → label(k8s の Node の metadata.labels と同じ形 — 能力の導出の検・
   #4070)。どちらも parts-of が deepcopy を作り、1 回の走りの間だけ変更する。runtime-env = 宣言の実行環境の宣言
   (本番の declare の --runtime-env と同じ — Redeclare にも載せる。None = 送り手の版のコードだけ)。versions = sim の送り手・
   worker・子が名乗る版の識別(sim-plan が 1 度だけ綴る — env の root の外の process として・foundation/process_versions。宣言と blob の JSON にそのまま載る値なので dict)。"
  (#^ System system)
  (#^ Declaration declaration)
  (#^ tuple workers)
  (#^ dict environ)
  (#^ str revision)
  (#^ dict versions)
  (#^ int start-ms)
  (#^ ClusterTiming timing)
  (#^ ClusterNaming naming)
  (#^ WorkerPolicy policy)
  (#^ tuple passable)
  ;; 知らせの broker(coordinator が worker の生死の出来事を出す先 — 呼び手が作って sim-cluster の :notice-broker で渡す。呼び手の世界の
  ;; job と筋書きが同じ broker の受け手に成れる・#3850。sim は作らない)。
  (#^ MemoryBroker notice-broker)
  ;; SimOutside.per-process と同じ型(文字列の注記の訳も同じ)。
  (setv #^ "Callable[[str, str], ProcessOutside] | None" per-process None)
  (setv #^ (| Callable None) store None)
  (setv #^ (| dict None) deployments None)
  (setv #^ (| dict None) nodes None)
  (setv #^ (| RuntimeEnv None) runtime-env None)
  ;; 比で延ばした世界(sim-cluster に :timing を渡さない筋書き — SIM-TIMING-RATIO)か。真なら worker の死の判断(WorkerGone)を見張り、
  ;; 出たら筋書きを待たずに SimLivenessError で終わる(延ばした窓は生死の判断が起きない前提 — #3865)。
  (setv #^ bool watches-gone False))


(defrecord SimParts
  "coordinator の Pod の部品(1 回の走りに 1 組 — 世界の session val が 1 回だけ作る)。emulated-handlers が受ける要求の列・置き場・
   停止の合図・偽の k8s・知らせの broker(coordinator が worker の生死の出来事を出す先 — 筋の Program はこの broker を読んで受け手に
   成れる・#3864)。"
  (#^ RequestQueue queue)
  (#^ MemoryWalStore store)
  (#^ StopState stop)
  (#^ KubeMemory kube)
  (#^ MemoryBroker broker))


(defrecord SimExit
  "sim の子 process の終わり方(本番の job_entry の入口の終わり方と同じ): code = exit-code・result = task の詰めた結果(service は None)・
   detail = 終わった理由の 1 行(例外の型と文・値で終わった時は空)・value = service が値で終わった時のその値(SimProcess の value へ写す)。"
  (#^ int code)
  (#^ (| str None) result)
  (setv #^ str detail "")
  (setv #^ object value None))


(defrecord HostTruth
  "worker の宿 1 つの真実(世界の session に在る)。boot = この世代の名(StartWorker で新しくなる)・processes = 子 process の観測・
   codes = 準備(鍵 → SimPreparation)・probes = 入口の検め・statuses = 最後に出した状態の報告(heartbeat の本文)・last-ok-ms =
   coordinator が最後に返事をした時刻・fence-ms = 自己停止の閾値・last-desired / last-warm = 最後に読めた job と task・温める表の行・
   programs = 取った詰めた Program(sha → 文字列)・results = 終わった task の結果(id → 詰めた結果)・task-echo = 切り離した task の
   返事の行(id → 行)・beats = coordinator が返事をした heartbeat の数・down = 死んだか止まった・stopping = 優雅な停止を頼まれた。
   heartbeat の切り離し(#1933 — 本番の coordinator への口の WatchCell と同じ意味・beat_policy): fresh = 前の heartbeat が届いた・
   sent-statuses = 前に届けた状態の報告・beat-interval-ms = 送る間隔・watch-after = 次の名指しの待ちの版(返事に版が無ければ None)・
   watch-confirmed = 待ちが 1 度答えた・watch-unsupported = 待つ口が無い(404)・woken = 待ちが「変わった」と答えた印・beat-bells =
   heartbeat が届いた時に鳴らす呼び鈴(起こした後の待ちが次の版を待つ)・watch-failure = 待ちの task が思わぬ例外で止まった理由
   (在れば拍ごとの heartbeat に戻る — 本番の coordinator への口の watching? が thread の死に気づくのと同じ)・tick-bell = 拍の間の
   眠りを起こす呼び鈴(#2692 — 待ちが「変わった」と答えた時に鳴らして手放し、次の宣言の読みが新しく掛ける。鳴るまでは拍をまたいで
   同じ物を渡す)・stalled-until-ms = 処理の止まり(StallWorker — #2804)の終わりの時刻(epoch ms・0 = 止まっていない)。この刻までは
   heartbeat を送らず(送りの失敗も無いので fence も効かない — 本番の worker の処理が I/O で止まった形)、前の宣言のまま拍を回す・
   sent-stopping = 前に届けた heartbeat に載せた止まり始め(#2819 — 拍の Program が渡す止まりと違えば送る間隔を待たずに送る。
   本番の coordinator への口の LinkState.sent-stopping と同じ)・wake-bell = 周の間の待ちを起こす宿の呼び鈴(#3871 の単位 5 — 宿が
   起きる物の組に入れ、宿の真実の周の判断に効く欄を世界が書き換えると鳴らして手放す。子 process の終わりは本番では待つ子の終わりで
   起きる — 模擬ではこの鈴で代える)・stop-bell = 止めの合図の待ち(AwaitStop)の呼び鈴(止めの頼みで世界が鳴らして手放す)・
   ticks = 宣言を読んだ周の数(検が読む — 周が期限と出来事の分だけ回るかの物差し)。"
  (#^ str boot)
  (#^ int boot-at)
  (#^ tuple processes)
  (#^ dict codes)
  (#^ dict probes)
  (#^ list statuses)
  (#^ int last-ok-ms)
  (#^ int fence-ms)
  (#^ tuple last-desired)
  (#^ tuple last-warm)
  (#^ dict programs)
  (#^ dict results)
  (#^ dict task-echo)
  (setv #^ int beats 0)
  (setv #^ bool down False)
  (setv #^ bool stopping False)
  (setv #^ bool fresh False)
  (setv #^ (| list None) sent-statuses None)
  (setv #^ int beat-interval-ms (beat-interval-ms None {}))
  (setv #^ (| int None) watch-after None)
  (setv #^ bool watch-confirmed False)
  (setv #^ bool watch-unsupported False)
  (setv #^ bool woken False)
  (setv #^ tuple beat-bells #())
  (setv #^ (| str None) watch-failure None)
  (setv #^ (| Promise None) tick-bell None)
  (setv #^ int stalled-until-ms 0)
  ;; 途絶しても動かし続けてよい印の在る job を止めるまでの長い方の柵(#2804 — 本番の LinkState.keep-fence-ms と同じ・返事の timing が上書きする)。
  (setv #^ int keep-fence-ms (. (ClusterTiming) keep-fence-ms))
  (setv #^ bool sent-stopping False)
  (setv #^ (| Promise None) wake-bell None)
  (setv #^ (| Promise None) stop-bell None)
  (setv #^ int ticks 0)
  ;; root ごとの待ちの子の観測(#3646 — WarmChildView の列)。模擬の待ちの子は読み込みの秒を持たず、起こした刻に準備済み(印は分かれる前の
  ;; 形)で、分かれる task は今までどおり世界の task として走る(fork の代役は Spawn)。
  (setv #^ tuple warm-children #()))


(defrecord HostTruthChange
  "ChangeHostTruth の答え: before = 世界が読んだ宿の真実・after = 置き直した宿の真実(世代が終わっていて直さなかったなら None)。"
  (#^ HostTruth before)
  (#^ (| HostTruth None) after))


(defrecord SimStopBox
  "process 1 つの止めの合図の受け手(世界の session の stop-boxes の値 — 在る = 止めの問いか待ちを受けた process・#3145)。reason = 立った
   止めの理由(最初の 1 つ — None = まだ)・waiters = 止めを待つ Promise・bridged = 外の世界の止めの待ちを写す task を起こしたか。"
  (setv #^ (| str None) reason None)
  (setv #^ tuple waiters #())
  (setv #^ bool bridged False))


(defrecord SimStopWait
  "止めの待ち(ProcessStopWait)の答え。promise = 止めの理由で満たされる Promise・bridge = 外の世界の止めの待ちを写す task を起こす番か。"
  (#^ Promise promise)
  (#^ bool bridge))


(defrecord SimNoticeWaiter
  "退きの知らせの待ち 1 つ(#3672 — AwaitRetirement): after = 待ち手が前に受けた知らせ(None = まだ何も)・promise = 今の知らせが after と
   違う値になった時に、その知らせで満たされる Promise。"
  (#^ (| Retired HandoffAbandoned None) after)
  (#^ Promise promise))


(defrecord SimNoticeBox
  "process 1 つの退きの知らせの受け手(世界の session の notice-boxes の値 — #3672)。notice = worker が最後に送った知らせ(None = まだ)・
   waiters = 待ち(SimNoticeWaiter)の列。"
  (setv #^ (| Retired HandoffAbandoned None) notice None)
  (setv #^ tuple waiters #()))


(defrecord HostStop
  "worker の拍の止めの問い(核の StopRequested)に宿が答える材料(StopRequestOf の答え — 世代の確かめと全 worker の止まれを世界への
   問い 1 つで・#3054 の C-6)。truth = その worker の宿の真実・all-stopping = 全 worker が止まる時か。"
  (#^ HostTruth truth)
  (#^ bool all-stopping))


(defclass WorkerDied [Exception]
  "偽の宿の世代が終わった(node ごと死んだ・止めた後に次の世代が起きた)— その世代の run-worker をその場で終わらせる。")


;; --- 世界の仕組みの effect(答えるのは世界の handler sim-world だけ — Program からは柵で届かない)---------------------

(defeffect PlanOf
  "sim の筋(SimPlan)。"
  {:answer SimPlan :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PartsOf
  "coordinator の Pod の部品(SimParts)。"
  {:answer SimParts :tags {:context "doeff-cluster" :role "intent"}})

(defeffect HostTruthOf
  "worker name の宿の真実(HostTruth)。"
  {:fields [(: name str)] :answer HostTruth :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PutHostTruth
  "worker name の宿の真実を置き直す。"
  {:fields [(: name str) (: truth HostTruth)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ChangeHostTruth
  "worker name の世代 boot の宿の真実を読み、まだその世代なら change(宿の真実 → 宿の真実の純粋な関数か、宿の真実を組む defk — 呼んだ結果の
   Program は世界の節の中で走らせる・待たない Program に限る)で直して置き直す — 読んで直して書く組を
   世界への問い 1 つにする(読みと書きを別々に聞くと 1 組ごとに世界を 2 度通る・#2668)。live = 真なら止まった宿も「終わった世代」と
   数える(live-truth と同じ)・偽なら世代だけを見る。答え = HostTruthChange(読んだ真実と、置き直した真実 — 終わった世代なら None)。"
  {:fields [(: name str) (: boot str) (: live bool) (: change Callable)] :answer HostTruthChange :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NextPid
  "次の process の番号(sim の中で重ならない)。"
  {:answer int :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KeepHandle
  "process pid の task の把手を覚える(止めの合図と Crash が取り消す相手)。既に止めると決まった process なら、その場で取り消す。"
  {:fields [(: pid int) (: task Task)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect HandleOf
  "process pid の task の把手(終わっていれば None)。"
  {:fields [(: pid int)] :answer (| Task None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect KeepChild
  "process pid の中で Spawn した task を覚える(process の終わりで一緒に取り消す)。process がもう終わっていれば、その場で取り消す。"
  {:fields [(: pid int) (: task Task)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessStopAsked
  "process pid の止めの問い(StopRequested)— 合図の受け手を据え(本番の os-signal-stop-handler が最初の問いで信号の受け手を据えるのと
   同じ — 据えた process への TERM は取り消しでなく止めの合図になる)、立っている止めの理由を答える(無ければ None)。"
  {:fields [(: pid int)] :answer (| str None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessStopWait
  "process pid の止めの待ち(AwaitStop)— 合図の受け手を据え、止めの理由で満たされる Promise を答える(既に立っていれば満たした物)。
   bridge = 外の世界が止めの待ちを通すか。答え = SimStopWait(bridge が真なのはその process で初めての時だけ — 外の答えを写す task を
   1 つだけ起こす)。"
  {:fields [(: pid int) (: bridge bool)] :answer SimStopWait :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessStopRaised
  "process pid に止めの理由 reason を立てる(最初の理由だけを残す)— 待ちを起こす。答え = 合図が届いたか(受け手を据えていない process
   には届かない — 呼び手の SignalJob が取り消す)。"
  {:fields [(: pid int) (: reason str)] :answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessNoticeWait
  "process pid の退きの知らせの待ち(AwaitRetirement — #3672)。今の知らせが after と違えば(まだ何も来ていない時を除く)その知らせで満たした
   Promise を、同じなら次の知らせで満たされる Promise を答える。"
  {:fields [(: pid int) (: after (| Retired HandoffAbandoned None))] :answer Promise :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ProcessNoticeRaised
  "process pid へ退きの知らせ notice を立てる(今の知らせを置き換える — 本番の worker が shim の標準入力へ書く行の代役)。after が notice と
   違う待ちを起こす。"
  {:fields [(: pid int) (: notice (| Retired HandoffAbandoned))] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteProcess
  "起こした process を記録する。"
  {:fields [(: process SimProcess)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect EndProcess
  "process の終わり(SimExit — exit-code・task の結果・理由)を書き(worker の宿の観測の exit-code・task の結果・記録の終わり)、
   process の中で Spawn した task を取り消す。"
  {:fields [(: worker str) (: pid int) (: ended SimExit)] :answer None
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteCoordinatorWrite
  "coordinator が置き場への書き(Persist の writes)を終えたことを世界に知らせる — 書きで終わった切り離した task の待ち手と、準備の状態を
   待つ AwaitReadiness の待ち手を起こす(待ち手は起きた時に 1 回だけ読み直す — 時計の刻みでは読み直さない・#3053)。書き 1 回の後の
   問いは世界への 1 つ(部品を別に聞かない・#3054 の C-6)。"
  {:fields [(: writes tuple)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NotePreparation
  "worker の宿が起こした準備を記録する。"
  {:fields [(: preparation SimPreparation)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteWatchFailure
  "worker の世代の名指しの待ちの task が思わぬ例外で止まったことを記録し、その世代(今の世代なら)を拍ごとの heartbeat に戻す。"
  {:fields [(: failure SimWatchFailure)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WorkersStopping
  "全 worker が止まる時か(scenario が終わった後に真)。"
  {:answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopWorkers
  "全 worker に止まれと合図する(死んだ worker の起き直しの待ちも解く)。"
  {:answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WorkerEnded
  "worker name の世代 boot の run-worker が抜けた(その世代がまだ今の世代なら止まったと記録し、StopWorker の待ちを解く)。"
  {:fields [(: name str) (: boot str)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect RevivalOf
  "worker name が止まっていれば、StartWorker で完了する Promise(動いていれば None — すぐ次の世代を起こす)。"
  {:fields [(: name str)] :answer (| Promise None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect AdmitBatch
  "調停ループの 1 歩が取った要求 batch を、網の切れと口の故障の今で篩い、残した要求のうち service の報告を記録し、返事の前に落ちたら
   接続の失敗を返す相手として覚える — 1 歩の問いを世界への 1 つにする(篩い・報告・覚えを別々に聞くと 1 歩に 2〜3 度・#2668・
   #3054 の C-6)。答え = Admission。"
  {:fields [(: batch tuple)] :answer Admission :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorSteps
  "検の effect: 置き場へ書いた coordinator の歩ごとの記録(CoordinatorStep)の列(StepEnded が運んだ記録・刻の順)。止まる時の歩
   (止まる刻の生存の印)は次の歩の頭が無いので入らない。"
  {:answer tuple :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ReleaseRequest
  "返事を済ませた要求を覚えから外す。"
  {:fields [(: request Request)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect TakeHeldRequests
  "覚えている要求を全部取り出して空にする(答え = Request の tuple)。"
  {:answer tuple :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PauseDue
  "coordinator の止まりの kind(stop | crash)が頼まれているか。頼まれていれば筋書きから外し、止まっている秒を覚える。"
  {:fields [(: kind str)] :answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StepEnded
  "調停ループの 1 歩の終わり(次の歩の頭 — 止まりを判じる刻・返事を済ませた後)の世界への問い 1 つ: 返事を済ませた要求 released を覚えから
   外し(ReleaseRequest と同じ)、この歩の置き場への書き writes(Persist ごとの writes の列 — 書きの順)を終えたことを知らせ
   (NoteCoordinatorWrite と同じ待ち手を書きの順に起こす)、止まり(stop)が頼まれているかを PauseDue と同じく判じる。返事ごと・書きごと・
   歩の頭に別々に聞くと 1 歩に 3〜5 度(#2670 の根 A)。答え = 止まりが頼まれているか。"
  {:fields [(: released tuple) (: writes tuple)] :answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DowntimeOf
  "止まっている秒(None = 作り直さない)を取り出して空にする。"
  {:answer (| float None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect PersistCrashDue
  "次の Persist で落ちを注入するか — 筋書きの落ち(crash)が頼まれているかを PauseDue と同じく判じて筋書きから外す。observe-requests は
   注入が待っている時(列の crash-waiting)だけ、書きの前に問う(#3132 — 書きごとに問うと生存の印の書き 1 回 46 歩のうち 20 歩に
   なった)。"
  {:answer bool :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NextWorldDue
  "sim の世界の次の予定の刻(行き止まりの見張りが問う — 業務の task が全部出来事を待って止まっていても、世界の予定がまだ来るなら
   行き止まりにしない・#3078 の子 3 = #3094): 網の切れ・口の故障・worker の処理の止まりが明ける刻と、止まった coordinator を作り直す刻の
   うち now-ms より後の最も早い刻(epoch ms)。筋書きが頼んだ coordinator の止まり・落ちが残っていれば now-ms(次の歩・次の保存で起きる
   出来事で、刻を持たない)。何も無ければ None。"
  {:fields [(: now-ms int)] :answer (| int None) :tags {:context "doeff-cluster" :role "intent"}})

(defeffect StopRequestOf
  "worker name の拍の止めの問い(核の StopRequested)に答える材料 — その worker の宿の真実と、全 worker が止まる時かを世界への問い
   1 つで読む(#3054 の C-6)。答え = HostStop。"
  {:fields [(: name str)] :answer HostStop :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorStarted
  "coordinator の Pod が起きた(時刻 ms)。"
  {:fields [(: ms int)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect CoordinatorEnded
  "coordinator の Pod が止まった(時刻 ms・止まり方 outcome)。"
  {:fields [(: ms int) (: outcome str)] :answer None :tags {:context "doeff-cluster" :role "intent"}})


;; --- 行き止まりの見張りの値と effect(#3078)----------------------------------------------------------------

(defrecord BusinessWait
  "出来事を待っている業務の task 1 つ: process の pid・job の名・待つ出来事の型の名(WaitForEvent・WaitForEvents の型の名を , で繋いだ物)。"
  (#^ int pid)
  (#^ str job)
  (#^ str events))

(defrecord LiveProcess
  "生きている業務の process 1 つ: pid・job の名・task の数(主の task 1 + 中で Spawn した task の数 — 終わった子も process の終わりまで
   数える)。"
  (#^ int pid)
  (#^ str job)
  (#^ int tasks))

(defrecord WaitsSeen
  "世界が答える行き止まりの材料: live = 生きている業務の process・waits = 出来事を待っている業務の task(生きている process の物だけ)・
   scenario-waiting = 筋書きの本体が出来事か job の結末を期限なしで待っているか。"
  (#^ (get tuple #(LiveProcess ...)) live)
  (#^ (get tuple #(BusinessWait ...)) waits)
  (#^ bool scenario-waiting))

(defrecord WaitSnapshot
  "行き止まりの判じ(deadlock-of)の材料: WaitsSeen の 3 つ + rows-settled = coordinator の task の行が全部 running か終わりか・
   armed-timers = 業務の timer(#3093 が ArmedTimers の答えを入れる — 既定は空)・world-due = sim の世界の次の予定の刻 ms(#3094 が
   NextWorldDue の答えを入れる — 既定は None)。"
  (#^ (get tuple #(LiveProcess ...)) live)
  (#^ (get tuple #(BusinessWait ...)) waits)
  (#^ bool scenario-waiting)
  (#^ bool rows-settled)
  (setv #^ (get tuple #(ArmedTimer ...)) armed-timers #())
  (setv #^ (| int None) world-due None))

(defrecord SimDeadlock
  "行き止まり: waits = 出来事を待って止まっている業務の task の全部・at-ms = 見張りが見つけた仮想の時計の刻(判じ deadlock-of は時計を
   読まないので None — 見張りが入れる)。"
  (#^ (get tuple #(BusinessWait ...)) waits)
  (setv #^ (| int None) at-ms None))

(defclass SimDeadlockError [RuntimeError]  ; class にする理由: sim-cluster を終わらせる例外の型(検が pytest.raises で名指す — 欄も状態も足さない)
  "sim-cluster の行き止まり: 業務の task が全部 出来事を待って止まり、それを起こす物(筋書き・timer・予定・置き直し)が無い。args = 知らせの文と
   SimDeadlock。")

(defclass SimLivenessError [RuntimeError]  ; class にする理由: sim-cluster を終わらせる例外の型(検が pytest.raises で名指す — 欄も状態も足さない)
  "比で延ばした世界(:timing を渡さない筋書き)で worker の死の判断が出た: 延ばした窓は生死の判断が起きない前提なので、生死を試す筋書きは
   本番の値を :timing で明示する(#3865)。args = 知らせの文と WorkerGone。")

(defeffect NoteEventWait
  "業務の process pid の task 1 つが出来事の待ち(WaitForEvent・WaitForEvents)に入った(waiting 真 — events = 待つ型の名)か、出た(偽)かを世界に
   知らせる(#3078)。"
  {:fields [(: pid int) (: events str) (: waiting bool)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect NoteScenarioWait
  "筋書きの本体が出来事か job の結末を期限なしで待ち始めた(真)か、待ち終えた(偽)かを世界に知らせる(#3078)。"
  {:fields [(: waiting bool)] :answer None :tags {:context "doeff-cluster" :role "intent"}})

(defeffect ArmWaitChange
  "行き止まりの見張りの呼び鈴を掛ける(#3078)。答え = 呼び鈴(Promise)。世界は、業務の待ちか筋書きの待ちが変わった時と、業務の task が
   出来事を待っている間に process が終わるか coordinator が書いた時に鳴らす — 時計の刻みでは鳴らさない。"
  {:answer Promise :tags {:context "doeff-cluster" :role "intent"}})

(defeffect WaitsOf
  "行き止まりの材料(WaitsSeen)を世界から読む(#3078)。"
  {:answer WaitsSeen :tags {:context "doeff-cluster" :role "intent"}})


;; --- 筋の組み立て(純粋)--------------------------------------------------------------------------------

(defk default-workers [system]
  {:pre [(: system System)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker を名指さない時の既定 = 系の全 job の needs の和を提供する 1 台(どの job も置ける・task のために空けておく分は無い)。"
  (val needs (frozenset (gfor j system.jobs n j.needs n)))
  #((SimWorker :name "sim-worker" :provides needs :task-reserve 0)))


(defk declaration-of [system revision environ runtime-env versions]
  {:pre [(: system System) (: revision str) (: environ dict) (: runtime-env (| RuntimeEnv None)) (: versions dict)] :post [(: % Declaration)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "系 → coordinator へ渡す宣言(本番の declare と同じ system-declaration)に、job ごとの environ の上書きと実行環境の宣言を重ねるため
   (計画 2.7 の H・改訂 1 の M — whole.hy の overrides の置き換え先)。上書きの規則(系に無い job・宣言の :environ に無い名・文字列でない
   値は断る)は本番の declare と同じ 1 つ(service_rules.environ-overlay-refusal)。実行環境の宣言は本番の declare の --runtime-env と同じ
   欄に載り、本物の worker が子へ DOEFF_RUNTIME_ENV で渡す(子の run-context の runtime-env)。versions = 送り手の版の識別(筋の versions)。"
  (system-declaration system revision :versions versions :runtime-env runtime-env :environ environ))


(defk sim-plan [system workers environ revision start-ms timing policy outside store [deployments None] [runtime-env None] [nodes None] * notice-broker]
  {:pre [(: system System) (: workers (| tuple None)) (: environ (| dict None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None)) (: store (| Callable None))
         (: deployments (| dict None)) (: runtime-env (| RuntimeEnv None)) (: nodes (| dict None)) (: notice-broker MemoryBroker)]
   :post [(: % SimPlan)] :tags {:context "doeff-cluster" :role "judgment"}}
  "sim-cluster の引数を検めて筋にするため(走らせる前に断る — environ の上書きの誤り・名の重なる worker)。"
  (<- fallback tuple (default-workers system))
  (val chosen (if (is workers None) fallback workers))
  (val names (lfor w chosen w.name))
  (when (or (not chosen) (!= (len names) (len (set names))) (not (all (gfor w chosen (isinstance w SimWorker)))))
    (raise (ValueError (.format "workers は名の重ならない SimWorker の 1 つ以上の tuple: {!r}" chosen))))
  ;; sim の送り手は env の root の外の process として名乗る(env のキーを名乗らない — 実の環境変数を読まない)。版そのものはこの
  ;; process に入っている版で、sim の worker と子も同じ識別を名乗る(1 つの process の中の模擬)。
  (<- versions dict (process-versions {}))
  (<- declaration Declaration (declaration-of system revision (or environ {}) runtime-env versions))
  (<- scaled ClusterTiming (scaled-timing SIM-TIMING-RATIO))
  (SimPlan :system system :declaration declaration :workers chosen :environ (or environ {}) :revision revision :versions versions
           :per-process (if (is outside None) None outside.per-process) :store store :deployments deployments :runtime-env runtime-env :nodes nodes
           :watches-gone (is timing None)
           :start-ms start-ms :timing (or timing scaled) :naming (ClusterNaming) :policy (or policy (WorkerPolicy))
           :passable (+ SIM-PASSABLE (if (is outside None) #() outside.effects)) :notice-broker notice-broker))


(defk parts-of [plan]
  {:pre [(: plan SimPlan)] :post [(: % SimParts)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の Pod の部品を作るため(世界の handler が session で 1 回だけ呼ぶ — 置き場は 1 回の走りに 1 つで、作り直した
   coordinator も同じ置き場から読み直す)。置き場は筋の store が作る(無ければ MemoryWalStore)。知らせの broker は呼び手が渡した筋の
   notice-broker(作らない — 呼び手の世界の受け手と同じ broker・#3850)。"
  (val store (if (is plan.store None) (MemoryWalStore) (plan.store)))
  (when (not (isinstance store MemoryWalStore))
    (raise (TypeError (.format "store は MemoryWalStore の値を作る関数: {!r} が {!r} を返した" plan.store store))))
  (SimParts :queue (RequestQueue) :store store :stop (StopState) :kube (KubeMemory (deepcopy (or plan.deployments {})) (deepcopy (or plan.nodes {})))
            :broker plan.notice-broker))


(defk fresh-truth [name generation now timing]
  {:pre [(: name str) (: generation int) (: now int) (: timing ClusterTiming)] :post [(: % HostTruth)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name の世代 generation の起きた時の宿の真実を作るため(起きた時刻を最後の連絡とみなす — 本番の coordinator への口 と同じ)。
   途絶の柵(fence と、印の在る job の長い方の柵)は sim の筋の時間の設定から(返事の timing が上書きする)。"
  (HostTruth :boot (.format "{}-boot{}" name generation) :boot-at now :processes #() :codes {} :probes {} :statuses []
             :last-ok-ms now :fence-ms timing.fence-ms :keep-fence-ms timing.keep-fence-ms
             :last-desired #() :last-warm #() :programs {} :results {} :task-echo {}))


(defk fresh-hosts [plan]
  {:pre [(: plan SimPlan)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker ごとの最初の世代の宿の真実を作るため(starts-down の worker は止まったまま — StartWorker で起きる)。"
  (var hosts {})
  (for [w plan.workers]
    (<- truth HostTruth (fresh-truth w.name 1 plan.start-ms plan.timing))
    (:= hosts (| hosts {w.name (replace truth :down w.starts-down)})))
  hosts)


(defk reports-in [batch now]
  {:pre [(: batch list) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator が取った要求のうち service の報告(POST /resources/Service/<名>/readiness|metrics)を SimReport にするため。"
  (tuple (gfor r batch
               :setv parts r.parts
               :if (and (= r.method "POST") (= (len parts) 4) (= (get parts 0) "resources") (= (get parts 1) "Service")
                        (in (get parts 3) REPORT-KINDS) (isinstance r.body dict))
               (SimReport :job (get parts 2) :kind (get parts 3) :instance (str (.get r.body "instance" "")) :at now
                          :ready (.get r.body "ready") :reason (str (.get r.body "reason" "")) :role (str (.get r.body "role" "active"))
                          :metrics (.get r.body "metrics")))))


(defk code-view-of [preparation now]
  {:pre [(: preparation SimPreparation) (: now int)] :post [(: % CodeView)] :tags {:context "doeff-cluster" :role "judgment"}}
  "準備 1 つ → worker の観測(揃うまで PREPARING・失敗なら FAILED と失敗の kind・揃えば READY と path)にするため。"
  (cond
    (< now preparation.ready-ms) (CodeView preparation.key CodeState.PREPARING)
    (is-not preparation.failure None) (CodeView preparation.key CodeState.FAILED :detail preparation.failure.detail
                                                :failed-ms preparation.ready-ms :failure preparation.failure)
    True (CodeView preparation.key CodeState.READY (+ "/sim/code/" preparation.key))))


(defk codes-view [codes now]
  {:pre [(: codes dict) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の準備の全部を worker の観測の列にするため。"
  (var views #())
  (for [preparation (.values codes)]
    (<- view CodeView (code-view-of preparation now))
    (:= views (+ views #(view))))
  views)


(defk view-of [truth now]
  {:pre [(: truth HostTruth) (: now int)] :post [(: % WorldView)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の真実 → worker の観測(準備・子 process・入口の検め・待ちの子)にするため。"
  (<- codes tuple (codes-view truth.codes now))
  (WorldView codes truth.processes (tuple (.values truth.probes)) :warm-children truth.warm-children))


(defk run-context-of [worker spec attempt instance]
  {:pre [(: worker str) (: spec JobSpec) (: attempt int) (: instance str)] :post [(: % RunContext)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "起こす process の宿の契約の run-context を作るため: 本番の worker が子へ渡す環境変数を同じ関数(shared/core/run_context_rules の
   worker-context-environ・process-context-environ — 本番の main と ProcessHost.launch が呼ぶ物)で作り、本番の子と同じ読み
   (context-of-environ)で読む(報告の世代が coordinator の report-matches と合い、実行環境の job の子は宣言とキーを受ける)。"
  (<- shared dict (worker-context-environ SIM-URL worker))
  (<- own dict (process-context-environ spec instance attempt))
  (<- ctx RunContext (context-of-environ (| shared own)))
  ctx)


(defk program-path-of [sha]
  {:pre [(: sha (| str None))] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の契約の Program の path を作るため(本番の worker の cache の file と同じ形 <sha>.json — 記録係の header が置き場のキーを読む)。"
  (if sha (+ "/sim/programs/" sha ".json") ""))


(defk control-link [queue revision versions timing]
  {:pre [(: queue RequestQueue) (: revision str) (: versions dict) (: timing ClusterTiming)] :post [(: % SimLink)] :tags {:context "doeff-cluster" :role "judgment"}}
  "sim の仕組み(宣言・検の読み)が coordinator へ話す口を作るため(送り手 sim-declare — 網の切断は受けない)。"
  (SimLink :queue queue :actor DECLARE-ACTOR :revision revision :peer DECLARE-ACTOR :versions versions :timing timing))


;; --- coordinator との話し方(scheduler と時計の effect だけ — 柵の内側の答えも使う)--------------------------------

(defk send-request [link method path query body]
  {:pre [(: link SimLink) (: method str) (: path str) (: query dict) (: body (| dict None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の受け口(要求の列)へ 1 件送り、返事 #(status 本文) を待つため。止まっている coordinator には接続の失敗
   #(None {\"error\" …}) を返す(本番の送り手の接続の失敗に当たる)。返事は本番の HTTP の client と同じ打ち切り(link.timing.client-reply-ms)までしか
   待たず、来なければ途中で切れた失敗 #(None {\"error\" …}) を返す — 受けたまま返事をしない coordinator(拍の判断が落ち続ける等)の前で
   送り手を無期限に止めない(止めると worker の拍と止めの手順が終わらず、模擬が仮想の時計を回し続ける・#2596)。"
  (if (not link.queue.up)
      #(None {"error" "coordinator に接続できない(止まっている)"})
      (do (<- promise Promise (CreatePromise))
          (<- (enqueue-request link.queue (! (http-request method path query body :slot promise :actor link.actor :peer link.peer))))
          (<- answer (| tuple None) (await-answer link.queue promise (/ link.timing.client-reply-ms 1000.0)))
          (if (is answer None)
              #(None {"error" (.format "coordinator の返事が {} 秒で来ない(途中で切れた)" (/ link.timing.client-reply-ms 1000.0))})
              answer))))


(defk send-resent [link method path query body]
  {:pre [(: link SimLink) (: method str) (: path str) (: query dict) (: body (| dict None))]
   :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "何度送っても同じ意味の要求を、届かなければ本番の宛先の部品(resent-request)と同じ期限(IDEMPOTENT-DEADLINE-SECONDS)と間(RESEND-PAUSE-SECONDS)で
   送り直すため(sim の時計で眠る)。答え = 最後の返事(期限を過ぎても届かなければ接続の失敗)。"
  (<- started int (now-epoch-ms))
  (var answer #(None {"error" "送っていない"}))
  (var going True)
  (while going
    (<- sent tuple (send-request link method path query body))
    (<- now int (now-epoch-ms))
    (:= answer sent)
    (if (or (is-not (get sent 0) None)
            (> (+ (/ (- now started) 1000.0) RESEND-PAUSE-SECONDS) IDEMPOTENT-DEADLINE-SECONDS))
        (:= going False)
        (<- (Delay RESEND-PAUSE-SECONDS))))
  answer)


(defk send-shaped [link shape]
  {:pre [(: link SimLink) (: shape tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の送り手と同じ関数が作った要求 #(method path query 本文) を 1 回送るため。"
  (<- answer tuple (send-request link (get shape 0) (get shape 1) (get shape 2) (get shape 3)))
  answer)


(defk send-shaped-resent [link shape]
  {:pre [(: link SimLink) (: shape tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の送り手と同じ関数が作った、何度送っても同じ意味の要求を、届くまで期限の内で送り直すため。"
  (<- answer tuple (send-resent link (get shape 0) (get shape 1) (get shape 2) (get shape 3)))
  answer)


(deff answered-body [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % (| dict list str int float bool None PlainText))] :tags {:context "doeff-cluster" :role "judgment"}}
  "返事 #(status 本文) の本文を返すため(300 以上・届かないなら理由つきの RemoteJobFailed — 本番の raise-for-status に当たる)。"
  (when (or (is (get answer 0) None) (>= (get answer 0) 300))
    (raise (RemoteJobFailed (.format "{}: coordinator の返事 {} {}" what (get answer 0) (get answer 1)))))
  (get answer 1))


(deff refused-or-body [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % (| dict list str int float bool None PlainText))] :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task と温める表の口の返事を読むため: 呼び手の誤り(400・409・413・429)は本番の client と同じ DetachedRefused
   (detached.detached-refusal)、それ以外は answered-body。"
  (let [refusal (detached-refusal (get answer 0) (if (isinstance (get answer 1) dict) (get answer 1) None))]
    (when refusal (raise refusal))
    (answered-body answer what)))


(deff object-body [#^ (| dict list str int float bool None PlainText) body #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: body (| dict list str int float bool None PlainText)) (: what str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "欄を読む口の本文が JSON の object であることを確かめて dict として返すため(object でない本文は本番の送り手と同じく
   RemoteJobFailed — 型を持たない本文を添字で読まない)。"
  (if (isinstance body dict)
      body
      (raise (RemoteJobFailed (.format "{}: coordinator の返事の本文が object でない: {!r}" what body)))))


(deff answered-object [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "欄を読む口の返事 #(status 本文) の本文を dict で返すため(answered-body の後に object であることを確かめる)。"
  (object-body (answered-body answer what) what))


(deff refused-or-object [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えか例外に変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task と温める表の口の返事の本文を dict で返すため(refused-or-body の後に object であることを確かめる)。"
  (object-body (refused-or-body answer what) what))


(deff unreached-reason [#^ tuple answer]  ; defk にできない: 答えの節が返事の理由を読む純粋な判断
  {:pre [(: answer tuple)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "届かなかった返事 #(None {\"error\" 理由}) の理由の 1 行を読むため。"
  (if (isinstance (get answer 1) dict) (str (.get (get answer 1) "error" "")) (str (get answer 1))))


(deff board-written [#^ tuple answer]  ; defk にできない: 答えの節が返事を Program への答えに変える純粋な判断
  {:pre [(: answer tuple)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の compare-and-set の返事を WriteShared の答えにするため(409 = 合わなかった = 偽 — 本番の shared-http(write-accepted)と同じ読み)。"
  (if (= (get answer 0) 409) False (do (answered-body answer "盤に書けない") True)))


(defk remote-outcome [link program needs name environ]
  {:pre [(: link SimLink) (: program (| Program EffectBase)) (: needs frozenset) (: name str) (: environ dict)]
   :post [(: % (| TaskSucceeded TaskFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "RemoteJob を本番の remote-cluster と同じ手順で coordinator へ出し、結果を待つため: 詰めた Program を PUT /programs/<sha> で置き、
   POST /tasks(task-submit-body)で出し、問い合わせ(lease を延ばす)を終わるまで続け、抜ける時は task を落とす。送れない値は送る前に
   断る(encode-program の UnsendableProgram)。版は送り手の版(link.revision)・実行環境の宣言は送り手の宣言(link.runtime-env —
   本番の TaskSender の runtime-env と同じく本文の runtimeEnv に載せる)。"
  (val blob (encode-program program))
  (val sha (program-sha blob))
  (<- put tuple (send-resent link "PUT" (+ "/programs/" sha) {} {"blob" blob "versions" link.versions}))
  (answered-body put "task の Program を置けない")
  (<- body dict (task-submit-body sha link.revision needs name TASK-LEASE-SECONDS link.runtime-env environ))
  (<- sent tuple (send-request link "POST" "/tasks" {} body))
  (val id (get (answered-object sent "task を出せない") "task"))
  (var outcome None)
  (try
    (while (is outcome None)
      (<- (Delay TASK-POLL-SECONDS))
      (<- polled tuple (send-request link "GET" (+ "/tasks/" id) {} None))
      ;; 届かない問い合わせは次の拍で送り直す(本番の宛先の部品(resent-request)と同じく、読みは何度送っても同じ意味)。
      (when (= (get polled 0) 200)
        (:= outcome (outcome-of (get polled 1) id link.revision))))
    (finally
      (<- (send-request link "DELETE" (+ "/tasks/" id) {} None))))
  outcome)


(defk declared-env [env]
  {:pre [(: env (| RuntimeEnv None))] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "送り手の実行環境の宣言を本文の JSON にするため(無ければ None)。"
  (if (is env None)
      None
      (do (<- declared dict (runtime-env->json env))
          declared)))


(defk submit-detached [link program key needs name lease-seconds retain-seconds environ]
  {:pre [(: link SimLink) (: program (| Program EffectBase)) (: key str) (: needs frozenset) (: name str) (: lease-seconds float)
         (: retain-seconds float) (: environ dict)]
   :post [(: % DetachedSubmitAnswer)] :tags {:context "doeff-cluster" :role "protocol"}}
  "SubmitDetached を本番の detached-cluster(detached-submitted)と同じ手順で送るため: 詰めた Program を版と一緒に PUT /programs/<sha> に置き、PUT /detached/<key>
   (detached-submit-body)で出す。どちらも何度送っても同じ意味なので期限まで送り直し、届かなければ DetachedUnreachable(送れたかは
   分からない — key で冪等)。送れない値は送る前に断る(UnsendableProgram)・呼び手の誤りは DetachedRefused。"
  (val blob (encode-program program))
  (val sha (program-sha blob))
  (<- put tuple (send-resent link "PUT" (+ "/programs/" sha) {} {"blob" blob "versions" link.versions}))
  (if (is (get put 0) None)
      (submit-unreachable (unreached-reason put))
      (do (refused-or-body put "task の Program を置けない")
          (<- declared (| dict None) (declared-env link.runtime-env))
          (<- body dict (detached-submit-body sha link.revision needs name lease-seconds retain-seconds declared environ))
          (<- sent tuple (send-resent link "PUT" (detached-path key "") {} body))
          (if (is (get sent 0) None)
              (submit-unreachable (unreached-reason sent))
              (DetachedSubmitted key (get (refused-or-object sent "task を出せない") "created"))))))


(defk bell-span [view timeout-seconds waited]
  {:pre [(: view (| dict None)) (: timeout-seconds (| float int None)) (: waited float)] :post [(: % (| float None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "切り離した task の待ちの 1 回の眠りの上限を決めるため: coordinator に届かない・起きた直後(warming)の間は送り直しの間隔
   DETACHED-POLL-SECONDS(書きが来ない — 本番の送り手の送り直しと同じ)、それ以外は期限までの残り(期限なし = None — 終わりの書きの
   呼び鈴だけで起きる)。"
  (cond
    (or (is view None) (= (.get view "phase") WARMING-PHASE)) DETACHED-POLL-SECONDS
    (is timeout-seconds None) None
    True (max 0.0 (- (float timeout-seconds) waited))))


(defk await-detached [link key timeout-seconds]
  {:pre [(: link SimLink) (: key str) (: timeout-seconds (| float int None))] :post [(: % DetachedAwaited)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitDetached を本番の await-cluster と同じ読みで待つため: GET /detached/<key>(期限まで送り直す・503 = 起きた直後の warming は本文)を
   読み、1 回の読みは本番と同じ判断(detached.awaited-answer)で答えか待ち続けるかを決める。待つ間は読み直さず、読む前に掛けた
   呼び鈴(RequestQueue.bells — 模擬の coordinator がその task の終わりの phase を書いた時に鳴らす)か期限で起きる(proboscis/doeff#631)。
   読みの回数は終わりの書きの前後の 2 回と期限の 1 回ほど(coordinator に届かない間だけ DETACHED-POLL-SECONDS ごとに送り直す)。"
  (<- started int (now-epoch-ms))
  (val bells link.queue.bells)
  (var answer None)
  (while (is answer None)
    (<- bell Promise (CreatePromise))
    ;; 読む前に掛ける(読みの返事と終わりの書きの間に鳴らしを取りこぼさない)。
    (setv (get bells key) (+ (.get bells key #()) #(bell)))
    (<- read tuple (send-resent link "GET" (detached-path key "") {} None))
    (<- now int (now-epoch-ms))
    (val waited (/ (- now started) 1000.0))
    (val view (cond (is (get read 0) None) None
                    (= (get read 0) 503) (object-body (get read 1) "task を読めない")
                    True (refused-or-object read "task を読めない")))
    (:= answer (awaited-answer view (unreached-reason read) key waited timeout-seconds))
    (when (is answer None)
      (<- span (| float None) (bell-span view timeout-seconds waited))
      (<- (promise-or-timeout bell.future span)))
    ;; 鳴らなかった呼び鈴を外す(鳴った物は鳴らした側が外している)。
    (val left (tuple (gfor b (.get bells key #()) :if (is-not b bell) b)))
    (if left (setv (get bells key) left) (.pop bells key None)))
  answer)


(defk ended-task-keys [writes]
  {:pre [(: writes tuple)] :post [(: % frozenset)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の 1 回の Persist の書き(TableWrite の組)から、終わりの phase(cluster_model.ENDED-PHASES)を書いた切り離した task の
   key を読むため(その key の呼び鈴を鳴らす)。"
  (frozenset (gfor w writes
                   :setv row w.value
                   :if (and (.startswith w.key "task/") (isinstance row dict) (.get row "detached") (.get row "key")
                            (in (.get row "phase") ENDED-PHASES))
                   (get row "key"))))


(defk ring-ended-tasks [queue writes]
  {:pre [(: queue RequestQueue) (: writes tuple)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "Persist が書き終えた書きの組で終わった切り離した task の呼び鈴を外して鳴らすため(待っている送り手が 1 回だけ読み直す)。
   答え = 鳴らした呼び鈴の数。"
  (<- keys frozenset (ended-task-keys writes))
  (val rung (lfor key (sorted keys) bell (.pop queue.bells key #()) bell))
  (for [bell rung]
    (<- (CompletePromise bell None)))
  (len rung))


(defk read-runners [link]
  {:pre [(: link SimLink)] :post [(: % (| tuple RunnersUnreachable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadRunners を本番の detached.runners-read と同じく GET /state の workers から読むため(届かなければ RunnersUnreachable)。"
  (<- read tuple (send-resent link "GET" "/state" {} None))
  (if (is (get read 0) None)
      (runners-unreachable (unreached-reason read))
      (runner-facts-of-view (get (answered-object read "名簿を読めない") "workers"))))


(defk read-services [link]
  {:pre [(: link SimLink)] :post [(: % (| tuple ServicesUnreachable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadServices を本番の detached.services-read と同じく GET /resources/Service の items から読むため(届かなければ ServicesUnreachable・
   #3479)。"
  (<- read tuple (send-resent link "GET" "/resources/Service" {} None))
  (if (is (get read 0) None)
      (services-unreachable (unreached-reason read))
      (service-facts-of-view (get (answered-object read "Service の一覧を読めない") "items"))))


(defk await-runners-change [link after timeout-seconds]
  {:pre [(: link SimLink) (: after int) (: timeout-seconds float)] :post [(: % RunnersChangeAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitRunnersChange を本番の detached.runners-changed と同じく GET /watch で 1 回待ち、同じ読み(detached.runners-change-of)で
   答えるため(#1934)。"
  (<- answer tuple (send-request link "GET" "/watch" (watch-query after timeout-seconds) None))
  (runners-change-of (get answer 0) (if (is (get answer 0) None) (unreached-reason answer) (get answer 1))))


(defk await-service-ready [link name]
  {:pre [(: link SimLink) (: name str)] :post [(: % ServiceReady)] :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitServiceReady を本番の detached.service-ready-awaited と同じ読み(detached.service-ready-of の Service の読みと、GET /watch の版の
   変化の待ち)で答えるため(#3470 — 模擬の job が依る service の止まりを、本番と同じ形で越える)。模擬の coordinator に届かない間だけ
   1 秒の間を置いて問い直す(本番の答え手の poll-seconds の既定と同じ)。"
  (var after 0)
  (while True
    (<- read tuple (send-request link "GET" (+ "/resources/Service/" (url-quote name :safe "")) {} None))
    (<- ready (| bool None) (service-ready-of (get read 0) (if (is (get read 0) None) (unreached-reason read) (get read 1))))
    (when (is ready True)
      (return (ServiceReady :name name :revision after)))
    (<- change (await-runners-change link after (/ link.timing.watch-max-ms 1000.0)))
    (match change
      (RunnersChange :revision revision) (:= after revision)
      (RunnersWatchMissing :detail detail)
        (raise (RuntimeError (.format "Service {!r} の Ready を版の変化で待てない(coordinator に GET /watch が無い): {}" name detail)))
      _ (<- (Delay 1.0)))))


(deff warm-answer-of [#^ tuple answer #^ str what]  ; defk にできない: 答えの節が返事を Program への答えに変える純粋な判断
  {:pre [(: answer tuple) (: what str)] :post [(: % WarmAnswer)] :tags {:context "doeff-cluster" :role "judgment"}}
  "温める表の返事を、本番の warm-cluster と同じ読み(同じ定義 detached.warm-unconnected・warm-server-failure)で答えにするため:
   期限まで届かない = WarmUnreachable・coordinator の 5xx = WarmUnreachable・断り(400 ほか)= DetachedRefused・それ以外 = 行の姿。"
  (cond
    (is (get answer 0) None) (warm-unconnected (unreached-reason answer))
    (>= (get answer 0) SERVER-ERROR) (warm-server-failure (get answer 0) (str (get answer 1)))
    True (let [body (refused-or-body answer what)]
           ;; 温める表の口の本文は行の dict(refused-or-body の答えの形は口ごとなので、ここで確かめて絞る)。
           (if (isinstance body dict)
               (warm-state-of-json body)
               (raise (TypeError (.format "{}: 温める表の返事の本文が dict でない: {!r}" what body)))))))


(defk warm-write [link env needs ttl-seconds holder]
  {:pre [(: link SimLink) (: env RuntimeEnv) (: needs frozenset) (: ttl-seconds float) (: holder str)] :post [(: % WarmAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "WarmRuntimeEnv を本番の warm-cluster(warm-written)と同じ本文(warm-request-body)で POST /warm に書き、今の姿を読むため(届かない・coordinator の
   5xx は本番と同じ WarmUnreachable)。"
  (<- declared dict (runtime-env->json env))
  (<- written tuple (send-resent link "POST" "/warm" {} (warm-request-body declared needs ttl-seconds holder)))
  (warm-answer-of written "温める表に書けない"))


(defk warm-read [link key]
  {:pre [(: link SimLink) (: key str)] :post [(: % WarmAnswer)] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadWarmState を本番の warm-cluster(warm-read)と同じく GET /warm/<キー> で読むため(表に無い行 = 404 は空の姿・届かない・coordinator の
   5xx は本番と同じ WarmUnreachable)。"
  (<- read tuple (send-resent link "GET" (warm-path key) {} None))
  (if (= (get read 0) 404)
      (absent-warm-state key)
      (warm-answer-of read "温める表を読めない")))


(defk await-warm [link key timeout-seconds]
  {:pre [(: link SimLink) (: key str) (: timeout-seconds float)] :post [(: % (| WarmReady WarmFailed WarmWaitExpired))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "AwaitWarm を本番の warm-cluster(warm-awaited)と同じ形で答えるため(#3668 (b)): GET /warm/<キー> で読み、同じ判断(warm_rules.
   warm-wait-answer)が待ちを言えば GET /watch(本番の detached.runners-changed と同じ読み)で版の変化まで待って読み直す。模擬の
   coordinator に届かない間だけ 1 秒の間を置いて問い直す(本番の答え手の既定と同じ)。"
  (<- started int (now-epoch-ms))
  (var after 0)
  (var answer None)
  (while (is answer None)
    (<- read WarmAnswer (warm-read link key))
    (<- now int (now-epoch-ms))
    (val waited (/ (- now started) 1000.0))
    (<- step (| WarmReady WarmFailed WarmWaitExpired None) (warm-wait-answer read key waited timeout-seconds))
    (val left (max 0.0 (- timeout-seconds waited)))
    (if (is-not step None)
        (:= answer step)
        (do (<- change (await-runners-change link after (min (/ link.timing.watch-max-ms 1000.0) left)))
            (match change
              (RunnersChange :revision revision) (:= after revision)
              (RunnersWatchMissing :detail detail)
                (raise (RuntimeError (.format "温める表の行 {!r} の組みを版の変化で待てない(coordinator に GET /watch が無い): {}" key detail)))
              _ (<- (Delay (min 1.0 left)))))))
  answer)


;; --- 柵と答え(process ごと・Program のすぐ外)--------------------------------------------------------------

(defclass TrackedSpawn [Spawn]  ; class にする理由: scheduler が isinstance で Spawn と読む印つきの形(外の基底を継ぐ — 欄も状態も足さない)
  "柵が包み直した Spawn の印(柵はこの形を包み直さずに通す — 包み直しを繰り返さない)。")


(defk tracked-child [pid body priority daemon]
  {:pre [(: pid int) (: body (| Program EffectBase)) (: priority int) (: daemon bool)] :post [(: % "body の答え")]
   :tags {:context "doeff-cluster" :role "program"}}
  "process pid の中で Spawn した task の本体: body を同じ handler の下の task として起こし、その把手を世界に覚えさせ(process の終わりで
   取り消す)、答えを待って返すため。呼び手がこの task を取り消せば body も取り消す。body は本番の Spawn と同じく Program か効果 1 つ
   (効果をそのまま Spawn する Program も、本番と同じく sim で走る — #2938)。"
  (<- inner Task (TrackedSpawn body :priority priority :daemon daemon))
  (<- (KeepChild pid inner))
  (try
    (<- value (Wait inner))
    value
    (except [TaskCancelledError]
      (<- (Cancel inner))
      (raise))))


(defhandler fence [#^ int pid #^ tuple passable]
  {:tags {:context "doeff-cluster" :role "protocol"}}
  ;; 引数に残す理由: process の中で Spawn した task を、その process の番号で覚える(process の終わりで一緒に取り消す)。柵は宿の答えより
  ;; 外に在り、Program の effect ではない番号を Ask で問えない。
  ;; 宿の答えより外で、SIM-PASSABLE(scheduler と時計)の外の effect を本番の子と同じ未処理の例外で Program へ投げ返す(改訂 1 の B)。
  ;; これが無いと、sim の外側(検の handler・sim の世界)が本番には無い答えを黙って返し、本番で答えの無い effect が sim だけで通る。
  ;; Spawn は tracked-child で包み直して元の継続のまま送る(reperform — 子の task は Program の中の handler と柵を持ち運ぶ。答えを受けて
  ;; 把手を覚える形にすると、子の task が柵より内の handler を失う)。子の本体が起きた時に把手を世界に覚えさせる(KeepChild は柵の
  ;; 仕組みの effect なので、ここだけは外へ通す)。本番の子 process の中の task は process と一緒に消える — 柵の外で動き続けさせない。
  (Spawn []
    :when (not (isinstance effect TrackedSpawn))
    (reperform (TrackedSpawn (tracked-child pid effect.program effect.priority effect.daemon)
                             :priority effect.priority :daemon effect.daemon)))
  (KeepChild []
    (reperform effect))
  ;; 止めの問いと待ちは process の信号の口(柵のすぐ外の process-signals — 本番の子 process の SIGTERM の代役・#3145)へ通す。
  (StopRequested []
    (reperform effect))
  (AwaitStop []
    (reperform effect))
  ;; 退きの知らせの待ちは process の知らせの口(柵のすぐ外の process-notices — 本番の子 process の知らせの pipe の代役・#3672)へ通す。
  (AwaitRetirement []
    (reperform effect))
  (EffectBase []
    :when (not (isinstance effect passable))
    (raise (UnhandledEffect (.format "sim の柵: 答えの無い effect {} ({!r}) — 本番の子 process でも答える handler が無い"
                                     (. (type effect) __name__) effect)))))



(defk stop-bridge [pid]
  {:pre [(: pid int)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "process pid の外の世界の止めの待ち(AwaitStop — process-signals の外側の答え手が答える)を待ち、来た理由を process の止めの合図に
   写すため(外の筋書きの止めと worker の TERM の先に来た方で、process の止めの待ちが起きる)。process の中の task として覚えられ、
   process の終わりで一緒に止まる。"
  (<- reason str (AwaitStop))
  (<- (ProcessStopRaised pid reason))
  None)


(defhandler process-signals [#^ int pid #^ tuple passable]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 止めの合図は process ごと(番号で世界の受け手を引く)、外の世界が止めの問いを通すか(passable)は process ごとの
  ;; 外の組で違う。柵より外に在り、Program の effect ではない番号を Ask で問えない。
  ;; process の SIGTERM の代役(#3145): 本番の os-signal-stop-handler と同じ意味で StopRequested・AwaitStop に答える — 理由は worker の
  ;; SignalJob TERM が世界の受け手に立てる。外の世界がその問いを通す(passable)なら、合図が立つまでは外の答え手の答えを使う(外の
  ;; 筋書きの止めも効く)。通さなければ「まだ来ていない」(None)— 受け手を据えた process の止めの問いは本番でも答えがある。
  (StopRequested []
    (<- own (| str None) (ProcessStopAsked pid))
    (if (or (is-not own None) (not (isinstance effect passable)))
        (resume own)
        (do (<- outer (| str None) effect)
            (resume outer))))
  (AwaitStop []
    (<- wait SimStopWait (ProcessStopWait pid (isinstance effect passable)))
    ;; 外の世界の止めの待ちを写す task は process で 1 つ(節の中から起こす — 外側の答え手だけを持つ)。process の中の task として覚え、
    ;; process の終わりで一緒に止める。
    (when wait.bridge
      (<- bridge Task (Spawn (stop-bridge pid)))
      (<- (KeepChild pid bridge)))
    (<- reason str (Wait wait.promise.future))
    (resume reason)))


(defhandler process-notices [#^ int pid]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 退きの知らせは process ごと(番号で世界の受け手を引く)。柵より外に在り、Program の effect ではない番号を Ask で
  ;; 問えない。
  ;; process の知らせの pipe の代役(#3672): 本番の pipe-retirement-notices と同じ意味で AwaitRetirement に答える — 知らせは偽の宿の
  ;; RetireJob(退く)と NoticeJob(取り消し・もう一度退く)が世界の受け手に立てる。待ちは書きで起きる(読み直さない)。
  (AwaitRetirement [after]
    (<- promise Promise (ProcessNoticeWait pid after))
    (<- notice (| Retired HandoffAbandoned) (Wait promise.future))
    (resume notice)))


(defrecord SimChild
  "sim の子 process 1 つに宿が答える物(宿の答え host-answers の引数)。ctx = 宿の契約の run-context・program-path = Program の path・
   environ = 宣言の :environ(上書きを重ねた物)か task の effect の :environ(名 → 値 — どちらも spec.environ)・link = coordinator へ話す口(送り手 = job の名・居る所 = worker)・
   pid = sim の中の process の番号・passable = 柵が外へ通す effect の型(SimPlan.passable)・outside = この process の外の handler の組
   (SimOutside.per-process が作った物 — 柵の外側に並べる)。"
  (#^ RunContext ctx)
  (#^ str program-path)
  (#^ dict environ)
  (#^ SimLink link)
  (#^ int pid)
  (#^ tuple passable)
  (setv #^ tuple outside #()))


(defk send-report [child kind payload]
  {:pre [(: child SimChild) (: kind str) (: payload dict)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "service の報告を本番の sent-report(shared/protocol/service_report.hy)と同じ形で送るため。届かなくても業務を止めない(報告は観測)。"
  (val ctx child.ctx)
  (<- shape tuple (report-request ctx.job (| {"worker" ctx.worker "pid" child.pid "revision" ctx.revision} (.identity ctx)) kind payload))
  (<- (send-shaped child.link shape))
  None)


(defhandler host-answers [#^ SimChild child]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 答えは process ごとに違い(世代・Program の path・environ)、宿の契約の Ask の鍵は本番の宿と同じなので、Ask で
  ;; 区別できない。柵の内側に在るので世界の effect で読むこともできない。
  ;; 本番の宿と土台の HTTP の handler が process に答える物(宿の契約 HOST-CONTRACT の run-context と Program の path・service の報告)に、
  ;; 同じ本文で答える。宣言の :environ は、本番の土台の os-environ-reader と同じ答え方の environ-table-reader を子の spec.environ の上に並べて
  ;; 答える(run-fenced — ここで第 2 の読みを持たない)。
  (Ask [key]
    :when (in key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key HOST-CONTRACT.versions-key))
    (resume (match key
              HOST-CONTRACT.run-context-key child.ctx
              HOST-CONTRACT.program-key child.program-path
              _ child.link.versions)))
  (ReportReady [ready reason role]
    (<- (send-report child "readiness" {"ready" ready "reason" reason "role" role}))
    (resume None))
  (ReportMetrics [metrics]
    (<- (send-report child "metrics" {"metrics" metrics}))
    (resume None)))


(defhandler coordinator-answers [#^ SimLink link]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 送り手ごとに口が違い(job の名・居る所の worker・版・実行環境の宣言)、柵の内側に在るので世界の effect で読めない。
  ;; 本番では土台の HTTP の handler(shared-http・remote-cluster・detached-cluster・warm-cluster)が coordinator へ送るクラスタの約束の
  ;; effect に、同じ本文と同じ送り直しで答える。世界の effect を出さず、要求の列(値)と scheduler・時計の effect だけで話す。
  (ReadShared [prefix]
    (<- answer tuple (send-shaped-resent link (board-read-request prefix)))
    (resume (answered-body answer "盤を読めない")))
  (WriteShared [key value expect ttl-seconds]
    (<- answer tuple (send-shaped link (board-write-request key value (is-not expect ANY) (if (is expect ANY) None expect) ttl-seconds)))
    (resume (board-written answer)))
  (LeaseOp [name op token permits ttl-ms]
    ;; claim と renew は同じ token で何度送っても同じ意味(本番の shared-http と同じく送り直す)。release・drop は 1 回だけ。
    (val shape (lease-request name op token permits ttl-ms))
    (<- answer tuple (if (in op #("claim" "renew")) (send-shaped-resent link shape) (send-shaped link shape)))
    ;; 本番の shared-http と同じく、返事の本文を LeaseAnswer に解いて答える(#2523)。
    (<- leased LeaseAnswer (parse-wire LeaseAnswer (answered-body answer (.format "lease {} の {}" name op))))
    (resume leased))
  (AwaitLeaseFree [name]
    ;; 本番の shared-http と同じ要求(GET /watch?lease=<名>)を同じ送り直しで送り、同じ読みで答える。
    (<- answer tuple (send-shaped-resent link (lease-wait-request name)))
    (resume (lease-wait-answer (answered-body answer (.format "lease {} の空きの待ち" name)))))
  (RemoteJob [program needs name environ]
    (<- outcome (remote-outcome link program needs name environ))
    (resume (settled-value outcome)))
  (SubmitDetached [program key needs name lease-seconds retain-seconds environ]
    ;; 本番の detached-cluster と同じく、effect の EnvVar の tuple を本文の形(名 → 値の object)へ綴ってから送る(#2179)。
    (<- environ-body dict (env-mapping environ))
    (<- submitted (submit-detached link program key needs name (float lease-seconds) (float retain-seconds) environ-body))
    (resume submitted))
  (AwaitDetached [key timeout-seconds]
    (<- awaited (await-detached link key timeout-seconds))
    (resume awaited))
  (CancelDetached [key]
    (<- answer tuple (send-resent link "POST" (detached-path key "/cancel") {} None))
    (resume (get (refused-or-object answer "取り消せない") "cancelled")))
  (ReleaseDetached [key]
    (<- answer tuple (send-resent link "DELETE" (detached-path key "") {} None))
    (resume (get (refused-or-object answer "保持を解けない") "released")))
  (ReadRunners []
    (<- runners (read-runners link))
    (resume runners))
  (ReadServices []
    (<- services (read-services link))
    (resume services))
  (AwaitRunnersChange [after timeout-seconds]
    (<- change (await-runners-change link after (float timeout-seconds)))
    (resume change))
  (AwaitServiceReady [name]
    (<- ready ServiceReady (await-service-ready link name))
    (resume ready))
  (WarmRuntimeEnv [env needs ttl-seconds holder]
    (<- warmed WarmAnswer (warm-write link env needs (float ttl-seconds) holder))
    (resume warmed))
  (ReadWarmState [key]
    (<- warm WarmAnswer (warm-read link key))
    (resume warm))
  (AwaitWarm [key timeout-seconds]
    (<- awaited (await-warm link key (float timeout-seconds)))
    (resume awaited)))


(defrecord ProcessOutside
  "process ごとの外の世界(SimOutside.per-process の答え): handlers = その process の柵の外側に並べる handler(外側が先)・effects =
   その process の柵だけが外へ通す effect の型(isinstance — 基底の型でよい)。共有の外の世界の型(SimOutside.effects)は全 process の
   柵が通すので、本番で job ごとに持つ口(その job の土台だけが答える effect)はここに置く — 系で 1 つの許しの和にすると、本番の土台が
   答えない effect を別の job の外の口が sim で黙って答える(構成のレビュー 2026-09-28 の A)。"
  ;; 欄の型は型の引数まで書く(SimOutside の欄と同じ訳 — #4254)。
  (#^ (get tuple #((get Callable #(... object)) ...)) handlers)
  (setv #^ (get tuple #(type ...)) effects #()))


(defk process-outside [per-process job worker]
  {:pre [(: per-process (| Callable None)) (: job str) (: worker str)] :post [(: % ProcessOutside)] :tags {:context "doeff-cluster" :role "judgment"}}
  "process ごとの外の世界を作るため(SimOutside.per-process を job の名と worker の名で呼ぶ — 無ければ空)。ProcessOutside でない答えは
   断る(黙って外の世界を欠いた process を起こさない)。"
  (when (is per-process None)
    (return (ProcessOutside :handlers #())))
  (val made (per-process job worker))
  (when (not (isinstance made ProcessOutside))
    (raise (TypeError (.format "SimOutside の per-process の答えは ProcessOutside(job {} ・worker {}): {!r}" job worker made))))
  made)


(defk run-fenced [program child once]
  {:pre [(: program DoExpr) (: child SimChild) (: once bool)] :post [(: % SimExit)] :tags {:context "doeff-cluster" :role "program"}}
  "Program を柵と答えの中で走らせ、終わり方を決めるため(本番の job_entry の service / task の入口の終わり方と同じ: service は
   値 = 0・例外 = 1、task は結果を書いて 0。取り消し(止めの合図の受け手を据えていない process への TERM・KILL)= -15 — 受け手を
   据えた process は TERM で止めの節を回して自分で終わる・#3145)。殺された process(Crash = 1・worker の死 = -9)はここへ戻らない —
   本物の SIGKILL と同じく task ごと捨てられ(Discard — 巻き戻さない・finally の effect は走らない)、終わりは殺した側が書く。"
  (try
    ;; 出来事の待ちの印(business-wait-tap — 行き止まりの見張りの材料・#3078)は柵の外側に置く: 柵が外へ通した WaitForEvent・WaitForEvents だけを見て、
    ;; 印の知らせ(NoteEventWait)は柵を通らずに世界へ届く。
    ;; 止めの合図の口(process-signals — 本番の SIGTERM の代役・#3145)は柵のすぐ外: 柵が通した止めの問いと待ちに答え、外の世界が
    ;; 通す時だけ外の答え手へ渡す。
    ;; 退きの知らせの口(process-notices — 本番の知らせの pipe の代役・#3672)も柵のすぐ外。
    (<- value (with-handlers [#* child.outside (business-wait-tap child.pid) (process-signals child.pid child.passable)
                              (process-notices child.pid)
                              (fence child.pid child.passable) (coordinator-answers child.link) (host-answers child)
                              (environ-table-reader child.environ)]
                             program))
    (SimExit :code 0 :result (if once (encode-outcome (TaskSucceeded value)) None) :value (if once None value))
    (except [TaskCancelledError]
      (SimExit :code -15 :result None :detail "止めの合図"))
    (except [error Exception]
      (SimExit :code (if once 0 1) :result (if once (encode-outcome (failed-from error)) None)
               :detail (.format "{}: {}" (. (type error) __name__) error)))))


(defk refused-exit [refusal once]
  {:pre [(: refusal RemoteJobFailed) (: once bool)] :post [(: % SimExit)] :tags {:context "doeff-cluster" :role "judgment"}}
  "Program を解けなかった process の終わり方を決めるため(本番の job_entry と同じ: service は理由を出して 3・task は失敗の結果を書いて 0)。"
  (SimExit :code (if once 0 3)
           :result (if once (encode-outcome (failed-from refusal)) None)
           :detail (.format "{}: {}" (. (type refusal) __name__) refusal)))


(defk deliver-task-result [child result]
  {:pre [(: child SimChild) (: result str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の task の子 process が終わる前に結果を coordinator へ直に届けるのと同じ要求(task_result.task-result-request)を、子の送り手の
   口で 1 回送るため(#1387)。答えは読まない — 届かなければ、世界に書いた結果を worker の heartbeat が運ぶ(本番の file の路と同じ)。
   届ける相手の task の id は本番の子と同じ判断(task_result.task-id-of-job)で子の文脈の job の名から読み、読めなければ送らない。"
  (val task (task-id-of-job child.ctx.job))
  (when (is-not task None)
    (<- (send-shaped child.link (task-result-request task child.ctx.worker child.ctx.instance result))))
  None)


(defk sim-process [worker spec child blob]
  {:pre [(: worker str) (: spec JobSpec) (: child SimChild) (: blob (| str None))]
   :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "sim の子 process 1 つの一生: 詰めた Program を解き(解けなければ本番の入口と同じく service は 3・task は失敗の結果)、柵の中で
   走らせ、task は終わる前に結果を coordinator へ直に届け(本番の job_entry.run-task と同じ)、終わりを世界へ書く(中で Spawn した task も
   一緒に止まる)。殺された process(結果なし)は届けない。"
  (val decoded (if (is blob None)
                   #(None (RemoteJobFailed (.format "Program {} を coordinator の置き場から取れていない" spec.program)))
                   (decoded-program blob)))
  (<- ended SimExit (match decoded
                      #(None refusal) (refused-exit refusal spec.once)
                      #(program None) (run-fenced program child spec.once)))
  (when (is-not ended.result None)
    (<- (deliver-task-result child ended.result)))
  (<- (EndProcess worker child.pid ended))
  None)


;; --- worker の偽の宿 ------------------------------------------------------------------------------------

(defk live-truth [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % HostTruth)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name の世代 boot の宿の真実を読むため。その世代がもう終わっていれば(死んだ・止めた後に次の世代が起きた)WorkerDied で
   その世代の run-worker を終わらせる。"
  (<- truth HostTruth (HostTruthOf name))
  (<- checked HostTruth (checked-truth name boot truth))
  checked)


(defk checked-truth [name boot truth]
  {:pre [(: name str) (: boot str) (: truth HostTruth)] :post [(: % HostTruth)] :tags {:context "doeff-cluster" :role "judgment"}}
  "読んだ宿の真実 truth が worker name の世代 boot のまま動いているかを確かめるため。その世代がもう終わっていれば(死んだ・止めた後に
   次の世代が起きた)WorkerDied でその世代の run-worker を終わらせる(live-truth と、別の問いで真実を読んだ節が同じ判断を使う)。"
  (when (or truth.down (!= truth.boot boot))
    (raise (WorkerDied (.format "worker {} の世代 {} は終わった(今は {}{})" name boot truth.boot (if truth.down "・止まっている" "")))))
  truth)


(defk change-live-truth [name boot change]
  {:pre [(: name str) (: boot str) (: change Callable)] :post [(: % HostTruthChange)] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker name の世代 boot の宿の真実を change で直して置き直すため(読んで直して書く組を世界への問い 1 つに — #2668)。その世代がもう
   終わっていれば live-truth と同じく WorkerDied で run-worker を終わらせる。答え = 読んだ真実と置き直した真実。"
  (<- changed HostTruthChange (ChangeHostTruth name boot True change))
  (when (is changed.after None)
    (raise (WorkerDied (.format "worker {} の世代 {} は終わった(今は {}{})" name boot changed.before.boot
                                (if changed.before.down "・止まっている" "")))))
  changed)


(defk statuses-written [truth statuses]
  {:pre [(: truth HostTruth) (: statuses tuple)] :post [(: % HostTruth)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宿の真実 truth の状態の報告を statuses から綴り直した真実を作るため — PublishStatus が ChangeHostTruth の change に渡し、世界の節の中で
   走る(綴りの status-report は何の効果も待たない)。"
  (<- rows list (status-report statuses truth.task-echo truth.results))
  (replace truth :statuses rows))


(defk accepted-programs [link wanted known]
  {:pre [(: link SimLink) (: wanted list) (: known dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言の job と task のうち、まだ取っていない詰めた Program を coordinator の /programs/<sha> から取るため(本番の
   coordinator への口の fetched-programs と同じ — 中身の sha256 がキーと合わない物は取らない・取れなければ次の拍で試し直す)。"
  (var fetched {})
  (for [sha wanted]
    (when (not-in sha known)
      (<- answer tuple (send-request link "GET" (+ "/programs/" sha) {} None))
      (when (and (= (get answer 0) 200) (= (program-sha (get (get answer 1) "blob")) sha))
        (:= fetched (| fetched {sha (get (get answer 1) "blob")})))))
  fetched)


(defk beat-body [worker truth sent-at stopping plan]
  {:pre [(: worker SimWorker) (: truth HostTruth) (: sent-at int) (: stopping bool) (: plan SimPlan)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "宿の真実 truth から、刻 sent-at に送る heartbeat の本文(本番の coordinator への口の polled と同じ heartbeat-body・env-heartbeat-part —
   名乗る止まり始め stopping も載せる・#2819)を綴るため。plan = sim の筋(宿の世代の始めに 1 度読んだ物 — 拍ごとに世界へ聞かない・#3054 の C-6)。"
  (<- views tuple (codes-view truth.codes sent-at))
  ;; 今持っている印(#2804 — 本番の coordinator への口の beat と同じ判断)。印を知らない古い worker の代役は欄を載せない。
  (<- kept tuple (keep-marks-held truth.last-desired))
  (val named (if (is worker.claims-provides None) worker.provides worker.claims-provides))
  (val named-exclusive (if (is worker.claims-exclusive None) worker.exclusive worker.claims-exclusive))
  (<- base dict (heartbeat-body :name worker.name :provides (tuple (sorted named)) :exclusive (tuple (sorted named-exclusive))
                                :node worker.node
                                :capacity (if (is worker.overstates-capacity None) worker.capacity worker.overstates-capacity)
                                :task-reserve (if (is worker.claims-task-reserve None) worker.task-reserve worker.claims-task-reserve)
                                :versions (or worker.versions plan.versions)
                                :statuses truth.statuses :endpoint (+ "sim://" worker.name)
                                :boot (if worker.fresh-boot-every-beat (+ truth.boot "-" (str sent-at)) truth.boot)
                                :boot-at truth.boot-at :tools {} :kept kept :stopping stopping))
  (val full (| base (env-heartbeat-part (env-report views "ok" (frozenset)) (current-platform))))
  (if worker.ignores-keep-marks (dfor #(k v) (.items full) :if (!= k "keptWhenCutOff") k v) full))


(defk heartbeat [worker boot stopping plan parts]
  {:pre [(: worker SimWorker) (: boot str) (: stopping bool) (: plan SimPlan) (: parts SimParts)]
   :post [(: % (| DesiredJobs DesiredUnreadable))] :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の coordinator への口の polled の代役: 生存・能力・版・状態・root の名乗り・止まり始め(stopping — 拍の Program が宣言の読みで渡した
   止まり・#2819)を同じ本文(beat-body — heartbeat-body・env-heartbeat-part)で送り、返事の job と task と温める表の行を宣言として返す。
   届かなければ desired-when-unreachable(本番と同じ判断)。plan と parts は宿の世代の始めに 1 度読んだ物(#3054 の C-6)。"
  ;; 送る前に起こしの印を下ろす(送った後に来た変化の印を消さない — 本番の coordinator への口の beat と同じ)。読みと下ろしは世界への
  ;; 問い 1 つ(#2668)— 本文は下ろす前の真実から組む(下ろす欄 woken は本文に載らない)。
  (<- marked HostTruthChange (change-live-truth worker.name boot (fn [truth] (replace truth :woken False))))
  (val before marked.before)
  (<- sent-at int (now-epoch-ms))
  (val link (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name :versions plan.versions :timing plan.timing))
  (<- body dict (beat-body worker before sent-at stopping plan))
  (<- answer tuple (send-request link "POST" "/heartbeat" {} body))
  (<- now int (now-epoch-ms))
  (if (= (get answer 0) 200)
      (do (val reply (get answer 1))
          ;; 返事の宣言の部分(job の行と draining)を本番の coordinator への口の beat と同じ JSON の境界で 1 度だけ解き、draining を
          ;; 版を据え置く印へ写す(#3684)。
          (<- declared DeclaredReply (declared-reply-of-json reply))
          (<- read-jobs tuple (declared-job-specs declared))
          ;; 印を知らない古い worker の代役は返事の印を読み捨てる(欄を知らない版の読みと同じ = 印の無い宣言)。
          (val jobs (if worker.ignores-keep-marks
                        (tuple (gfor s read-jobs (replace s :keep-when-cut-off False)))
                        read-jobs))
          (<- tasks tuple (task-specs (.get reply "tasks" []) (Path "/sim/tasks" worker.name)))
          (val warm (tuple (gfor row (.get reply "warm" []) (warm-env-of-row row (current-platform)))))
          (val ids (sfor t (.get reply "tasks" []) (get t "id")))
          ;; 取った Program は送る前に読んだ真実(before)で足りる(取った Program を書くのはこの宿の heartbeat だけ・世代の確かめは
          ;; 後の書きの change-live-truth がする — 静かな周で世界へ問いを 1 つ減らす・#3871 の単位 5)。
          (<- fetched dict (accepted-programs link (sorted (sfor s (+ jobs tasks) :if s.program s.program)) before.programs))
          ;; 返事を待つ間に他の task が宿の真実を書く — 書く時に読み直す(その間に世代が終わっていれば抜ける)。読みと書きは世界への問い 1 つ。
          (val timing (.get reply "timing"))
          (val echo (dfor t (.get reply "tasks" []) :if (.get t "detached") (get t "id") (dict t)))
          (<- written HostTruthChange
              (change-live-truth worker.name boot
                                 (fn [truth]
                                   (replace truth :last-ok-ms now :last-desired (+ jobs tasks) :last-warm warm :beats (+ truth.beats 1)
                                            :fence-ms (if (and timing (in "fence_ms" timing)) (int (get timing "fence_ms")) truth.fence-ms)
                                            :keep-fence-ms (if (and timing (in "keep_fence_ms" timing))
                                                               (int (get timing "keep_fence_ms"))
                                                               truth.keep-fence-ms)
                                            :programs (| truth.programs fetched)
                                            ;; 返事から外れた task の結果は落とす(本番の accept-tasks が結果の file を消すのと同じ)。
                                            :results (dfor #(k v) (.items truth.results) :if (in k ids) k v)
                                            :task-echo echo
                                            ;; 次の拍の判断の材料(#1933 — 本番の coordinator への口の beat と同じ)。
                                            :fresh True :sent-statuses before.statuses :sent-stopping stopping
                                            :beat-interval-ms (beat-interval-ms timing echo)
                                            :watch-after (reply-revision reply) :beat-bells #()))))
          ;; 起こした後の待ちに、版が進んだことを知らせる(鳴らすのは置き直す前に掛かっていた呼び鈴)。
          (for [bell written.before.beat-bells]
            (<- (CompletePromise bell None)))
          (DesiredJobs (+ jobs tasks) :warm warm))
      (do ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
          (<- unsent HostTruthChange (change-live-truth worker.name boot (fn [truth] (replace truth :fresh False))))
          (val truth unsent.before)
          (if worker.ignores-fence
              (DesiredJobs truth.last-desired :warm truth.last-warm)
              (desired-when-unreachable (- now truth.last-ok-ms) truth.fence-ms truth.keep-fence-ms truth.last-desired truth.last-warm
                                        (unreached-reason answer))))))


(defk release-leases [link job instance]
  {:pre [(: link SimLink) (: job str) (: instance str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "protocol"}}
  "終わった process(job の名 job・世代の名 instance)が持っていた lease を返すため(本番の handlers.release-leases と同じ要求 — 子が
   名乗った担い手と同じ定義の token の頭 <job>/<世代の名>/ の担い手を POST /leases/<名> の drop で外す)。答え = 返した数。届かなければ
   期限で切れる。"
  (val prefix (holder-tokens-prefix (lease-holder job instance)))
  (<- rows tuple (send-shaped link (board-read-request SEMAPHORE-PREFIX)))
  (var dropped 0)
  (when (= (get rows 0) 200)
    (for [#(key row) (.items (get rows 1))]
      (when (is-not (drop-holders row prefix) None)
        (<- answer tuple (send-request link "POST" (+ "/leases/" (url-quote (cut key (len SEMAPHORE-PREFIX) None) :safe "")) {}
                                       {"op" "drop" "token" prefix}))
        (when (and (is-not (get answer 0) None) (< (get answer 0) 300))
          (:= dropped (+ dropped 1))))))
  dropped)


(defk begin-preparation [worker truth key env warm now]
  {:pre [(: worker SimWorker) (: truth HostTruth) (: key str) (: env bool) (: warm bool) (: now int)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "準備 1 つを始めて宿の真実と世界の記録に書くため(揃う時刻 = now + 準備の秒 — コードの木は prepare-seconds・実行環境の root は
   env-prepare-seconds(None なら prepare-seconds)・実行環境の root は worker の env-failure で終わる)。"
  (val seconds (if (and env (is-not worker.env-prepare-seconds None)) worker.env-prepare-seconds worker.prepare-seconds))
  (val preparation (SimPreparation :worker worker.name :key key :env env :warm warm :started-ms now
                                   :ready-ms (+ now (int (* 1000 seconds)))
                                   :failure (if env worker.env-failure None)))
  (<- (PutHostTruth worker.name (replace truth :codes (| truth.codes {key preparation}))))
  (<- (NotePreparation preparation))
  None)


(defk prepare [worker boot key env warm]
  {:pre [(: worker SimWorker) (: boot str) (: key str) (: env bool) (: warm bool)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "コードの木か実行環境の root の準備を起こすため: prepare-seconds の後に揃う(実行環境の root は env-failure を持つ worker なら失敗で
   終わる)。worker のループは待たない(揃うのは ObserveWorld で観測する)。本番の code-host と env-host の PrepareCode・PrepareEnv と同じく、同じ鍵の準備が
   走っている・揃っているなら起こし直さない(先読みの準備を job が頼めば job の準備へ上げる)・失敗した物だけ起こし直す。"
  (<- truth HostTruth (live-truth worker.name boot))
  (<- now int (now-epoch-ms))
  (val current (.get truth.codes key))
  (cond
    (is current None) (<- (begin-preparation worker truth key env warm now))
    (and (< now current.ready-ms) current.warm (not warm))
      (<- (PutHostTruth worker.name (replace truth :codes (| truth.codes {key (replace current :warm False)}))))
    (< now current.ready-ms) None
    (is current.failure None) None
    True (<- (begin-preparation worker truth key env warm now)))
  None)


;; --- 周の間の待ちを起こす物(#3871 の単位 5)---------------------------------------------------------------------------
;; 周の間の待ち(AwaitNextTick)は本番の答え手 tick-pauses が宿の内側で答える(期限・呼び鈴・止めの合図の早い 1 つまで 1 本で待つ)。
;; 宿は起きる物の組(WorkerWakes)に、本番の送り手の口と同じ関数で組んだ期限と、宿の呼び鈴を入れる。宿の呼び鈴は周の頭に掛け
;; (ticked-truth — 周の観測の後に来た出来事も鳴らす)、子 process の終わり・node ごとの死で世界が鳴らして手放す(本番の待つ子の終わりの代わり)。
;; 止めの合図の待ち(AwaitStop)は止めの呼び鈴で待ち、止めの頼みで世界が鳴らす。

(defk rehung-bell [bell]
  {:pre [(: bell (| Promise None))] :post [(: % Promise)] :tags {:context "doeff-cluster" :role "protocol"}}
  "周の頭の呼び鈴 bell を掛け直すため: まだ鳴っていない呼び鈴(None でない)はそのまま渡し、鳴って手放された(None)なら新しく掛ける。"
  (var held bell)
  (when (is held None)
    (<- made Promise (CreatePromise))
    (:= held made))
  held)


(defk ticked-truth [truth]
  {:pre [(: truth HostTruth)] :post [(: % HostTruth)] :tags {:context "doeff-cluster" :role "protocol"}}
  "周の頭の宿の真実 truth に、周の数を 1 足し、鳴って手放された呼び鈴(宿の呼び鈴・止めの呼び鈴・宣言の変化の呼び鈴)を掛け直した
   真実を作るため(宣言の読みが ChangeHostTruth の change に渡し、世界の節の中で走らせる — 読みと掛けを世界への問い 1 つにする)。鳴って
   いない呼び鈴は周をまたいで同じ物を渡す。宣言の変化の呼び鈴は heartbeat の前に、待ちの口を確かめる前から掛ける(本番の
   coordinator への口の armed-bell と同じ — 起動の直後の最初の「変わった」でも起きる・#3871 の単位 4 の直し)。"
  (<- tick Promise (rehung-bell truth.tick-bell))
  (<- wake Promise (rehung-bell truth.wake-bell))
  (<- stop Promise (rehung-bell truth.stop-bell))
  (replace truth :ticks (+ truth.ticks 1) :tick-bell tick :wake-bell wake :stop-bell stop))


(defk host-wakes [worker truth now bell]
  {:pre [(: worker SimWorker) (: truth HostTruth) (: now int) (: bell (| Future None))] :post [(: % WakeSet)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "宿 worker の真実 truth の、刻 now の起きる物の組を作るため。期限 = 本番の送り手の口(coordinator_link の WorkerWakes)と同じ関数で
   組む heartbeat の期限(beat-due — 間隔は反例の worker の beat-every-ms か返事の間隔)・途絶の柵(fence-due)・前の heartbeat が届いて
   いなければ送り直しの刻(now + RESEND-AFTER-MS)と、宿が持つ刻(処理の止まりの明け stalled-until-ms・準備の揃う刻 ready-ms — 本番では
   準備の task の終わり)の早い方。呼び鈴 = 宿の呼び鈴 bell(期限が全部過ぎても起きる物の無い待ちにしない)。bell が None なら、周の頭に
   掛けた鈴がこの周の間に鳴った(周の観測の後の出来事かもしれない)ので今すぐ(本番の、もう終わった子の待ちと同じ)。"
  (val interval (if (is worker.beat-every-ms None) truth.beat-interval-ms worker.beat-every-ms))
  (<- beat (| DueAt DueNever) (beat-due now truth.last-ok-ms interval))
  (<- fence (| DueAt DueNever) (fence-due now truth.last-ok-ms truth.fence-ms truth.keep-fence-ms))
  ;; 時間で取り直す理由: 届かない coordinator の戻りを知る試し(届いた後は heartbeat の期限だけ — 本番の送り手の口と同じ)。
  (val resend (if truth.fresh (DueNever) (DueAt :at (+ now RESEND-AFTER-MS))))
  (<- held (| DueAt DueNever) (due-after now (+ #(truth.stalled-until-ms) (tuple (gfor p (.values truth.codes) p.ready-ms)))))
  (<- due (| DueAt DueNow DueNever) (earliest-wake #(beat fence resend held)))
  (if (is bell None)
      (WakeSet :due (DueNow) :bells #() :exits #())
      (WakeSet :due due :bells #(bell) :exits #())))


(defhandler sim-host [#^ SimWorker worker #^ str boot #^ SimPlan plan #^ SimParts parts]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 同じ組の中で worker ごと・世代ごとに別の宿を並べる(run-worker は自分の名も世代も effect で問わない)ので Ask で
  ;; 区別できない。plan と parts(sim の筋と coordinator の部品 — sim の間変わらない)は世代の始めに run-sim-worker が 1 度読んで運ぶ —
  ;; 拍ごとに世界へ聞かない(#3054 の C-6)。session の値は鍵が宿ごとに分かれず、読むたびに状態の答え手を通る。
  ;; 本物の run-worker の effect に偽の宿で答える(本番の組 = worker/protocol の local-host・coordinator-link・
  ;; lease-release-coordinator・stop-flag)。宿の真実は世界の session に在り、HostTruthOf / PutHostTruth で読み書きする。どの節も先に
  ;; 世代が今のものかを確かめ(live-truth)、終わった世代の run-worker をその場で終わらせる。
  (ReadDesired [stopping]
    ;; heartbeat は送る拍(beat_policy.heartbeat-due — 本番の coordinator への口の polled と同じ判断)だけ送り、それ以外は前の返事の desired。
    ;; 止まり始め(拍の Program が渡す)は状態の報告の違いと同じく、送る間隔を待たずに名乗る(#2819)。周の頭なので、周の数を足し、
    ;; 鳴って手放された呼び鈴を掛け直す(ticked-truth — 読みと掛けは世界への問い 1 つ)。
    (<- ticked HostTruthChange (change-live-truth worker.name boot ticked-truth))
    (val truth ticked.after)
    (<- now int (now-epoch-ms))
    (val watching (and truth.watch-confirmed (not truth.watch-unsupported) (is-not truth.watch-after None)
                       (is truth.watch-failure None)))
    ;; 名乗る止まり(反例の worker silent-stop は名乗らない — 条 C3 の失敗ケース)。
    (val announced (and stopping (not worker.silent-stop)))
    ;; 処理の止まり(StallWorker — #2804)の間は送らない(送りの失敗も無いので fence の判断も走らない)。
    (val due (and (>= now truth.stalled-until-ms)
                  (heartbeat-due watching truth.fresh truth.woken
                                 (or (!= truth.statuses truth.sent-statuses) (!= announced truth.sent-stopping))
                                 (- now truth.last-ok-ms)
                                 (if (is worker.beat-every-ms None) truth.beat-interval-ms worker.beat-every-ms))))
    ;; 自己停止を周期ごとに時間で判じる(#2806 — 本番の coordinator への口の polled と同じ判断)。処理の止まりの間は拍の走らない本番に
    ;; 合わせて判じず、明けた最初の周期で判じる。止めた周期は heartbeat を送らず、次の周期で送る(宣言を捨てた = fresh を下ろす)。
    ;; 反例の worker ignores-fence(印の無い job も途絶で止めない壊れた worker)は判じない。
    (val silenced (if (and (>= now truth.stalled-until-ms) (not worker.ignores-fence))
                      (desired-after-silence (- now truth.last-ok-ms) truth.fence-ms truth.keep-fence-ms truth.fresh
                                             truth.last-desired truth.last-warm)
                      None))
    ;; 拍の間の眠りを起こす呼び鈴は heartbeat の前に掛けてある(周の頭の ticked-truth — 送っている間に来た変化も鳴らす・#2692)。
    (val bell truth.tick-bell)
    (var read (DesiredJobs truth.last-desired :warm truth.last-warm))
    (cond
      (is-not silenced None)
      (do (<- (change-live-truth worker.name boot (fn [t] (replace t :fresh False))))
          (:= read silenced))
      due
      (do (<- beaten (| DesiredJobs DesiredUnreadable) (heartbeat worker boot announced plan parts))
          (:= read beaten)))
    (<- belled (| DesiredJobs DesiredUnreadable) (with-bell read bell))
    (resume belled))
  (ObserveWorld []
    (<- truth HostTruth (live-truth worker.name boot))
    (<- now int (now-epoch-ms))
    (<- view WorldView (view-of truth now))
    ;; 壊れた worker hides-retired(条 C14 の反例)は、入れ替えで名から外した旧の process を観測に載せない — worker の判断は退いた
    ;; process が無いと読み、旧を止める前に次の新を並べる。
    (resume (if worker.hides-retired
                (replace view :processes (tuple (gfor p view.processes :if (is p.retired-from None) p)))
                view)))
  (PrepareCode [revision]
    (<- (prepare worker boot revision False False))
    (resume None))
  (PrepareEnv [key runtime-env warm]
    (<- (prepare worker boot key True warm))
    (resume None))
  (SweepEnvs [pinned]
    (<- (live-truth worker.name boot))
    (resume None))
  (ProbeEntry [spec code-path]
    (val key (spec-hash spec))
    (<- (change-live-truth worker.name boot (fn [truth] (replace truth :probes (| truth.probes {key (ProbeView key ProbeState.PASSED)})))))
    (resume None))
  (ForgetProbes [keep]
    (<- (change-live-truth worker.name boot (fn [truth] (replace truth :probes (dfor #(k v) (.items truth.probes) :if (in k keep) k v)))))
    (resume None))
  ;; 待ちの子(#3646)の代役: 起こした刻に準備済み(模擬に読み込みの秒も thread も VM も無い)・止めは合図の刻に終わる(TERM = -15・
  ;; KILL = -9)・忘れると観測から外す。分かれた task は待ちの子と別に走る(本番の A が別の session なのと同じ — 止めても task に触れない)。
  (StartWarmChild [key launch]
    (<- now int (now-epoch-ms))
    (<- pid int (NextPid))
    (val started (WarmChildView :key key :pid pid :started-ms now :mark (WarmChildMark :threads 1 :vm-live #(0 0 0))))
    (<- (change-live-truth worker.name boot
                           (fn [truth] (replace truth :warm-children (+ (tuple (gfor v truth.warm-children :if (!= v.key key) v)) #(started))))))
    (resume None))
  (StopWarmChild [key stage reason]
    (<- now int (now-epoch-ms))
    (val code (if (= stage StopStage.TERM) -15 -9))
    (<- (change-live-truth worker.name boot
                           (fn [truth] (replace truth :warm-children
                                                (tuple (gfor v truth.warm-children
                                                             (if (and (= v.key key) (is v.exit-code None))
                                                                 (replace v :exit-code code :ended-ms now :detail reason
                                                                          :stop (StopProgress now stage now))
                                                                 v)))))))
    (resume None))
  (ForgetWarmChild [key]
    (<- (change-live-truth worker.name boot
                           (fn [truth] (replace truth :warm-children (tuple (gfor v truth.warm-children :if (!= v.key key) v))))))
    (resume None))
  (StartJob [spec attempt code-path]
    (<- truth HostTruth (live-truth worker.name boot))
    (<- now int (now-epoch-ms))
    (<- pid int (NextPid))
    (val instance (.format "{}-p{}" worker.name pid))
    (<- ctx RunContext (run-context-of worker.name spec attempt instance))
    ;; 観測と記録を Spawn の前に書く(Spawn の直後に新しい task が先に走って終わっても、終わりを書く相手が在る)。
    (<- (PutHostTruth worker.name
                      (replace truth :processes (+ truth.processes #((ProcessView spec.name spec attempt pid now :instance instance))))))
    (<- (NoteProcess (SimProcess :job spec.name :worker worker.name :instance instance :attempt attempt :pid pid
                                 :spec-hash (spec-hash spec) :started-ms now)))
    ;; 節の中から Spawn する — 新しい task は節の外側の handler(世界・時計)だけを持ち、run-worker の中の handler を持たない。
    (<- program-path str (program-path-of spec.program))
    ;; 子の送り手の口は本番の子の TaskSender・DetachedSender と同じく run-context の実行環境の宣言を持つ(cluster_foundation の組)。
    (<- child-env (| RuntimeEnv None) (runtime-env-of-context ctx))
    (val link (SimLink :queue parts.queue :actor spec.name :revision spec.revision :peer worker.name :versions plan.versions :timing plan.timing
                       :runtime-env child-env))
    (<- outside ProcessOutside (process-outside plan.per-process spec.name worker.name))
    (val child (SimChild :ctx ctx :program-path program-path :environ (dict spec.environ) :link link :pid pid
                         :passable (+ plan.passable outside.effects) :outside outside.handlers))
    (<- task Task (Spawn (sim-process worker.name spec child (.get truth.programs spec.program))))
    (<- (KeepHandle pid task))
    (resume None))
  (SignalJob [name pid stage]
    ;; 本番の SignalProcess の代役(#3145): TERM は process の止めの合図の受け手へ理由を立てる(本番の os-signal-stop-handler が SIGTERM で
    ;; 理由を立てるのと同じ — process は止めの節を回して自分で終わる)。受け手を据えていない process(止めを問わない Program — 本番では
    ;; SIGTERM の既定の動きで終わる)と KILL(猶予切れ)は取り消す(exit -15)。
    (<- (live-truth worker.name boot))
    (<- handle (| Task None) (HandleOf pid))
    (when (is-not handle None)
      (match stage
        StopStage.TERM (do (<- delivered bool (ProcessStopRaised pid TERM-REASON))
                           (when (not delivered)
                             (<- (Cancel handle))))
        StopStage.KILL (<- (Cancel handle))))
    (resume None))
  (ReapJob [name pid outcome exit-code]
    (<- (change-live-truth worker.name boot (fn [truth] (replace truth :processes (tuple (gfor p truth.processes :if (!= p.pid pid) p))))))
    (resume None))
  (RetireJob [name pid new-name]
    ;; 名から外すと同時に、観測の notice に「退く」を残し、process の知らせの口へ「退く」を立てる(本番の process-host が shim の標準入力へ
    ;; 書く行の代役・#3672)。壊れた worker silent-notices(条 W2 の反例)は観測だけ書いて知らせない。退いた刻(寿命の上限 retired-ms を
    ;; 数える起点 — #4072 の D-2)も観測に残す(本番の process-host と同じ)。
    (<- retired-at int (now-epoch-ms))
    (<- (change-live-truth worker.name boot
                           (fn [truth] (replace truth :processes (tuple (gfor p truth.processes
                                                                              (if (= p.pid pid)
                                                                                  (replace p :name new-name :retired-from name :notice (Retired)
                                                                                           :retired-at-ms retired-at)
                                                                                  p)))))))
    (when (not worker.silent-notices)
      (<- (ProcessNoticeRaised pid (Retired))))
    (when worker.retire-stops
      (<- handle (| Task None) (HandleOf pid))
      (when (is-not handle None)
        (<- (Cancel handle))))
    (resume None))
  (NoticeJob [name pid notice]
    ;; 退いた process への知らせの変わり目(入れ替えの諦め・その解け — #3672): 観測の notice を書き、process の知らせの口へ立てる
    ;; (RetireJob と同じく、壊れた worker silent-notices は観測だけ書く)。
    (<- (change-live-truth worker.name boot
                           (fn [truth] (replace truth :processes (tuple (gfor p truth.processes
                                                                              (if (= p.pid pid) (replace p :notice notice) p)))))))
    (when (not worker.silent-notices)
      (<- (ProcessNoticeRaised pid notice)))
    (resume None))
  (ReleaseLeases [job instance]
    (<- (live-truth worker.name boot))
    (<- (release-leases (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name :versions plan.versions :timing plan.timing)
                         job instance))
    (resume None))
  (PublishStatus [statuses note]
    ;; 宿の真実の結果と写しで状態の報告を綴り、報告だけを置き直す — 綴り(defk の status-report)は世界の節の中で走らせ、読みと書きを
    ;; 世界への問い 1 つにする(#2668・L1218 の不変条件 — 前に読んでから書くと 1 組で世界を 2 度通る)。
    (<- (change-live-truth worker.name boot (fn [latest] (statuses-written latest statuses))))
    (resume None))
  (StopRequested []
    ;; 世代の確かめと全 worker の止まれを世界への問い 1 つで読む(#3054 の C-6)。止めの頼み(StopWorker・StopWorkers)は本番の
    ;; worker の Pod への SIGTERM に当たるので、理由は本番の os-signal-stop-handler と同じ綴り(#3871 の単位 3)。
    (<- asked HostStop (StopRequestOf worker.name))
    (<- truth HostTruth (checked-truth worker.name boot asked.truth))
    (resume (if (or asked.all-stopping truth.stopping) TERM-REASON None)))
  (EnvReport []
    ;; sim の宿は heartbeat の root の名乗りを世界の root から自分で作る(env-heartbeat-part)ので、拍の Program の問いには None で答える。
    (resume None))
  (WorkerWakes []
    ;; 周の間の待ちを起こす物(#3871 の単位 5)の一番外: 宿の期限(本番の送り手の口と同じ関数)と宿の呼び鈴(host-wakes)。宿の内側に
    ;; 状態を持つ handler は無いので、外へ出し直さずに答える。
    (<- truth HostTruth (live-truth worker.name boot))
    (<- now int (now-epoch-ms))
    (<- wakes WakeSet (host-wakes worker truth now (if (is truth.wake-bell None) None truth.wake-bell.future)))
    (resume wakes))
  (AwaitStop []
    ;; 止めの合図の待ち(周の間の待ちが競わせる — 本番の答え手は os-signal-stop-handler)。止めが頼まれていれば今すぐ、そうでなければ
    ;; 周の頭に掛けた止めの呼び鈴が鳴るまで(鳴って手放されていれば、この周の間に頼まれた — 今すぐ)。
    (<- asked HostStop (StopRequestOf worker.name))
    (<- truth HostTruth (checked-truth worker.name boot asked.truth))
    (when (not (or asked.all-stopping truth.stopping (is truth.stop-bell None)))
      (<- (Wait truth.stop-bell.future)))
    (resume TERM-REASON)))


(defk await-beat [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "起こした後の待ち(または版をまだ知らない待ち)が、次の heartbeat が届くまで眠るため(上限 WAKE-HOLD-SECONDS — 同じ版で待ち直して
   空回りしない)。本番の WatchCell.beats を見る待ちに当たる。起きたら自分の鈴を払う — heartbeat が届かず時間切れで起きた鈴を残すと、
   届かない間ずっと鈴の列が伸び続ける(#2596)。"
  (<- truth HostTruth (HostTruthOf name))
  (when (= truth.boot boot)
    (<- bell Promise (CreatePromise))
    (<- (PutHostTruth name (replace truth :beat-bells (+ truth.beat-bells #(bell)))))
    (<- (promise-or-timeout bell.future WAKE-HOLD-SECONDS))
    ;; 同じ世代なら自分の鈴を払う(読みと書きは世界への問い 1 つ — #2668)。
    (<- (ChangeHostTruth name boot False (fn [woke] (replace woke :beat-bells (tuple (gfor b woke.beat-bells :if (is-not b bell) b)))))))
  None)


(defk note-watch [name boot after reading]
  {:pre [(: name str) (: boot str) (: after int) (: reading WatchReading)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "program"}}
  "待ち 1 回の答えを宿の真実へ写すため(本番の coordinator への口の watch-loop の枝と同じ意味)。答え = 待ち続けるか。"
  ;; 書く答えは読みと書きを世界への問い 1 つで(#2668)— 世代が終わっていれば書かずに抜ける。
  (match reading.kind
    WatchKind.UNSUPPORTED (do (<- (ChangeHostTruth name boot False (fn [truth] (replace truth :watch-unsupported True))))
                              False)
    WatchKind.FAILED (do (<- truth HostTruth (HostTruthOf name))
                         (if (!= truth.boot boot)
                             False
                             (do (<- (Delay WATCH-RETRY-SECONDS))
                                 True)))
    ;; 「変わった」は拍に heartbeat を送らせ(woken)、拍の間の眠りの呼び鈴を鳴らして手放す(#2692 — 鳴らすのは書いた後: 世界の節は
    ;; session の書きを scheduler の切り替わる effect より前に済ませる)。
    WatchKind.CHANGED (do (<- changed HostTruthChange
                              (ChangeHostTruth name boot False
                                               (fn [truth] (replace truth :watch-confirmed True :woken True :tick-bell None))))
                          (val bell changed.before.tick-bell)
                          (when (and (is-not changed.after None) (is-not bell None))
                            (<- (CompletePromise bell True)))
                          (is-not changed.after None))
    WatchKind.UNCHANGED (do (<- changed HostTruthChange
                                (ChangeHostTruth name boot False
                                                 (fn [truth] (replace truth :watch-confirmed True
                                                                      :watch-after (if (= truth.watch-after after) reading.revision
                                                                                       truth.watch-after)))))
                            (is-not changed.after None))))


(defk watch-desired [worker boot]
  {:pre [(: worker SimWorker) (: boot str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の世代 1 つの名指しの待ち(本番の coordinator への口の背景の task の代役 — worker-keeper が世代ごとに Spawn し、世代の終わりで
   取り消す): 前の heartbeat の版の後の変化を GET /watch で待ち、「変わった」なら拍に heartbeat を送らせ(woken)、次の heartbeat が
   届くまで眠る。待つ口が無い(404)・世代が終わったら抜ける。網は worker と同じ(切れていれば届かない)。"
  (<- parts SimParts (PartsOf))
  (<- plan SimPlan (PlanOf))
  (val link (SimLink :queue parts.queue :actor worker.name :revision plan.revision :peer worker.name :versions plan.versions :timing plan.timing))
  (var going True)
  (while going
    (<- truth HostTruth (HostTruthOf worker.name))
    (cond
      (or truth.down (!= truth.boot boot) truth.watch-unsupported) (:= going False)
      (or (is truth.watch-after None) truth.woken) (<- (await-beat worker.name boot))
      True (do (val after truth.watch-after)
               (<- answer tuple (send-request link "GET" "/watch" (watch-params after worker.name boot truth.watch-confirmed plan.timing.watch-max-ms) None))
               (<- more bool (note-watch worker.name boot after (watch-reading (get answer 0) (get answer 1))))
               (:= going more))))
  boot)


(defk guarded-watch [worker boot]
  {:pre [(: worker SimWorker) (: boot str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "世代の名指しの待ちの task の本体: watch-desired を回し、思わぬ例外で止まれば理由を世界の記録に残して、その世代を拍ごとの heartbeat に
   戻すため(本番の「待ちの thread が止まった」の 1 行と watching の気づきに当たる — 模擬で黙って落ちると模擬の検が壊れを隠す)。
   世代の終わりの取り消しは記録しない。"
  (try
    (<- (watch-desired worker boot))
    (except [cancelled TaskCancelledError]
      (raise cancelled))
    (except [error Exception]
      (<- now int (now-epoch-ms))
      (<- (NoteWatchFailure (SimWatchFailure :worker worker.name :boot boot :at now
                                             :reason (.format "{}: {}" (. (type error) __name__) error))))))
  boot)


(defk run-sim-worker [worker policy boot]
  {:pre [(: worker SimWorker) (: policy WorkerPolicy) (: boot str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の世代 1 つ: 本物の run-worker を、本番の入口と同じ組み立て(worker-on)で偽の宿の組の上で回す(止まれの合図で全 job を
   止めの手順で回収して終わる)。"
  ;; 周の間の待ち(AwaitNextTick)は本番の答え手 tick-pauses が宿の内側で答える(#3871 の単位 5 — 競わせの task は宿の節の外側の
  ;; handler を持つので、止めの合図の待ちには宿が答える)。sim の筋と coordinator の部品は世代の始めに 1 度読んで宿へ運ぶ(拍ごとに
  ;; 世界へ聞かない — #3054 の C-6)。
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  ;; worker の調整ループ自身の行(起こしの見送り — #3713・本番は入口の slog-handler が出す)は sim-host の内側で捨てる。job の Program は
  ;; sim-host の節の中から Spawn され、節の外側の handler だけを持つので、job の slog はこの捨てる handler に当たらず外の受け手へ届く。
  (<- (worker-on [(sim-host worker boot plan parts) tick-pauses slog-discard-handler] policy))
  boot)


(defk generation-end [loop]
  {:pre [(: loop Task)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の世代の run-worker が抜けるのを待ち、抜け方(stopped = 止めの手順で回収した・died = 死んだ)を返すため。"
  (try
    (<- (Wait loop))
    "stopped"
    (except [WorkerDied]
      "died")))


(defk worker-keeper [worker policy]
  {:pre [(: worker SimWorker) (: policy WorkerPolicy)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "worker 1 台の node の一生: 止まっていれば(死んだ・止めた・止まったまま始まる worker)StartWorker を待ち、今の世代の run-worker を
   回す。抜ければ(死んだ・止めた)次の世代を待つ。全 worker の止まれの合図で抜ける。各世代は、その世代を起こす時の値(WorkerOf —
   止まっている間に ReplaceWorker で差し替えた値)で回す(#3366)。"
  (var going True)
  (while going
    (<- revival (| Promise None) (RevivalOf worker.name))
    (when (is-not revival None)
      (<- (Wait revival.future)))
    (<- stopping bool (WorkersStopping))
    (if stopping
        (:= going False)
        (do (<- truth HostTruth (HostTruthOf worker.name))
            (<- now-worker SimWorker (WorkerOf worker.name))
            (<- loop Task (Spawn (run-sim-worker now-worker policy truth.boot)))
            ;; 世代ごとの名指しの待ち(本番の coordinator への口の背景の task に当たる — 世代の終わりで取り消す)。
            (<- watcher Task (Spawn (guarded-watch now-worker truth.boot)))
            (<- (generation-end loop))
            (<- (Cancel watcher))
            (<- (WorkerEnded worker.name truth.boot))
            (<- again bool (WorkersStopping))
            (:= going (not again)))))
  worker.name)


;; --- coordinator の Pod ---------------------------------------------------------------------------------

(defclass [dataclass] StepBook []
  "coordinator の一生 1 つの、調停ループの 1 歩の間に observe-requests が溜める物(#2670 の根 A): released = この歩で返事を済ませた要求
   (歩の終わりに覚えから外す)・noted = この歩の Persist の writes の列(書きの順 — 歩の終わりに知らせる)・stopped = 歩の頭の判定が
   止まりと答えた(止まる調停ループの待ちへの返事 release-watchers はすぐ外す — 次の歩の頭は無い)。歩の頭ごとに空にする。一生ごとに
   作り直す。session の値に持たないのは、読み書きのたびに状態の答え手までの効果になり、世界への問い 1 つと同じ重さになるため。"
  (setv #^ tuple released #()
        #^ tuple noted #()
        #^ bool stopped False))


(defhandler observe-requests [#^ StepBook book #^ RequestQueue queue]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 1 歩の間に溜める帳面 book は coordinator の一生ごとに作る可変の箱(coordinator-life が作って渡す)— session の値に
  ;; すると読み書きのたびに状態の答え手までの効果になる(StepBook の docstring)。一生をまたがない。queue = coordinator の要求の列
  ;; (世界の部品 SimParts.queue と同じ物)— 落ちの注入が待っているか(crash-waiting — 世界の CrashCoordinator が立て、落ちで下ろす)を
  ;; 書きの前に読む(#3132)。
  ;; 調停ループの一番内側: 取った要求のうち網の切れた worker から届いた物を落とし(送り手には接続の失敗 — 本番では届かない)、故障を
  ;; 入れている口(FailRoute)への物に注入した status で答えて調停ループへ渡さず、service の
  ;; 報告(ReportReady・ReportMetrics)を世界へ記録し、返事の前に落ちた時に接続の失敗を返す相手として取った要求を覚える。筋書きの
  ;; 止まり(止めの合図)と落ち(Persist の失敗 — 返事をせずに落ちる)を注入する。効果はそのまま外側(本物の組)へ出し直す。
  ;; 1 歩の世界への問いは 2〜3 つ(#2670 の根 A): 取りの篩い(AdmitBatch)・歩の終わり(StepEnded — 次の歩の頭の止まりの判定の刻に、
  ;; この歩の返事の手放しと書きの知らせと止まりの判定を 1 つで)と、落ちの注入が待っている間だけ書きの前の落ちの判断(PersistCrashDue —
  ;; 書きより前に要る・#3132)。返事と書きは帳面 book に溜める。
  (NextRequests [timeout-seconds limit]
    (<- batch list effect)
    ;; 篩い・報告の記録・覚えを、世界への問い 1 つで(#3054 の C-6)。
    (<- admitted Admission (AdmitBatch (tuple batch)))
    (val faults admitted.faults)
    (val kept (list admitted.kept))
    (for [r batch]
      (cond
        (in r.peer faults.cut) (<- (CompletePromise r.slot #(None {"error" CUT-REASON})))
        (in #(r.method r.path) faults.failing)
          (<- (CompletePromise r.slot #((get faults.failing #(r.method r.path)) {"error" FAULT-REASON})))))
    (resume kept))
  (Reply [request status body]
    ;; 返事を済ませた要求は歩の終わりに覚えから外す(止まる調停ループの返事は次の歩の頭が無いので、すぐ外す)。
    (if book.stopped
        (<- (ReleaseRequest request))
        (setv book.released (+ book.released #(request))))
    (<- effect)
    (resume None))
  (Persist [writes]
    ;; 落ちの注入の判断(書きより前): 落ちの注入が待っている時だけ筋書きの落ちの頼みを世界への問い 1 つで判じる — 待っていない書き
    ;; (生存の印の書きの大半)は世界へ問わない(#3132)。
    (when queue.crash-waiting
      (<- crash bool (PersistCrashDue))
      (when crash
        ;; 落ちる前に、この歩で溜めた書きの知らせを出す(書き終えた書きの待ち手は、落ちても起こす)。
        (for [done book.noted]
          (<- (NoteCoordinatorWrite done)))
        (setv book.noted #())
        (raise (OSError "sim: Persist の失敗(注入 — fsync の失敗)。返事をせずに落ちる"))))
    (<- effect)
    ;; 書き終えたことは歩の終わり(StepEnded)に知らせる — 書きで終わった切り離した task の待ち手(proboscis/doeff#631)と準備の状態を
    ;; 待つ待ち手(AwaitReadiness・#3053)を起こす。
    (setv book.noted (+ book.noted #(writes)))
    (resume None))
  (CoordinatorStopRequested []
    ;; 歩の終わりの問い 1 つ: この歩の返事の手放し・書きの知らせ・止まりの判定。
    (<- due bool (StepEnded book.released book.noted))
    (setv book.released #()
          book.noted #())
    (if due
        (do (setv book.stopped True)
            (resume True))
        (do (<- asked bool effect)
            (setv book.stopped asked)
            (resume asked)))))


(defk fail-open-requests [queue held reason]
  {:pre [(: queue RequestQueue) (: held tuple) (: reason str)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "止まった・落ちた coordinator が返事をしていない要求(取った物と並んでいた物)に接続の失敗を返すため(送り手を待たせたままにしない)。"
  (val open (+ (list held) (list queue.pending)))
  (setattr queue "pending" [])
  (for [request open]
    (<- (CompletePromise request.slot #(None {"error" reason}))))
  (len open))


(defk coordinator-life [plan parts]
  {:pre [(: plan SimPlan) (: parts SimParts)] :post [(: % str)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod の一生 1 つ: 置き場から読み直して(無ければ新しい状態で)、この Pod の環境から走っている doeff の版を本番の入口と
   同じ読み(with-running-commit — 環境の答え手は模擬の Pod の環境 = ReplaceCoordinatorEnviron の値・#3772)で載せ、本物の調停ループを
   emulated-handlers の上で回し、止まる(止めの合図)か落ちる(Persist の失敗)まで。答え = 止まり方。"
  (<- now int (now-epoch-ms))
  ;; 読み直しの 1 行の報告(本番の入口と同じ slog)は、ここで stderr へ出す。
  (var state None)
  (if (.exists parts.store)
      (do (<- loaded ClusterState (with-handlers [slog-handler] (load-state NO-STATE-FILE parts.store now)))
          (:= state loaded))
      (:= state (ClusterState :started-ms now :task-prefix (fresh-task-prefix now))))
  (<- environ (get tuple #(EnvEntry ...)) (CoordinatorEnvironOf))
  (<- started ClusterState (with-handlers [(scripted-process-handler (ProcessScript :commands #() :env environ))]
                             (with-running-commit state)))
  (<- (CoordinatorStarted now))
  (setattr parts.queue "up" True)
  (try
    (<- (with-handlers (emulated-handlers parts.queue parts.store parts.stop parts.kube parts.broker [(observe-requests (StepBook) parts.queue)])
          (run-coordinator started plan.timing plan.naming)))
    "stopped"
    (except [error OSError]
      (str error))))


(defk coordinator-pod []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod の代役: 一生を回し、止まる・落ちるたびに返事の無い要求へ接続の失敗を返し、筋書きの止まっている秒の後に作り直す
   (止めの合図で止まり、止まっている秒が無ければ終わる)。答え = 起きた回数。"
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  (var lives 0)
  (var going True)
  (while going
    (<- outcome str (coordinator-life plan parts))
    (setattr parts.queue "up" False)
    (<- held tuple (TakeHeldRequests))
    (<- (fail-open-requests parts.queue held "coordinator が止まった・落ちた(返事なし)"))
    (<- ended int (now-epoch-ms))
    (<- (CoordinatorEnded ended outcome))
    (:= lives (+ lives 1))
    (<- down (| float None) (DowntimeOf))
    (if (is down None)
        (:= going False)
        (<- (Delay down))))
  lives)


(defk await-coordinator [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod が受け付けを始めるまで待つため(宣言を先に送って接続の失敗にしない)。"
  (while (not queue.up)
    (<- (Delay QUEUE-WAIT-SECONDS)))
  None)


(defk first-beats-missing [names]
  {:pre [(: names tuple)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "動いている worker のうち、まだ 1 度も coordinator に名乗れていない(heartbeat の返事を受けていない)物が在るかを見るため(止まったまま
   始まる worker は待たない)。"
  (var missing False)
  (for [name names]
    (<- truth HostTruth (HostTruthOf name))
    (when (and (not truth.down) (= truth.beats 0))
      (:= missing True)))
  missing)


(defk await-workers [names]
  {:pre [(: names tuple)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "筋書きを始める前に、worker が 1 度ずつ coordinator に名乗るのを待つため(筋書きの最初の送りや名簿の読みが、起動の順に左右されない —
   本番の系は worker が名乗ってから使われる)。名乗れない worker が在っても WORKER-WAIT-SECONDS で待つのをやめる。"
  (<- started int (now-epoch-ms))
  (var waiting True)
  (while waiting
    (<- missing bool (first-beats-missing names))
    (<- now int (now-epoch-ms))
    (if (and missing (< (- now started) (* 1000 WORKER-WAIT-SECONDS)))
        (<- (Delay QUEUE-WAIT-SECONDS))
        (:= waiting False)))
  None)


(defk apply-declaration [link declaration]
  {:pre [(: link SimLink) (: declaration Declaration)] :post [(: % tuple)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言を本番の declare(declare.apply-declaration)と同じ順と本文で coordinator へ書くため: Service を全部読み、書く Service(無い・spec が
   変わった)が名指す Program だけを PUT /programs/<sha> で置いてから、無ければ POST(create-body)・spec が変わった Service だけ読んだ版を
   付けて PUT(差分の宣言の判断は本番と同じ declaration_requests の service-read・needed-programs。PUT が 409 を受けたら本番と同じく
   読み直し、spec が同じ — 変わったのは状態だけ — なら今の版で書き直す)。台数は本番と同じく行の値(job の :replicas)。答え = 宣言した
   Service の名(書かなかった Service も含む)。"
  (var reads #())
  (for [row declaration.rows]
    (val path (+ "/resources/Service/" (url-quote (get row "name") :safe "")))
    (<- current (| dict None) (sim-service-current link (get row "name") path))
    (<- read ServiceRead (service-read (get row "name") row path current))
    (:= reads (+ reads #(read))))
  (<- needed (get tuple #(str ...)) (needed-programs declaration reads))
  (for [sha needed]
    (<- put tuple (send-request link "PUT" (+ "/programs/" sha) {}
                                {"blob" (get declaration.programs sha) "versions" (get (get (get declaration.rows 0) "run") "versions")}))
    (answered-body put (+ "program " sha)))
  (for [read reads :if (is-not read.body None)]
    (<- written tuple (sim-service-written-rereading link read))
    (answered-body written (+ "Service " read.name)))
  (tuple (gfor row declaration.rows (get row "name"))))


(defk sim-service-current [link name path]
  {:pre [(: link SimLink) (: name str) (: path str)] :post [(: % (| dict None))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "資源の口 path の Service name の今の資源(GET の本文 — 無ければ None)を読むため(本番の declare.service-current と同じ読み — 宣言の前の
   読みと、409 の後の読み直しが通る)。"
  (<- current tuple (send-request link "GET" path {} None))
  (if (= (get current 0) 404) None (answered-object current (+ "Service " name))))


(defk sim-service-written [link read]
  {:pre [(: link SimLink) (: read ServiceRead)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "読んだ Service 1 つへ書きを送るため(本番の declare.service-written と同じ — 無ければ POST・在れば読んだ版つきの PUT)。答え = 返事
   #(status 本文)。"
  (<- body dict (service-body-of read))
  (<- written tuple (if (is read.version None)
                        (send-request link "POST" "/resources/Service" {} body)
                        (send-request link "PUT" read.target {} body)))
  written)


(defk sim-service-written-rereading [link read]
  {:pre [(: link SimLink) (: read ServiceRead)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "読んだ Service 1 つへ書きを送り、版つきの書き直しが 409 を受けたら読み直して書き直すため(本番の declare.service-written-rereading と
   同じ判断 — declaration_requests.reread-after-conflict・CONFLICT-REREADS 回まで。spec が変わっていれば最後の 409 を返す)。"
  (var sent read)
  (var rereads 0)
  (<- first tuple (sim-service-written link sent))
  (var written first)
  (while (and (= (get written 0) 409) (is-not sent.version None) (< rereads CONFLICT-REREADS))
    (:= rereads (+ rereads 1))
    (<- current (| dict None) (sim-service-current link sent.name sent.target))
    (<- again (| ServiceRead None) (reread-after-conflict sent current))
    (when (is again None)
      (break))
    (:= sent again)
    (<- retried tuple (sim-service-written link sent))
    (:= written retried))
  written)


;; --- 準備の状態と job の次の process の待ち(AwaitReadiness・AwaitJobProcess — 書きで起こす・#3053)----------------

(defrecord NextWaiter
  "AwaitJobProcess の待ち手 1 つ(job の process のうち pid が excluding に無い物を待つ — 世界が process を記録した時に起こす)。"
  (#^ str job)
  (#^ (get tuple #(int ...)) excluding)
  (#^ Promise promise))


(defk job-process-outside [log job excluding]
  {:pre [(: log tuple) (: job str) (: excluding (get tuple #(int ...)))] :post [(: % (| SimProcess None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "AwaitJobProcess の待つ相手(job の process のうち pid が excluding に無い最初の物 — 記録の順)が起きていればその記録を、まだなら None
   を返すため。"
  (next (gfor r log :if (and (= r.job job) (not-in r.pid excluding)) r) None))


(defk service-answer-of [link name]
  {:pre [(: link SimLink) (: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "模擬の coordinator の GET /resources/Service/<name> を 1 回読むため(本番と同じ口 — 答え = (code 本文)・本文は 200 なら JSON の
   object)。準備の状態と待ちの答えは、本番と同じ readiness-of-body・readiness-wait-answer が読む。"
  (<- answer tuple (send-request link "GET" (+ "/resources/Service/" (url-quote name :safe "")) {} None))
  answer)


;; --- process の終わりの待ち(AwaitProcessEnded — 世界が書きで起こす)---------------------------------------------

(defrecord DueWaiters
  "process の終わりを書いた後の待ち手の分け方(due-end-waiters の答え)。remaining = まだ待つ job → Promise の tuple・due = 起こす
   #(Promise 答え) の tuple(掛けた順)。"
  (#^ dict remaining)
  (#^ tuple due))


(defk ended-process [log job]
  {:pre [(: log tuple) (: job str)] :post [(: % (| SimProcess None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "AwaitProcessEnded の待つ相手(job の今の最後の process)が終わっていればその記録を、動いている・まだ無ければ None を返すため。"
  (val mine (lfor r log :if (= r.job job) r))
  (if (and mine (is-not (. (get mine -1) ended-ms) None)) (get mine -1) None))


(defk first-process [log job]
  {:pre [(: log tuple) (: job str)] :post [(: % (| SimProcess None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "AwaitProcessStarted の待つ相手(job の最初の process)が起きていればその記録を、まだなら None を返すため。"
  (next (gfor r log :if (= r.job job) r) None))


(defk ended-answer [process]
  {:pre [(: process SimProcess)] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster" :role "judgment"}}
  "終わった process の記録を AwaitProcessEnded の答えにするため。"
  (ProcessEnded :job process.job :instance process.instance :worker process.worker))


(defk due-end-waiters [log waiters]
  {:pre [(: log tuple) (: waiters dict)] :post [(: % DueWaiters)] :tags {:context "doeff-cluster" :role "judgment"}}
  "process の終わりを記録に書いた直後に、待ち手のうち待つ相手の終わった物(起こす)と、まだ待つ物を分けるため。"
  (var remaining {})
  (var due #())
  (for [#(job promises) (.items waiters)]
    (<- ended (| SimProcess None) (ended-process log job))
    (if (is ended None)
        (:= remaining (| remaining {job promises}))
        (do (<- answer ProcessEnded (ended-answer ended))
            (:= due (+ due (tuple (gfor p promises #(p answer))))))))
  (DueWaiters :remaining remaining :due due))


(defrecord ProcessEnds
  "process の終わりを世界に書いた後の、世界の欄の値と書いた後にする事(end-process の答え — 終わりを書く道 EndProcess・Crash・
   KillWorker が同じ記録を書くための 1 か所)。hosts・log・handles・children・finished・end-waiters = 世界の session の同名の欄の新しい値・
   stopped = 終わった process の中で Spawn した task(止める)・due = 起こす #(Promise 答え)・bells = 鳴らす宿の呼び鈴(周の間の待ちを起こす)。
   dict の欄は、世界の session がその形で持つ欄の値をそのまま運ぶ(形を変えるのはこの記録の役目の外)。"
  (#^ dict hosts)
  (#^ tuple log)
  (#^ dict handles)
  (#^ dict children)
  (#^ frozenset finished)
  (#^ dict end-waiters)
  (#^ tuple stopped)
  (#^ tuple due)
  (#^ tuple bells))


(defk end-process [ends worker pid ended now]
  {:pre [(: ends ProcessEnds) (: worker str) (: pid int) (: ended SimExit) (: now int)] :post [(: % ProcessEnds)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "process pid の終わり ended を刻 now で世界に書いた形を求めるため(process が自分で終わった時も、殺された時も同じ記録): 宿の観測の
   exit-code と task の結果・記録の終わり・把手と子の把手を外し・終わった印・待ち手の分け方。中で Spawn した task は止める物に、宿の
   呼び鈴は鳴らす物に足して手放す(#3871 の単位 5 — process の終わりは周の間の待ちを起こす出来事。本番の待つ子の終わりの代わり)。"
  (val truth (get ends.hosts worker))
  (val view (next (gfor p truth.processes :if (= p.pid pid) p) None))
  (val task-id (if (and (is-not view None) view.spec.once) (cut view.spec.name 5 None) None))
  (val hosts (| ends.hosts {worker (replace truth
                                            :processes (tuple (gfor p truth.processes (if (= p.pid pid) (replace p :exit-code ended.code) p)))
                                            :results (if (and task-id (is-not ended.result None))
                                                         (| truth.results {task-id ended.result})
                                                         truth.results)
                                            :wake-bell None)}))
  (val log (tuple (gfor r ends.log (if (= r.pid pid) (replace r :ended-ms now :exit-code ended.code :detail ended.detail :value ended.value) r))))
  (<- woken DueWaiters (due-end-waiters log ends.end-waiters))
  (ProcessEnds :hosts hosts :log log
               :handles (dfor #(k v) (.items ends.handles) :if (!= k pid) k v)
               :children (dfor #(k v) (.items ends.children) :if (!= k pid) k v)
               :finished (| ends.finished (frozenset [pid]))
               :end-waiters woken.remaining
               :stopped (+ ends.stopped (.get ends.children pid #()))
               :due (+ ends.due woken.due)
               :bells (if (is truth.wake-bell None) ends.bells (+ ends.bells #(truth.wake-bell)))))


(defk end-processes [ends victims ended now]
  {:pre [(: ends ProcessEnds) (: victims tuple) (: ended SimExit) (: now int)] :post [(: % ProcessEnds)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "殺した process の組(SimProcess の tuple)の終わりを、殺した刻 now と同じ終わり方 ended で順に書いた形を求めるため(Crash・KillWorker
   — 本物の process は殺された時点で終わる。巻き戻しの長さに依らない)。"
  (var after ends)
  (for [victim victims]
    (<- step ProcessEnds (end-process after victim.worker victim.pid ended now))
    (:= after step))
  after)


(defk settle-process-ends [ends killed]
  {:pre [(: ends ProcessEnds) (: killed bool)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "終わりを書いた後にする事をするため: process が終われば中で Spawn した task も止まり(本番は子 process ごと消える — 殺された
   process の task は巻き戻さずに捨てる(Discard)、自分で終わった process の task は取り消す)、終わりを待つ待ち手を起こし、宿の
   呼び鈴を鳴らす(周の間の待ちを起こす)。"
  (for [task ends.stopped]
    (<- (if killed (Discard task) (Cancel task))))
  (for [#(promise answer) ends.due]
    (<- (CompletePromise promise answer)))
  (for [bell ends.bells]
    (<- (CompletePromise bell None)))
  None)


(defk pause-taken [pausing kind]
  {:pre [(: pausing SimPauses) (: kind str)] :post [(: % (| SimPauses None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "筋書きが頼んだ coordinator の止まり kind が待っていれば、それを頼みの列から外して止まっている秒を覚えた後の止まりの値を、待って
   いなければ None を返すため(世界の PauseDue と PersistCrashDue が同じ判断を使う)。"
  (val due (next (gfor p pausing.queued :if (= (get p 0) kind) p) None))
  (if (is due None)
      None
      (SimPauses :queued (tuple (gfor p pausing.queued :if (is-not p due) p)) :downtime (get due 1))))


(defk next-world-due [intake hosts pausing now-ms]
  {:pre [(: intake SimIntake) (: hosts dict) (: pausing SimPauses) (: now-ms int)] :post [(: % (| int None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "世界の次の予定の刻を求めるため(問い NextWorldDue の答え・#3094): 筋書きが頼んだ coordinator の止まり・落ち(queued)か、効いた止まりの
   作り直しの秒(downtime — まだ取り出されていない)が残っていれば now-ms(次の歩・次の保存という出来事で起きる)。そうでなければ、網の
   切れ(cuts)・口の故障(failing)・worker の処理の止まり(hosts の stalled-until-ms)が明ける刻と作り直しの刻(restart-ms)のうち now-ms より
   後の最も早い刻。過ぎた刻は数えない(cuts と failing は明けても表に残る)。何も無ければ None。"
  (if (or pausing.queued (is-not pausing.downtime None))
      now-ms
      (do (val ends (+ (list (.values intake.cuts))
                       (lfor fault (.values intake.failing) (get fault 1))
                       (lfor truth (.values hosts) truth.stalled-until-ms)
                       (if (is pausing.restart-ms None) [] [pausing.restart-ms])))
          (val ahead (lfor end ends :if (> end now-ms) end))
          (if ahead (min ahead) None))))


;; --- 世界 -----------------------------------------------------------------------------------------------

(defhandler sim-world [#^ SimPlan plan]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 筋(系・worker・environ の上書き)は検ごとに違う値で、世界の外側に Ask へ答える物を置くと検の筋書きからも読めて
  ;; しまう(世界の答えは sim の中の仕組みだけが読む)。
  ;; sim の世界 1 つ(coordinator の部品と一生・worker の宿の真実と世代・process の把手と記録・process の中の task・届いた報告・準備・
  ;; 網の切断・止まれの合図)を session に持ち、仕組みの effect と検の effect に答える。session の値の置き場(doeff_core_effects の
  ;; state)はこの handler の外側に要る(sim-cluster が置く)。真実(本当に動いている process・届いた報告)はここが持つ — coordinator の
  ;; 信念(状態の中の報告)ではない。節の session の書きは scheduler の切り替わる effect より前に済ませる(頭の註)。
  (session val parts !(parts-of plan))
  (session var hosts !(fresh-hosts plan))
  (session var generations (dfor w plan.workers w.name 1))
  ;; worker の名 → 今の値(ReplaceWorker で差し替える — keeper は世代ごとにここから読む・#3366)。
  (session var current (dfor w plan.workers w.name w))
  (session var handles {})
  (session var children {})
  (session var finished (frozenset))
  (session var kills {})
  (session var next-pid 0)
  (session var log #())
  (session var preparations #())
  (session var stopping False)
  (session var stop-waiters {})
  ;; process ごとの止めの合図の受け手(pid → SimStopBox — 止めの問いか待ちを受けた process だけ・#3145)。
  (session var stop-boxes {})
  ;; process ごとの退きの知らせの受け手(pid → SimNoticeBox — 知らせを受けたか待ちを受けた process だけ・#3672)。
  (session var notice-boxes {})
  (session var revivals {})
  ;; 要求の受付まわり(網の切れ・口の故障・返事の前の覚え・報告・区間の歩の書きの数)は 1 つの値(1 歩の問いが 1 度だけ読む・#3054 の C-6)。
  (session var intake (SimIntake :cuts {} :failing {} :held #() :reports #()))
  (session var pausing (SimPauses :queued #()))
  (session var runs #())
  ;; coordinator の Pod の環境変数(ReplaceCoordinatorEnviron で差し替える — 一生の始めに CoordinatorEnvironOf で読む・#3772)。
  (session var coordinator-environ #())
  ;; coordinator の歩の記録(CoordinatorStep の tuple・刻の順 — AdmitBatch が運ぶ・検が CoordinatorSteps で読む・#2670 の根 B)。
  (session var steps-seen #())
  (session var end-waiters {})
  (session var watch-failures #())
  (session var start-waiters {})
  (session var ready-waiters #())
  (session var next-waiters #())
  ;; 行き止まりの見張りの材料(#3078): 出来事を待っている業務の task(BusinessWait の組)・筋書きの本体の期限なしの待ち・見張りの呼び鈴。
  (session var event-waits #())
  (session var scenario-waiting False)
  (session var change-bells #())
  (PlanOf []
    (resume plan))
  (NoteEventWait [pid events waiting]
    (val job (next (gfor r log :if (= r.pid pid) r.job) "?"))
    (<- kept tuple (without-one-wait event-waits pid events))
    (:= event-waits (if waiting (+ event-waits #((BusinessWait :pid pid :job job :events events))) kept))
    (val rung change-bells)
    (:= change-bells #())
    (for [bell rung]
      (<- (CompletePromise bell None)))
    (resume None))
  (NoteScenarioWait [waiting]
    (:= scenario-waiting waiting)
    (val rung change-bells)
    (:= change-bells #())
    (for [bell rung]
      (<- (CompletePromise bell None)))
    (resume None))
  (ArmWaitChange []
    (<- bell Promise (CreatePromise))
    (:= change-bells (+ change-bells #(bell)))
    (resume bell))
  (WaitsOf []
    (<- live tuple (live-processes handles children log))
    (resume (WaitsSeen :live live :waits (tuple (gfor w event-waits :if (in w.pid handles) w)) :scenario-waiting scenario-waiting)))
  (PartsOf []
    (resume parts))
  (HostTruthOf [name]
    (resume (get hosts name)))
  (PutHostTruth [name truth]
    ;; 呼び鈴は書き手の写しではなく今の物を残す(書き手が読んだ後に世界が鳴らして手放した鈴を置き戻さない)。
    (val before (get hosts name))
    (:= hosts (| hosts {name (replace truth :tick-bell before.tick-bell :wake-bell before.wake-bell :stop-bell before.stop-bell)}))
    (resume None))
  (ChangeHostTruth [name boot live change]
    (val truth (get hosts name))
    (if (or (!= truth.boot boot) (and live truth.down))
        (resume (HostTruthChange :before truth :after None))
        ;; change は宿の真実 → 宿の真実の純粋な関数か、宿の真実を組む defk(呼んだ結果は Program — 綴りの関数が defk の時)。Program は
        ;; 待たない物に限る(読みと書きの間に他の task が宿の真実を書かないため)。Program なら節の中で走らせ、読みと書きを世界への問い
        ;; 1 つのままにする(#2668 の L1218 の不変条件 — 検 test_sim_world_questions)。
        (do (val produced (change truth))
            (var changed produced)
            (when (isinstance produced Program)
              (<- ran HostTruth produced)
              (:= changed ran))
            (:= hosts (| hosts {name changed}))
            (resume (HostTruthChange :before truth :after changed)))))
  (NextPid []
    (:= next-pid (+ next-pid 1))
    (resume next-pid))
  (KeepHandle [pid task]
    ;; 把手を覚える前に殺された process(StartJob の Spawn と KeepHandle の間)は、殺した側が終わりを書き済み — 把手は覚えず、その場で
    ;; 捨てる(Discard — 殺された後に走らせない)。覚えた時は、その process の記録に根の task の id を書く(呼び手が job ごとの task の
    ;; 木を task ごとの積算の表から引くため・#4194)。
    (if (in pid kills)
        (<- (Discard task))
        (do (:= handles (| handles {pid task}))
            (:= log (tuple (gfor r log (if (= r.pid pid) (replace r :root-task task.task-id) r))))))
    (resume None))
  (HandleOf [pid]
    (resume (.get handles pid)))
  (ProcessStopAsked [pid]
    ;; 止めの問いを受けた process に受け手を据える(据えた後の TERM は止めの合図になる)。
    (val box (.get stop-boxes pid (SimStopBox)))
    (:= stop-boxes (| stop-boxes {pid box}))
    (resume box.reason))
  (ProcessStopWait [pid bridge]
    ;; 止めの待ちの Promise を受け手に掛ける(理由が立っていれば、その場で満たす)。外の待ちを写す task は process で 1 つ。
    (<- promise Promise (CreatePromise))
    (val box (.get stop-boxes pid (SimStopBox)))
    (val pending (is box.reason None))
    (val bridging (and bridge pending (not box.bridged)))
    (:= stop-boxes (| stop-boxes {pid (if pending
                                          (replace box :waiters (+ box.waiters #(promise)) :bridged (or box.bridged bridging))
                                          box)}))
    (when (not pending)
      (<- (CompletePromise promise box.reason)))
    (resume (SimStopWait :promise promise :bridge bridging)))
  (ProcessStopRaised [pid reason]
    ;; 受け手を据えた process にだけ届く(最初の理由を残す)。書きを済ませてから待ちを起こす。
    (val box (.get stop-boxes pid None))
    (val raising (and (is-not box None) (is box.reason None)))
    (val waking (if raising box.waiters #()))
    (when raising
      (:= stop-boxes (| stop-boxes {pid (replace box :reason reason :waiters #())})))
    (for [promise waking]
      (<- (CompletePromise promise reason)))
    (resume (is-not box None)))
  (ProcessNoticeWait [pid after]
    ;; 退きの知らせの待ちの Promise を受け手に掛ける(今の知らせが after と違えば、その場で満たす — #3672)。
    (<- promise Promise (CreatePromise))
    (val box (.get notice-boxes pid (SimNoticeBox)))
    (val ready (and (is-not box.notice None) (!= box.notice after)))
    (:= notice-boxes (| notice-boxes {pid (if ready
                                              box
                                              (replace box :waiters (+ box.waiters #((SimNoticeWaiter :after after :promise promise)))))}))
    (when ready
      (<- (CompletePromise promise box.notice)))
    (resume promise))
  (ProcessNoticeRaised [pid notice]
    ;; 知らせを置き換え、after が新しい知らせと違う待ちを起こす(同じ待ちは次の知らせまで残す)。書きを済ませてから待ちを起こす。
    (val box (.get notice-boxes pid (SimNoticeBox)))
    (val waking (tuple (gfor w box.waiters :if (!= w.after notice) w.promise)))
    (:= notice-boxes (| notice-boxes {pid (replace box :notice notice :waiters (tuple (gfor w box.waiters :if (= w.after notice) w)))}))
    (for [promise waking]
      (<- (CompletePromise promise notice)))
    (resume None))
  (KeepChild [pid task]
    ;; 終わった process の中で把手を覚える前だった task(Spawn した task が先に走り、KeepChild の前に process が終わった)は、その場で
    ;; 止める — 殺された process なら捨てる(Discard — 殺された後に走らせない)、自分で終わった process なら取り消す。
    (cond
      (in pid kills) (<- (Discard task))
      (in pid finished) (<- (Cancel task))
      True (:= children (| children {pid (+ (.get children pid #()) #(task))})))
    (resume None))
  (NoteProcess [process]
    (:= log (+ log #(process)))
    ;; その job の最初の process を待つ AwaitProcessStarted の待ち手を起こす(読み直さない — 書きで起こす)。
    (val starting (.get start-waiters process.job #()))
    (:= start-waiters (dfor #(k v) (.items start-waiters) :if (!= k process.job) k v))
    (<- first (| SimProcess None) (first-process log process.job))
    (for [promise starting]
      (<- (CompletePromise promise first)))
    ;; job の次の process を待つ AwaitJobProcess の待ち手のうち、この process の pid が excluding の外の物を起こす(書きで起こす・#3053)。
    (val woken (tuple (gfor w next-waiters :if (and (= w.job process.job) (not-in process.pid w.excluding)) w)))
    (:= next-waiters (tuple (gfor w next-waiters :if (not (and (= w.job process.job) (not-in process.pid w.excluding))) w)))
    (for [w woken]
      (<- (CompletePromise w.promise (JobProcessSeen :job process.job :pid process.pid))))
    (resume None))
  (NoteCoordinatorWrite [writes]
    ;; 書き終えた書きで終わった切り離した task の待ち手を起こす(送り手は読み直さずに待っている — proboscis/doeff#631)。
    (<- (ring-ended-tasks parts.queue writes))
    ;; coordinator が書き終えた時に、準備の状態を待つ待ち手を全部起こす(起きた待ち手が 1 回だけ読み直す・#3053)。
    (val rung ready-waiters)
    (:= ready-waiters #())
    ;; 業務の task が出来事を待っている間は、行き止まりの見張りも起こす(task の行が落ち着いたかを 1 回だけ読み直す・#3078)。
    (val watching (if (or event-waits scenario-waiting) change-bells #()))
    (when watching
      (:= change-bells #()))
    (for [bell rung]
      (<- (CompletePromise bell True)))
    (for [bell watching]
      (<- (CompletePromise bell None)))
    (resume None))
  (EndProcess [worker pid ended]
    ;; 終わりの記録の持ち主は 1 つ: 殺された process は捨てられて知らせを出さず、殺した側(Crash・KillWorker)が殺した刻で書く —
    ;; 書き済みの pid には書き直さない。
    (when (not-in pid finished)
      (<- now int (now-epoch-ms))
      (<- ends ProcessEnds (end-process (ProcessEnds :hosts hosts :log log :handles handles :children children :finished finished
                                                     :end-waiters end-waiters :stopped #() :due #() :bells #())
                                        worker pid ended now))
      (:= hosts ends.hosts)
      (:= log ends.log)
      (:= handles ends.handles)
      (:= children ends.children)
      (:= finished ends.finished)
      (:= end-waiters ends.end-waiters)
      (<- (settle-process-ends ends False)))
    ;; 業務の task が出来事を待っている間に process が終われば、行き止まりの見張りを起こす(生きている task の数が変わる・#3078)。
    (val watching (if (or event-waits scenario-waiting) change-bells #()))
    (when watching
      (:= change-bells #()))
    (for [bell watching]
      (<- (CompletePromise bell None)))
    (resume None))
  (NoteWatchFailure [failure]
    (:= watch-failures (+ watch-failures #(failure)))
    (val truth (get hosts failure.worker))
    ;; 待ちの task の止まりは、宿の heartbeat を周ごとへ戻す(待ちの口を使えない — 本番の待ちの thread の死と同じく宿を起こさない)。
    (when (= truth.boot failure.boot)
      (:= hosts (| hosts {failure.worker (replace truth :watch-failure failure.reason)})))
    (resume None))
  (WatchFailuresOf [name]
    (resume (tuple (gfor f watch-failures :if (= f.worker name) f))))
  (NotePreparation [preparation]
    (:= preparations (+ preparations #(preparation)))
    (resume None))
  (WorkersStopping []
    (resume stopping))
  (StopRequestOf [name]
    ;; 拍の止めの問いの材料(宿の真実と全 worker の止まれ)を 1 つの問いで(#3054 の C-6)。世代の確かめは宿の側(checked-truth)。
    (resume (HostStop :truth (get hosts name) :all-stopping stopping)))
  (StopWorkers []
    (val waiting (list (.values revivals)))
    ;; 止めの合図を待つ宿を全部起こす(本番の worker の Pod への SIGTERM — 止めの呼び鈴を鳴らして手放す)。
    (val asked (tuple (gfor truth (.values hosts) :if (is-not truth.stop-bell None) truth.stop-bell)))
    (:= hosts (dfor #(key truth) (.items hosts) key (replace truth :stop-bell None)))
    (:= stopping True)
    (:= revivals {})
    (for [promise waiting]
      (<- (CompletePromise promise None)))
    (for [bell asked]
      (<- (CompletePromise bell None)))
    (resume None))
  (WorkerEnded [name boot]
    (val truth (get hosts name))
    (val waiting (.get stop-waiters name #()))
    (when (= truth.boot boot)
      (:= hosts (| hosts {name (replace truth :down True :stopping False)})))
    (:= stop-waiters (dfor #(k v) (.items stop-waiters) :if (!= k name) k v))
    (for [promise waiting]
      (<- (CompletePromise promise None)))
    (resume None))
  (RevivalOf [name]
    ;; 全 worker が止まる時は待たせない(完了させる者が居ない)。
    (if (and (. (get hosts name) down) (not stopping))
        (do (<- promise Promise (CreatePromise))
            (:= revivals (| revivals {name promise}))
            (resume promise))
        (resume None)))
  (AdmitBatch [batch]
    ;; 篩い(網の切れ・口の故障・刻)と、残した要求の service の報告の記録と、返事の前に落ちた時の覚えを 1 つの問いで(#3054 の C-6)—
    ;; 読む世界の値は受付の 1 つ(intake)。何も取らなかった歩は書かない。
    (<- now int (now-epoch-ms))
    (val faults (RouteFaults :cut (frozenset (gfor #(name until) (.items intake.cuts) :if (> until now) name))
                             :failing (dfor #(route #(status until)) (.items intake.failing) :if (> until now) route status)
                             :now-ms now))
    (val kept (tuple (gfor r batch :if (and (not-in r.peer faults.cut) (not-in #(r.method r.path) faults.failing)) r)))
    (<- found tuple (reports-in (list kept) now))
    (when (or kept found)
      (:= intake (replace intake :held (+ intake.held kept) :reports (+ intake.reports found))))
    (resume (Admission :faults faults :kept kept)))
  (ReleaseRequest [request]
    (:= intake (replace intake :held (tuple (gfor r intake.held :if (is-not r request) r))))
    (resume None))
  (TakeHeldRequests []
    (val taken intake.held)
    (:= intake (replace intake :held #()))
    (resume taken))
  (PauseDue [kind]
    (<- after (| SimPauses None) (pause-taken pausing kind))
    (if (is after None)
        (resume False)
        (do (:= pausing after)
            ;; 落ちを待つ注入が残っていなければ、書きの前の落ちの問いをやめる(CrashCoordinator・#3132)。
            (setattr parts.queue "crash_waiting" (any (gfor p after.queued (= (get p 0) PAUSE-CRASH))))
            (resume True))))
  (StepEnded [released writes]
    ;; 調停ループの 1 歩の終わりの問い 1 つ(#2670 の根 A): 返事を済ませた要求を覚えから外し(ReleaseRequest と同じ)、この歩の書きを
    ;; 書きの順に知らせ(NoteCoordinatorWrite と同じ — 準備の状態の待ち手は最初の書きの知らせで起こす)、止まりを判じる(PauseDue と同じ)。
    ;; 書いた歩は、刻と書きの列を歩の記録に控える(CoordinatorSteps — #2670 の根 B・#3865)。
    (when writes
      (<- at int (now-epoch-ms))
      (:= steps-seen (+ steps-seen #((CoordinatorStep :at at :writes writes)))))
    (when released
      (:= intake (replace intake :held (tuple (gfor r intake.held :if (not (any (gfor done released (is done r)))) r)))))
    (for [#(index done) (enumerate writes)]
      (<- (ring-ended-tasks parts.queue done))
      (when (= index 0)
        (val rung ready-waiters)
        (:= ready-waiters #())
        (for [bell rung]
          (<- (CompletePromise bell True)))))
    (<- after (| SimPauses None) (pause-taken pausing PAUSE-STOP))
    (if (is after None)
        (resume False)
        (do (:= pausing after)
            ;; 落ちを待つ注入が残っていなければ、書きの前の落ちの問いをやめる(CrashCoordinator・#3132)。
            (setattr parts.queue "crash_waiting" (any (gfor p after.queued (= (get p 0) PAUSE-CRASH))))
            (resume True))))
  (PersistCrashDue []
    ;; 筋書きの落ちの頼みを PauseDue と同じく判じる(区間の歩の書きを落とさない判断は observe-requests の帳面 — #3132)。
    (<- after (| SimPauses None) (pause-taken pausing PAUSE-CRASH))
    (if (is after None)
        (resume False)
        (do (:= pausing after)
            (setattr parts.queue "crash_waiting" (any (gfor p after.queued (= (get p 0) PAUSE-CRASH))))
            (resume True))))
  (DowntimeOf []
    (val taken pausing.downtime)
    ;; 作り直す刻を覚える(coordinator の Pod の代役は取り出した秒だけ Delay で眠ってから作り直す — 次の予定の刻の問いが読む・#3094)。
    (<- now int (now-epoch-ms))
    (:= pausing (replace pausing :downtime None :restart-ms (if (is taken None) None (+ now (int (* 1000 taken))))))
    (resume taken))
  (NextWorldDue [now-ms]
    (<- due (| int None) (next-world-due intake hosts pausing now-ms))
    (resume due))
  (CoordinatorStarted [ms]
    (:= runs (+ runs #((SimCoordinatorRun :started-ms ms))))
    (:= pausing (replace pausing :restart-ms None))
    (resume None))
  (CoordinatorEnded [ms outcome]
    (:= runs (tuple (gfor #(i run) (enumerate runs) (if (= i (- (len runs) 1)) (replace run :ended-ms ms :outcome outcome) run))))
    (resume None))
  ;; --- 検の effect ---
  (Crash [name]
    (<- now int (now-epoch-ms))
    (val victims (tuple (gfor r log :if (and (= r.job name) (is r.ended-ms None) (in r.pid handles)) r)))
    (val crashed (SimExit :code 1 :result None :detail "Crash"))
    (val mains (tuple (gfor r victims (get handles r.pid))))
    (:= kills (| kills (dfor r victims r.pid crashed)))
    ;; 終わりは殺した刻で書く(end-processes — 殺された process は捨てられ、自分の終わりを書かない)。
    (<- ends ProcessEnds (end-processes (ProcessEnds :hosts hosts :log log :handles handles :children children :finished finished
                                                     :end-waiters end-waiters :stopped #() :due #() :bells #())
                                        victims crashed now))
    (:= hosts ends.hosts)
    (:= log ends.log)
    (:= handles ends.handles)
    (:= children ends.children)
    (:= finished ends.finished)
    (:= end-waiters ends.end-waiters)
    ;; 殺された process は本物の SIGKILL と同じく巻き戻さずに捨てる(Discard — finally の effect は走らず、世界に何も届かない)。
    (for [task mains]
      (<- (Discard task)))
    (<- (settle-process-ends ends True))
    (resume (len victims)))
  (KillWorker [name]
    (<- now int (now-epoch-ms))
    (val truth (get hosts name))
    (val victims (if truth.down #() (tuple (gfor r log :if (and (= r.worker name) (is r.ended-ms None)) r))))
    (val killed (SimExit :code KILLED-CODE :result None :detail "worker が死んだ(node ごと止まった)"))
    (val mains (tuple (gfor r victims :if (in r.pid handles) (get handles r.pid))))
    (:= kills (| kills (dfor r victims r.pid killed)))
    ;; 周の間を待つ宿を起こす(死んだ世代の run-worker は次の世界への問いで WorkerDied になって終わる)。
    (val resting truth.wake-bell)
    (:= hosts (| hosts {name (replace truth :down True :wake-bell None)}))
    ;; 終わりは殺した刻で書く(end-processes — 走り出す前に殺された process も、動いているように見せない)。把手がまだ無い process
    ;; (StartJob の Spawn と KeepHandle の間)は KeepHandle がその場で止める。
    (<- ends ProcessEnds (end-processes (ProcessEnds :hosts hosts :log log :handles handles :children children :finished finished
                                                     :end-waiters end-waiters :stopped #() :due #() :bells #())
                                        victims killed now))
    (:= hosts ends.hosts)
    (:= log ends.log)
    (:= handles ends.handles)
    (:= children ends.children)
    (:= finished ends.finished)
    (:= end-waiters ends.end-waiters)
    ;; 殺された process は本物の SIGKILL と同じく巻き戻さずに捨てる(Discard — finally の effect は走らず、世界に何も届かない)。
    (for [task mains]
      (<- (Discard task)))
    (<- (settle-process-ends ends True))
    (when (is-not resting None)
      (<- (CompletePromise resting None)))
    (resume (len victims)))
  (StopWorker [name]
    (val truth (get hosts name))
    (if truth.down
        (resume None)
        (do (<- promise Promise (CreatePromise))
            ;; 止めの合図を待つ宿を起こす(本番の worker の Pod への SIGTERM — 止めの呼び鈴を鳴らして手放す)。
            (val asked truth.stop-bell)
            (:= hosts (| hosts {name (replace truth :stopping True :stop-bell None)}))
            (:= stop-waiters (| stop-waiters {name (+ (.get stop-waiters name #()) #(promise))}))
            (when (is-not asked None)
              (<- (CompletePromise asked None)))
            (<- (Wait promise.future))
            (resume None))))
  (StartWorker [name]
    (val truth (get hosts name))
    (if (not truth.down)
        (resume False)
        (do (<- now int (now-epoch-ms))
            (val generation (+ (get generations name) 1))
            (<- fresh HostTruth (fresh-truth name generation now plan.timing))
            (val revival (.get revivals name))
            (:= generations (| generations {name generation}))
            (:= hosts (| hosts {name fresh}))
            (:= revivals (dfor #(k v) (.items revivals) :if (!= k name) k v))
            (when (is-not revival None)
              (<- (CompletePromise revival None)))
            (resume True))))
  (ReplaceWorker [name worker]
    ;; 止まっている間だけ値を差し替える(Recreate の作り直しの間)— 次の StartWorker の世代から keeper がこの値で起こす。
    (if (and (in name current) (= worker.name name) (. (get hosts name) down))
        (do (:= current (| current {name worker}))
            (resume True))
        (resume False)))
  (WorkerOf [name]
    (resume (get current name)))
  (CutWorker [name seconds]
    (<- now int (now-epoch-ms))
    (:= intake (replace intake :cuts (| intake.cuts {name (+ now (int (* 1000 seconds)))})))
    ;; 宿は起こさない(本番の網の切れと同じく、次の heartbeat の期限に送って届かないと知る)。
    (resume None))
  (StallWorker [name seconds]
    (<- now int (now-epoch-ms))
    (val truth (get hosts name))
    ;; 宿は起こさない(処理の止まりの間の heartbeat は送らない — 止まりの明けは宿の期限 host-wakes が持つ・#2850)。
    (:= hosts (| hosts {name (replace truth :stalled-until-ms (+ now (int (* 1000 seconds))))}))
    (resume None))
  (FailRoute [method path status seconds]
    (<- now int (now-epoch-ms))
    (:= intake (replace intake :failing (| intake.failing {#(method path) #(status (+ now (int (* 1000 seconds))))})))
    ;; 宿は起こさない(本番の口の故障と同じく、次の送りで知る)。
    (resume None))
  (DrainWorker [name ttl-seconds]
    (val request (drain-request name ttl-seconds (. (get hosts name) boot)))
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- answer tuple (send-shaped link request))
    (resume (if (is (get answer 0) None)
                {"error" (unreached-reason answer)}
                {"status" (get answer 0) "body" (get answer 1)})))
  (PreparationsOf [name]
    (resume (tuple (gfor p preparations :if (= p.worker name) p))))
  (StopCoordinator [seconds]
    (:= pausing (replace pausing :queued (+ pausing.queued #(#(PAUSE-STOP (float seconds))))))
    ;; 待っている coordinator を起こす(本番の停止の合図が受付の箱を起こすのと同じ — nudge-takers・#3865)。
    (<- (nudge-takers parts.queue))
    (resume None))
  (CrashCoordinator [seconds]
    (:= pausing (replace pausing :queued (+ pausing.queued #(#(PAUSE-CRASH (float seconds))))))
    ;; 落ちるのは次の Persist(注入の後の最初の書き)。待っている coordinator を起こす(その歩が生存の印を書けば、そこで落ちる)。
    ;; 欄の名は Hy が読む名(queue.crash-waiting = crash_waiting)で書く — 文字列の "crash-waiting" は別の属性を作り、印が立たない(#3132)。
    (setattr parts.queue "crash_waiting" True)
    (<- (nudge-takers parts.queue))
    (resume None))
  (CoordinatorRuns []
    (resume runs))
  (ReplaceCoordinatorEnviron [environ]
    (:= coordinator-environ environ)
    (resume None))
  (CoordinatorEnvironOf []
    (resume coordinator-environ))
  (CoordinatorSteps []
    (resume steps-seen))
  (ClientLink []
    (resume (SimLink :queue parts.queue :actor CLIENT-NAME :revision plan.revision :peer CLIENT-NAME :versions plan.versions :timing plan.timing)))
  (Redeclare [system environ]
    ;; その宣言し直しの上書き(渡されなければ最初の宣言の上書き)を新しい系に対して検めて重ねる(#3131 — 本番の declare と同じ 1 つの規則)。
    ;; 台数は本番の declare と同じく系の値の各 job の :replicas が行に載って書かれる(0 = 取り下げ — #3487)。
    (<- declaration Declaration (declaration-of system plan.revision (if (is environ None) plan.environ environ) plan.runtime-env plan.versions))
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- names tuple (apply-declaration link declaration))
    (resume names))
  (DeclareRollout [name spec]
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- answer tuple (send-request link "POST" "/resources/Rollout" {} {"name" name "spec" (deepcopy spec)}))
    (resume (answered-body answer (+ "Rollout を作れない: " name))))
  (KubeCalls []
    (resume (deepcopy (tuple parts.kube.calls))))
  (KubeReads []
    (resume parts.kube.reads))
  (SettleDeployment [namespace name ready]
    (.settle parts.kube (+ namespace "/" name) ready)
    ;; 本番の見張りは Deployment の変化の刻に受付の箱を起こす(#3868)。待っている coordinator を起こし、変化を伝えさせる(歩の途中なら、
    ;; 次の取りで kube-memory が伝えていない変化を見て待たずに返る)。
    (<- (nudge-takers parts.queue))
    (resume None))
  (NodeReads []
    (resume parts.kube.node-reads))
  (RelabelNode [name labels]
    (.relabel parts.kube name labels)
    ;; 本番の Node の見張りは label の変化の刻に受付の箱を起こす(#4070)。待っている coordinator を起こす(SettleDeployment と同じ)。
    (<- (nudge-takers parts.queue))
    (resume None))
  (ReportsOf [name]
    (resume (tuple (gfor r intake.reports :if (= r.job name) r))))
  (ProcessesOf [name]
    (resume (tuple (gfor r log :if (= r.job name) r))))
  (AwaitProcessStarted [name]
    (<- first (| SimProcess None) (first-process log name))
    (if (is-not first None)
        (resume first)
        (do (<- promise Promise (CreatePromise))
            (:= start-waiters (| start-waiters {name (+ (.get start-waiters name #()) #(promise))}))
            (<- started SimProcess (Wait promise.future))
            (resume started))))
  (AwaitProcessEnded [job timeout-seconds]
    ;; 待つ相手(job の今の最後の process — まだ無ければ最初に起きる process)が終わっていればすぐ答え、それ以外は Promise を掛けて、
    ;; 世界が process の終わりを書いた時(EndProcess・KillWorker)に起きる。読み直さない(proboscis/doeff#631)。
    (<- last-process (| SimProcess None) (ended-process log job))
    (cond
      (is-not last-process None)
        (do (<- ended-reply ProcessEnded (ended-answer last-process))
            (resume ended-reply))
      (and (is-not timeout-seconds None) (<= timeout-seconds 0))
        (resume (ProcessWaitExpired :job job :waited-seconds 0.0))
      True
        (do (<- promise Promise (CreatePromise))
            (:= end-waiters (| end-waiters {job (+ (.get end-waiters job #()) #(promise))}))
            ;; 上限なしの待ちは時間切れの答えを持たない(ProcessWaitExpired の秒は期限つきの待ちにだけ在る)。
            (if (is timeout-seconds None)
                (do (<- ended-later ProcessEnded (Wait promise.future))
                    (resume ended-later))
                (do (<- answer (promise-or-timeout promise.future timeout-seconds))
                    (resume (if (is answer None) (ProcessWaitExpired :job job :waited-seconds (float timeout-seconds)) answer)))))))
  (ReadinessOf [name]
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- answer tuple (service-answer-of link name))
    (<- readiness ServiceReadiness (readiness-of-body (get answer 0) (get answer 1)))
    (resume readiness))
  (AwaitReadiness [name state timeout-seconds]
    ;; 読む前に呼び鈴を掛け(読みと次の書きの間の鳴らしを取りこぼさない)、答えが出なければ coordinator の次の書き(NoteCoordinatorWrite)
    ;; か期限で起きて 1 回だけ読み直す。時計の刻みでは読み直さない(#3053)。答えの判断(届いた・落ちた・期限)は本番と同じ readiness-wait-answer。
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- started int (now-epoch-ms))
    (var answer None)
    (while (is answer None)
      (<- bell Promise (CreatePromise))
      (:= ready-waiters (+ ready-waiters #(bell)))
      (<- read tuple (service-answer-of link name))
      (<- now int (now-epoch-ms))
      (val waited (/ (- now started) 1000.0))
      (<- step (| ServiceReadiness ServiceFailed ReadinessWaitExpired None)
          (readiness-wait-answer name state (get read 0) (get read 1) waited (float timeout-seconds)))
      (if (is-not step None)
          (:= answer step)
          (<- (promise-or-timeout bell.future (- timeout-seconds waited))))
      ;; 鳴らなかった呼び鈴を外す(期限で起きた Promise を後の書きで 2 度満たさない — 鳴った物は鳴らした側が外している)。
      (:= ready-waiters (tuple (gfor b ready-waiters :if (is-not b bell) b))))
    (resume answer))
  (AwaitJobProcess [job excluding timeout-seconds]
    ;; 待つ相手が起きていればすぐ答え、それ以外は Promise を掛けて、世界が process を記録した時(NoteProcess)に起きる。読み直さない(#3053)。
    (<- seen (| SimProcess None) (job-process-outside log job excluding))
    (cond
      (is-not seen None)
        (resume (JobProcessSeen :job job :pid seen.pid))
      (<= timeout-seconds 0)
        (resume (JobProcessWaitExpired :job job :excluding excluding :waited-seconds 0.0))
      True
        (do (<- promise Promise (CreatePromise))
            (:= next-waiters (+ next-waiters #((NextWaiter :job job :excluding excluding :promise promise))))
            (<- answer (promise-or-timeout promise.future timeout-seconds))
            ;; 期限で起きた待ち手を外す(後の記録で 2 度満たさない — 起こした待ち手は起こした側が外している)。
            (:= next-waiters (tuple (gfor w next-waiters :if (is-not w.promise promise) w)))
            (resume (if (is answer None)
                        (JobProcessWaitExpired :job job :excluding excluding :waited-seconds (float timeout-seconds))
                        answer)))))
  (SharedRows [prefix]
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- answer tuple (send-shaped link (board-read-request prefix)))
    (resume (answered-body answer "盤を読めない")))
  (ReadCoordinator [path]
    (<- link SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
    (<- answer tuple (send-request link "GET" path {} None))
    (resume (answered-body answer (+ "GET " path)))))


;; --- 行き止まりの見張り(#3078 — 業務の task の待ちだけを見る・時計の刻みでは起きない)--------------------------------
;; 今の scheduler の行き止まりの判定(SchedulerDeadlockError — 走れる task も外の約束の待ちも無い時)は sim-cluster では起きない: worker の
;; 拍の timer がいつも時計の列に在り、列が空にならない。なので業務の task の出来事の待ち(WaitForEvent・WaitForEvents)だけを数え、それを起こす物
;; (筋書きの本体・coordinator の置き直し・業務の timer・sim の世界の予定)が無い時にその場で SimDeadlockError で終わらせる。

(defk event-names [event-types]
  {:pre [(: event-types tuple)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "WaitForEvent・WaitForEvents の型の組を、名を , で繋いだ文にするため(行き止まりの知らせの名指し)。"
  (.join "," (gfor t event-types t.__name__)))


(defk without-one-wait [waits pid events]
  {:pre [(: waits tuple) (: pid int) (: events str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "出来事の待ちの組から、process pid の events の待ち 1 つを外すため(同じ process の同じ型の待ちが 2 つ在れば 1 つだけ外す)。"
  (var kept [])
  (var dropped False)
  (for [w waits]
    (if (and (not dropped) (= w.pid pid) (= w.events events))
        (:= dropped True)
        (:= kept (+ kept [w]))))
  (tuple kept))


(defk live-processes [handles children log]
  {:pre [(: handles dict) (: children dict) (: log tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "生きている業務の process(把手の在る pid)ごとに、job の名と task の数(主の task 1 + 中で Spawn した task の数)を読むため。"
  (tuple (gfor r log :if (in r.pid handles)
               (LiveProcess :pid r.pid :job r.job :tasks (+ 1 (len (.get children r.pid #())))))))


(defk deadlock-of [snapshot]
  {:pre [(: snapshot WaitSnapshot)] :post [(: % (| SimDeadlock None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "行き止まりかを判じるため: 生きている業務の process が 1 つ以上在り、どの process も task の数だけ出来事を待ち、筋書きの本体も期限なしで
   待ち、coordinator の task の行が落ち着き、業務の timer も sim の世界の予定の刻も無い時だけ SimDeadlock(待っている task の全部)。"
  (val all-waiting (and (bool snapshot.live)
                        (all (gfor p snapshot.live
                                   (>= (sum (gfor w snapshot.waits :if (= w.pid p.pid) 1)) p.tasks)))))
  (if (and all-waiting snapshot.scenario-waiting snapshot.rows-settled (not snapshot.armed-timers) (is snapshot.world-due None))
      (SimDeadlock :waits snapshot.waits)
      None))


(defk waits-closed [seen]
  {:pre [(: seen WaitsSeen)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator を読む前の判じ: 業務の task が全部 出来事を待ち、筋書きの本体も期限なしで待っているか(開いていれば coordinator を
   読まない — 見張りが要求の数を増やさない)。"
  (<- found (| SimDeadlock None) (deadlock-of (WaitSnapshot :live seen.live :waits seen.waits :scenario-waiting seen.scenario-waiting
                                                            :rows-settled True)))
  (is-not found None))


(defk task-rows-settled [link]
  {:pre [(: link SimLink)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "coordinator の GET /state の task の行が全部 running か終わりの phase かを読むため(置き待ち・始まり待ち・置き直しの task が在れば偽 —
   置かれて起きる task が新しく出来事を発し得る。読めなければ偽 = 判じない)。"
  (<- read tuple (send-resent link "GET" "/state" {} None))
  (if (is (get read 0) None)
      False
      (all (gfor row (.get (answered-object read "状態を読めない") "tasks" [])
                 (or (= (.get row "phase") "running") (in (.get row "phase") ENDED-PHASES))))))


(defk deadlock-text [found]
  {:pre [(: found SimDeadlock)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "行き止まりの知らせの文を作るため(待っている task ごとに job・pid・出来事の型)。"
  (+ "sim-cluster の行き止まり — 業務の task が全部 出来事を待って止まり、起こす物が無い: "
     (.join "・" (gfor w found.waits (.format "{}(pid {})が {} を待つ" w.job w.pid w.events)))))


(defhandler business-wait-tap [#^ int pid]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 業務の process pid の出来事の待ち(WaitForEvent・WaitForEvents)の前後を世界に知らせる(行き止まりの見張りの材料)。待ちそのものは外側の出来事の
  ;; 答え手がする(答えはそのまま返す)。殺された process(Discard)の待ちは「出た」の知らせが戻らないが、世界は生きている process の
  ;; 待ちだけを数える。
  ;; 引数に残す理由: process ごとに別の pid で同じ handler を並べる(柵 fence と同じ — Ask では process を区別できない)。
  (WaitForEventEffect [event-types]
    (<- events str (event-names event-types))
    (<- (NoteEventWait pid events True))
    (<- event (WaitForEventEffect event-types))
    (<- (NoteEventWait pid events False))
    (resume event))
  (WaitForEventsEffect [event-types]
    (<- events str (event-names event-types))
    (<- (NoteEventWait pid events True))
    (<- came tuple (WaitForEventsEffect event-types))
    (<- (NoteEventWait pid events False))
    (resume came)))


(defhandler scenario-wait-tap
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 筋書きの本体の期限なしの待ち(出来事の待ち・job の process の終わりの期限なしの待ち)の前後を世界に知らせる。期限つきの待ちは期限で
  ;; 起きるので知らせない(行き止まりにしない)。
  (WaitForEventEffect [event-types]
    (<- (NoteScenarioWait True))
    (<- event (WaitForEventEffect event-types))
    (<- (NoteScenarioWait False))
    (resume event))
  (WaitForEventsEffect [event-types]
    (<- (NoteScenarioWait True))
    (<- came tuple (WaitForEventsEffect event-types))
    (<- (NoteScenarioWait False))
    (resume came))
  (AwaitProcessEnded [job timeout-seconds]
    :when (is timeout-seconds None)
    (<- (NoteScenarioWait True))
    (<- answer (| ProcessEnded ProcessWaitExpired) (AwaitProcessEnded job :timeout-seconds None))
    (<- (NoteScenarioWait False))
    (resume answer)))


(defk earliest-due [world-due armed]
  {:pre [(: world-due (| int None)) (: armed tuple)] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "見張りが次に読み直す刻を決めるため: sim の世界の次の予定の刻と、最も早い業務の timer の刻(armed は刻の早い順)の早い方。どちらも
   無ければ None(呼び鈴だけで起きる)。timer の発火は呼び鈴を鳴らさない — 誰も待っていない TimerFired は落ちるので、その刻に読み直す。"
  (val timer-due (if armed (epoch-ms-of (. (get armed 0) at)) None))
  (cond (is world-due None) timer-due
        (is timer-due None) world-due
        True (min world-due timer-due)))


(defhandler no-business-timers
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 業務の timer の答え手(doeff-events の timer-handler)を sim の外の世界(SimOutside.handlers)に置かない走りで、見張りの ArmedTimers
  ;; に「業務の timer は無い」と答える。外の世界の内側に置いた timer-handler が先に答えるので、置いた走りではこれに届かない。空と答えて
  ;; よい理由: timer-handler の無い走りでは ArmTimer 自身が答え手の無い effect で落ちるので、積まれた timer は在り得ない(推し量りの
  ;; 既定値ではなく、その走りの事実)。
  (ArmedTimersEffect []
    (resume #())))


(defk deadlock-watch [link]
  {:pre [(: link SimLink)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "行き止まりの見張り: 呼び鈴を先に掛けてから材料を読み(読みと次の変化の間の鳴らしを取りこぼさない)、行き止まりなら SimDeadlockError を
   上げ、そうでなければ呼び鈴が鳴るまで眠る。時計の刻みでは起きない — 起こすのは世界(業務の待ち・筋書きの待ちの変化と、業務の task が
   待っている間の process の終わり・coordinator の書き)と、待ちがそろっている間の sim の世界の次の予定の刻(NextWorldDue — 網の切れが
   明ける等。その刻に世界が変わるので 1 度だけ読み直す。予定の明けは呼び鈴を鳴らさない)と、最も早い業務の timer の刻(ArmedTimers —
   timer が残る間は行き止まりにしない。発火は呼び鈴を鳴らさないので、その刻に 1 度だけ読み直す・#3093)だけ。"
  (var due None)
  (while True
    (<- bell Promise (ArmWaitChange))
    (<- seen WaitsSeen (WaitsOf))
    (<- closed bool (waits-closed seen))
    (<- now int (now-epoch-ms))
    (:= due None)
    (when closed
      (<- settled bool (task-rows-settled link))
      (<- world-due (| int None) (NextWorldDue now))
      (<- armed tuple (ArmedTimers))
      (<- next-due (| int None) (earliest-due world-due armed))
      (:= due next-due)
      (<- found (| SimDeadlock None) (deadlock-of (WaitSnapshot :live seen.live :waits seen.waits :scenario-waiting seen.scenario-waiting
                                                                :rows-settled settled :armed-timers armed :world-due world-due)))
      (when (is-not found None)
        (val stamped (replace found :at-ms now))
        (<- text str (deadlock-text stamped))
        (raise (SimDeadlockError text stamped))))
    ;; 予定の刻が今と同じ(止まりの頼みが次の歩で起きる)でも、仮想の時計を少なくとも 1 ms 進めて他の task に歩を譲る。
    (if (is due None)
        (<- (Wait bell.future))
        (<- (promise-or-timeout bell.future (/ (max 1 (- due now)) 1000.0)))))
  None)


;; --- 比で延ばした世界の生死の見張り(#3865)--------------------------------------------------------------

(val GONE-READER "sim-gone-watch")

(defk gone-watch [broker]
  {:pre [(: broker MemoryBroker)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator と同じ知らせの broker の受け手として worker の死の判断(WorkerGone)を待ち、来たら SimLivenessError で終わらせるため
   (比で延ばした世界だけで走る — sim-main が筋書きと競わせる)。"
  (<- came WorkerGone (with-handlers [(subscribed-event-handler (EventBus) GONE-READER #(WorkerGone))
                                      (memory-notice-handler broker)
                                      (notice-events-handler GONE-READER WORKER-NOTICE-READS 3600.0)]
                        (WaitForEvent WorkerGone)))
  (raise (SimLivenessError (.format "比 {} で延ばした世界で worker {} の死の判断が出た — 生死を試す筋書きは :timing に本番の値を明示する"
                                    SIM-TIMING-RATIO came.worker)
                           came)))


;; --- 入口 -----------------------------------------------------------------------------------------------

(defk sim-main [scenario]
  {:pre [(: scenario (| Program EffectBase))] :post [(: % "scenario の答え(型は筋書きごと)")] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Pod を起こし、宣言を書き、worker を並べてから scenario を(筋書きの送り手の口の下で)走らせ、終われば worker・
   coordinator の順に止めるため。scenario は行き止まりの見張り(deadlock-watch)と競わせ、見張りが行き止まりを見つければ scenario を
   待たずに SimDeadlockError で終わる(#3078)。"
  (<- plan SimPlan (PlanOf))
  (<- parts SimParts (PartsOf))
  (<- pod Task (Spawn (coordinator-pod)))
  (var guards [])
  (when plan.watches-gone
    (<- gone Task (Spawn (gone-watch parts.broker)))
    (:= guards [gone]))
  (<- (await-coordinator parts.queue))
  (<- control SimLink (control-link parts.queue plan.revision plan.versions plan.timing))
  (<- (apply-declaration control plan.declaration))
  (var keepers [])
  (for [w plan.workers]
    (<- t Task (Spawn (worker-keeper w plan.policy)))
    (:= keepers (+ keepers [t])))
  (<- (await-workers (tuple (gfor w plan.workers w.name))))
  (<- client SimLink (ClientLink))
  (<- story Task (Spawn (with-handlers [(coordinator-answers client) scenario-wait-tap] scenario)))
  (<- watch Task (Spawn (deadlock-watch control)))
  (try
    (<- answer (Race story watch #* guards))
    answer
    (finally
      (for [guard guards]
        (<- (Cancel guard)))
      (<- (Cancel watch))
      (<- (Cancel story))
      (<- (StopWorkers))
      (<- (Gather #* keepers))
      (setattr parts.stop "requested" True)
      (<- (nudge-takers parts.queue))
      (<- (Wait pod)))))


(defk sim-under-clock [system scenario workers environ revision timing policy outside store [deployments None] [runtime-env None]
                       [nodes None] * notice-broker]
  {:pre [(: system System) (: scenario (| Program EffectBase)) (: workers (| tuple None)) (: environ (| dict None)) (: revision str)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None)) (: store (| Callable None))
         (: deployments (| dict None)) (: runtime-env (| RuntimeEnv None)) (: nodes (| dict None)) (: notice-broker MemoryBroker)]
   :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "program"}}
  "入口(sim-cluster・wall-sim-cluster)が選んだ時計の内側で、時計の今を起点に筋を作り(引数を検めて断る)、session の値の置き場・sim の
   外の世界・sim の世界を並べて sim-main を走らせるため。時計の違いは入口が並べる handler だけで、ここから内側は同じ。"
  (<- start-ms int (now-epoch-ms))
  (<- plan SimPlan (sim-plan system workers environ revision start-ms timing policy outside store deployments runtime-env nodes
                             :notice-broker notice-broker))
  ;; no-business-timers は外の世界の外側: 外の世界に timer-handler を置いた走りでは、それが先に ArmedTimers に答える(#3093)。
  (<- answer (with-handlers [(session-store) no-business-timers #* (if (is outside None) [] outside.handlers) (sim-world plan)]
               (sim-main scenario)))
  answer)


(defk sim-cluster [system scenario * [workers None] [environ None] [revision "sim"] [start-ms SIM-START-MS] [timing None] [policy None]
                  [outside None] [store None] [deployments None] [runtime-env None] [nodes None] notice-broker]
  {:tp [A]
   :pre [(: system System) (: scenario (| (of Program A object) (of EffectBase A))) (: workers (| (get tuple #(SimWorker ...)) None))
         (: environ (| (get dict #(str (get dict #(str str)))) None)) (: revision str) (: start-ms int)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None))
         (: store (| (get Callable #([] MemoryWalStore)) None)) (: deployments (| (get dict #(str (get dict #(str object)))) None))
         (: runtime-env (| RuntimeEnv None)) (: nodes (| (get dict #(str (get dict #(str str)))) None)) (: notice-broker MemoryBroker)]
   :post [(: % A)]
   :tags {:context "doeff-cluster" :role "entry"}}
  "系 system(sim の土台で作った System の値)を本物の coordinator と worker の上で走らせ、scenario(検の筋書きの Program — 同じ
   scheduler・同じ仮想の時計で並んで走る)の答えを返す。workers = SimWorker の tuple(既定 = 全 job の needs の和を提供する 1 台)・
   environ = job 名 → 宣言の :environ に重ねる環境変数(宣言に無い名は断る)・revision = 宣言の版・start-ms = 仮想の時計の起点・
   timing / policy = coordinator と worker の時間の設定(既定 = 本番の既定)・outside = sim の外の世界(SimOutside — 業務の外の系の模擬の
   handler と、それが答える effect の型。時計の内側・sim の世界の外側に置き、柵はその型も通す)・store = coordinator の置き場を作る
   関数(引数なし → MemoryWalStore の値 — 既定 = MemoryWalStore。反例の壊れた置き場 — 書いたふり・読み直せない・欄を落とす — を
   派生の class で渡す。1 回の走りに 1 回だけ呼び、作り直した coordinator も同じ置き場から読み直す)。自分で scheduler を持つ(外に
   scheduler が在っても無くても走る)。deployments = 「namespace/名」→ KubeMemory の観測の dict(specReplicas・replicas・readyReplicas 等)。
   走りごとに deepcopy を作り、初期値を変えない。既定は空。Pod の進行は SettleDeployment。nodes = node の名 → label の dict(k8s の Node の
   metadata.labels と同じ形 — worker の node の label から能力を導く検・#4070。deepcopy を作る・既定は空)。label の変化は RelabelNode。runtime-env = 宣言の実行環境の宣言(本番の
   declare の --runtime-env と同じ — 本物の worker が準備し、子の run-context の runtime-env になる。既定 None)。壁の時計で回すなら
   wall-sim-cluster。worker の代役(宿)は本番と同じ待ち(tick_pauses の await-wakes — 期限・呼び鈴・止めの合図の早い 1 つ)で周の間を
   待つ(#3871 の単位 5)。notice-broker = 知らせの broker
   (doeff-events の MemoryBroker — coordinator が worker の生死の出来事を出す先。呼び手が作って渡し、筋書きと呼び手の世界の job は同じ
   broker の受け手に成れる。既定は無い — sim は作らない・#3850)。"
  (<- answer (scheduled (with-handlers [(sim-time-handler :start-time (datetime-of-epoch-ms start-ms))]
                          (sim-under-clock system scenario workers environ revision timing policy outside store deployments runtime-env nodes
                                           :notice-broker notice-broker))))
  answer)


(defk wall-sim-cluster [system scenario * [workers None] [environ None] [revision "sim"] [timing None] [policy None] [outside None]
                       [store None] [deployments None] [runtime-env None] [nodes None] notice-broker]
  {:tp [A]
   :pre [(: system System) (: scenario (| (of Program A object) (of EffectBase A))) (: workers (| (get tuple #(SimWorker ...)) None))
         (: environ (| (get dict #(str (get dict #(str str)))) None)) (: revision str)
         (: timing (| ClusterTiming None)) (: policy (| WorkerPolicy None)) (: outside (| SimOutside None))
         (: store (| (get Callable #([] MemoryWalStore)) None)) (: deployments (| (get dict #(str (get dict #(str object)))) None))
         (: runtime-env (| RuntimeEnv None)) (: nodes (| (get dict #(str (get dict #(str str)))) None)) (: notice-broker MemoryBroker)]
   :post [(: % A)]
   :tags {:context "doeff-cluster" :role "entry"}}
  "sim-cluster と同じ系・同じ本物の coordinator と worker・同じ偽の宿と柵を、壁の時計で走らせ、scenario の答えを返す(引数の意味は
   sim-cluster と同じ — 起点は無く、今の時刻から始まる)。時計 = doeff-time の async-time-handler(Delay は実時間で待つ・GetTime は今の
   時刻)と、その待ちと Await に答える await-handler(process で共有の event loop — 外の thread と本物の socket の I/O もそこで走る)。
   筋書きは Await を出してよい(ここの await-handler が答える)。job の Await は柵を通らない(SIM-PASSABLE — 本番の子と同じく土台が
   await-handler を並べる)ので、本物の待ち受けを持つ job は土台に await-handler を置くか、その I/O を outside の handler に置く
   (時計の内側なので、ここの await-handler が答える)。自分で scheduler を持つ。"
  (<- answer (scheduled (with-handlers [(await-handler) (async-time-handler)]
                          (sim-under-clock system scenario workers environ revision timing policy outside store deployments runtime-env nodes
                                           :notice-broker notice-broker))))
  answer)
