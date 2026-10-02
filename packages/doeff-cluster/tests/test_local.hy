;; 手元の runner sim-cluster(doeff_cluster.sim.local — ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5)。
;;
;; sim の土台(tests.fixtures.envs の sim-foundation)で作った系の値を、本物の coordinator(emulated-handlers)と本物の run-worker(偽の宿)の
;; 上で、仮想の時計で走らせる。検の筋書き(scenario)は同じ scheduler・同じ時計で並んで走り、検の effect(Crash・Redeclare・ReportsOf・
;; ReadinessOf・ProcessesOf・SharedRows・ReadCoordinator・StopCoordinator・CrashCoordinator・CoordinatorRuns・KillWorker・StopWorker・
;; StartWorker・CutWorker・DrainWorker)で世界を動かし・読む。時間を進めるのは Delay。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import json)
(import pytest)
(import doeff [with-handlers])
(import doeff_time [Delay])
(import doeff_cluster.coordinator.entry.handler_sets [MemoryWalStore])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.intent.remote_model [UnsendableProgram TaskFailed])
(import doeff_cluster.shared.protocol.program_codec [decode-outcome])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.sim.local [sim-cluster sim-process SimChild SimLink EndProcess SimWorker SimProcess SimReport ServiceReadiness
                             SimCoordinatorRun Crash Redeclare ReportsOf ReadinessOf ProcessesOf SharedRows ReadCoordinator
                             StopCoordinator CrashCoordinator CoordinatorRuns KillWorker StopWorker StartWorker CutWorker DrainWorker])
(import doeff_cluster.shared.intent.cluster_control [AwaitReadiness ReadinessWaitExpired AwaitJobProcess JobProcessSeen
                                                     JobProcessWaitExpired])
(import doeff_cluster.shared.entry.service_build [job system-of])
(import doeff_cluster.shared.intent.service_model [System CallShape])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons beacons-v2 beacons-plus handoff-beacons handoff-beacons-v2 handoff-beacons-v3 relay flavors fenced gpu-only
                                    holding-unloadable Unloadable spawners quitters pulses detaching context-env-readers])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.coordinator.core.coordinator_invariants [acknowledged-writes-survive RevisionRead revision-never-goes-back
                                                                 acknowledged-values-survive declared-services-survive
                                                                 ServiceVersionRead service-versions-never-go-back
                                                                 WorkerProbe alive-only-while-reachable
                                                                 PlacementSeen WorkerGone places-only-on-reachable
                                                                 ProcessSpan WorkerCapacity running-within-capacity
                                                                 JobNeeds WorkerAbility placed-only-where-eligible
                                                                 JobProcess moves-to-a-live-worker
                                                                 WorkerExclusive exclusive-workers-take-only-their-jobs ran-only-where-eligible
                                                                 RunLimit runs-within-their-limit
                                                                 DrainWindow no-new-place-while-draining])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.worker.core.invariants [handoff-keeps-a-ready-writer])
(import tests.env_fixtures [LOCK env-of])


(defrecord Seen
  "筋書きが読んだ job 1 つの姿: coordinator の ready・届いた報告・process・盤の行。"
  (#^ ServiceReadiness readiness)
  (#^ tuple reports)
  (#^ tuple processes)
  (#^ dict rows))


(defk seen-of [name prefix]
  {:pre [(: name str) (: prefix str)] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの読み: job name の ready・報告・process と、盤の prefix の行を読む。"
  (<- readiness ServiceReadiness (ReadinessOf name))
  (<- reports tuple (ReportsOf name))
  (<- processes tuple (ProcessesOf name))
  (<- rows dict (SharedRows prefix))
  (Seen :readiness readiness :reports reports :processes processes :rows rows))


(defk watch-beacon [seconds]
  {:pre [(: seconds (| int float))] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: seconds 秒待ってから beacon を読む。"
  (<- (Delay (float seconds)))
  (<- seen Seen (seen-of "beacon" "beacon/"))
  seen)


(deftest test-a-service-runs-reports-ready-and-becomes-ready-within-its-window
  ;; service が起き、拍ごとに ReportReady を送り、coordinator が readiness の窓(5 秒)の中の報告で Ready と数える。
  (<- seen Seen (sim-cluster (beacons sim-foundation) (watch-beacon 12)))
  (assert (= seen.readiness.state "Ready") seen)
  (assert (= (len seen.processes) 1) seen.processes)
  (val first (get seen.processes 0))
  (assert (is first.exit-code None) seen.processes)
  ;; 報告は今動いている process の世代から届き、ready は真。
  (val readies (lfor r seen.reports :if (= r.kind "readiness") r))
  (assert (> (len readies) 3) seen.reports)
  (assert (all (gfor r readies (= r.instance first.instance))) seen.reports)
  (assert (all (gfor r readies r.ready)) seen.reports)
  ;; 計器の報告(ReportMetrics)も同じ世代から届く。
  (val metrics (lfor r seen.reports :if (= r.kind "metrics") r))
  (assert (and metrics (all (gfor r metrics (= r.instance first.instance)))) seen.reports)
  (assert (>= (get (. (get metrics -1) metrics) "counters" "beats") 3.0) metrics)
  ;; 宣言の :environ の STEP を宿が Ask に答えた(盤の行に載る)。
  (assert (= (get seen.rows "beacon/a" "step") "1") seen.rows))


(defrecord Changed
  "筋書きが世界を動かす前と後に読んだ job 1 つの姿: answer = 動かした effect の答え(Crash の数・Redeclare の名)。"
  (#^ (| int tuple) answer)
  (#^ Seen before)
  (#^ Seen after))


(defk crash-and-watch [name prefix]
  {:pre [(: name str) (: prefix str)] :post [(: % Changed)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って読み、job name の process を Crash で落とし、12 秒待ってもう一度読む。"
  (<- (Delay 8.0))
  (<- before Seen (seen-of name prefix))
  (<- n int (Crash name))
  (<- (Delay 12.0))
  (<- after Seen (seen-of name prefix))
  (Changed :answer n :before before :after after))


(defk redeclare-and-watch [system name prefix wait]
  {:pre [(: system System) (: name str) (: prefix str) (: wait float)] :post [(: % Changed)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って読み、系 system で宣言し直し、wait 秒待ってもう一度読む。"
  (<- (Delay 8.0))
  (<- before Seen (seen-of name prefix))
  (<- names tuple (Redeclare system))
  (<- (Delay wait))
  (<- after Seen (seen-of name prefix))
  (Changed :answer names :before before :after after))


(deftest test-a-crashed-service-is-restarted-by-the-real-worker-and-coordinator
  ;; Crash で落とした process(exit 1)を、本物の worker の判断が backoff の後に起こし直す(世代が増え、新しい世代が Ready に戻る)。
  (<- changed Changed (sim-cluster (beacons sim-foundation) (crash-and-watch "beacon" "beacon/")))
  (assert (= changed.answer 1) changed)
  (assert (= (len changed.before.processes) 1) changed.before.processes)
  (assert (= (len changed.after.processes) 2) changed.after.processes)
  (val old (get changed.after.processes 0))
  (val new (get changed.after.processes 1))
  (assert (= old.exit-code 1) old)
  (assert (is new.exit-code None) new)
  (assert (!= old.instance new.instance) changed.after.processes)
  (assert (> new.attempt old.attempt) changed.after.processes)
  (assert (>= new.started-ms old.ended-ms) changed.after.processes)
  (assert (= changed.after.readiness.state "Ready") changed.after.readiness)
  (assert (any (gfor r changed.after.reports (and (= r.instance new.instance) r.ready))) changed.after.reports))


(deftest test-redeclaring-a-recreate-service-stops-the-old-process-before-the-new-one-starts
  ;; 版(environ の STEP)を変えて宣言し直すと入れ替わる — recreate は旧を止めて(止めの合図 -15)から新を起こす。
  (<- changed Changed (sim-cluster (beacons sim-foundation) (redeclare-and-watch (beacons-v2 sim-foundation) "beacon" "beacon/" 12.0)))
  (assert (= changed.answer #("beacon")) changed.answer)
  (assert (= (get changed.before.rows "beacon/a" "step") "1") changed.before.rows)
  (assert (= (len changed.after.processes) 2) changed.after.processes)
  (val old (get changed.after.processes 0))
  (val new (get changed.after.processes 1))
  (assert (= old.exit-code -15) old)
  (assert (is new.exit-code None) new)
  (assert (!= old.spec-hash new.spec-hash) changed.after.processes)
  (assert (>= new.started-ms old.ended-ms) changed.after.processes)
  (assert (= (get changed.after.rows "beacon/a" "step") "2") changed.after.rows)
  (assert (= changed.after.readiness.state "Ready") changed.after.readiness))


(deftest test-redeclaring-a-handoff-service-stops-the-old-process-only-after-the-new-one-is-ready
  ;; handoff: 新を旧と並べて起こし、coordinator が新の世代を Ready と数えた後に旧を止める(引数 every を変えた版)。
  (<- changed Changed (sim-cluster (handoff-beacons sim-foundation)
                                   (redeclare-and-watch (handoff-beacons-v2 sim-foundation) "beacon" "beacon/" 15.0)))
  (assert (= (len changed.after.processes) 2) changed.after.processes)
  (val old (get changed.after.processes 0))
  (val new (get changed.after.processes 1))
  (assert (= old.exit-code -15) old)
  (assert (is new.exit-code None) new)
  ;; 並んで動いた(新は旧が止まる前に起きた)。
  (assert (< new.started-ms old.ended-ms) changed.after.processes)
  ;; 旧が止まったのは、新の世代の ready の報告が coordinator に届いた後。
  (val new-ready (lfor r changed.after.reports :if (and (= r.instance new.instance) (= r.kind "readiness") r.ready) r.at))
  (assert new-ready changed.after.reports)
  (assert (<= (min new-ready) old.ended-ms) #(new-ready old))
  (assert (= changed.after.readiness.state "Ready") changed.after.readiness)
  ;; 条 W1(architecture.hy の worker の :invariants): 入れ替えの間も Ready の書き手が途切れない。
  (<- lifetimes tuple (writer-lifetimes changed.after))
  (<- gaps tuple (handoff-keeps-a-ready-writer lifetimes))
  (assert (= gaps #()) #(gaps lifetimes)))


(defk writer-lifetimes [seen]
  {:pre [(: seen Seen)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "条 W1 の判断に渡す記録: 世代ごとの #(最初の Ready の報告の時刻 終わった時刻)(筋書きが読んだ process と報告から組むため)。"
  (tuple (gfor p seen.processes
               #((min (gfor r seen.reports :if (and (= r.instance p.instance) (= r.kind "readiness") r.ready) r.at) :default None)
                 p.ended-ms))))


(deftest test-a-counterexample-worker-that-stops-the-old-process-on-retire-breaks-w1
  ;; 反例(条 W1): 入れ替えで旧を名から外す handler(RetireJob)が外すと同時に旧を止める壊れた worker(retire-stops)では、新が Ready に
  ;; なるまで Ready の書き手が居ない区間ができ、W1 の判断が空白を返す — 本物の handler が旧を動かし続けていることの裏返し。
  (val workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :retire-stops True)))
  (<- changed Changed (sim-cluster (handoff-beacons sim-foundation)
                                   (redeclare-and-watch (handoff-beacons-v2 sim-foundation) "beacon" "beacon/" 15.0)
                                   :workers workers))
  (<- lifetimes tuple (writer-lifetimes changed.after))
  (<- gaps tuple (handoff-keeps-a-ready-writer lifetimes))
  (assert gaps lifetimes))


(defk handed-off-twice [first second]
  {:pre [(: first System) (: second System)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C14 の記録を集めるため: 8 秒待って系 first で宣言し直し、15 秒待って系 second で宣言し直し、15 秒待って beacon の子 process が
   本当に動いた区間を読む(入れ替えを 2 度通した JobProcess の列)。"
  (<- (Delay 8.0))
  (<- _first tuple (Redeclare first))
  (<- (Delay 15.0))
  (<- _second tuple (Redeclare second))
  (<- (Delay 15.0))
  (<- seen tuple (ProcessesOf "beacon"))
  (tuple (gfor p seen (JobProcess :job "beacon" :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


;; 条 C14 の上限: 入れ替えを宣言した beacon は旧と新の 2 つまで。
(val HANDOFF-LIMIT #((RunLimit :job "beacon" :limit 2)))


(deftest test-a-handoff-job-runs-at-most-two-processes-across-two-handoffs
  ;; 条 C14(architecture.hy の :invariants): 入れ替えを 2 度通しても、beacon が同時に動く子 process は旧と新の 2 つまで(本物の worker は
  ;; 新が Ready になった後に旧を止める)。
  (<- processes tuple (sim-cluster (handoff-beacons sim-foundation)
                                   (handed-off-twice (handoff-beacons-v2 sim-foundation) (handoff-beacons-v3 sim-foundation))))
  (assert (>= (len processes) 3) processes)
  (<- over tuple (runs-within-their-limit processes HANDOFF-LIMIT))
  (assert (= over #()) #(over processes)))


(deftest test-a-counterexample-worker-that-hides-retired-processes-breaks-c14
  ;; 条 C14 の失敗ケース: 入れ替えで名から外した旧の process を観測に載せない壊れた worker(SimWorker の hides-retired)では、旧を止める前に
  ;; 次の新が並び、2 度目の入れ替えで beacon が同時に 3 つ動き、条 C14 の判断がその process を名指す。
  (<- processes tuple (sim-cluster (handoff-beacons sim-foundation)
                                   (handed-off-twice (handoff-beacons-v2 sim-foundation) (handoff-beacons-v3 sim-foundation))
                                   :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :hides-retired True))))
  (<- over tuple (runs-within-their-limit processes HANDOFF-LIMIT))
  (assert over processes))


(defk watch-rows [seconds prefix]
  {:pre [(: seconds float) (: prefix str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: seconds 秒待って盤の prefix の行を読む。"
  (<- (Delay seconds))
  (<- rows dict (SharedRows prefix))
  rows)


(deftest test-services-connect-only-through-the-board
  ;; copier は beacon が盤に書いた行を読んで写す(service どうしは ReadShared / WriteShared でだけつながる)。
  (<- rows dict (sim-cluster (relay sim-foundation) (watch-rows 10.0 "relay/")))
  (assert (= (get rows "relay/source" "step") "9") rows)
  (assert (= (get rows "relay/copy" "step") "9") rows)
  (assert (>= (get rows "relay/copy" "n") 1) rows))


(defrecord Fenced
  "柵の検の読み: passer の盤の行と ready・peeker の process。"
  (#^ dict rows)
  (#^ ServiceReadiness passer)
  (#^ tuple peeker))


(defk watch-fence []
  {:pre [] :post [(: % Fenced)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒待って、passer の盤の行と ready・peeker の process を読む。"
  (<- (Delay 10.0))
  (<- rows dict (SharedRows "fence/"))
  (<- passer ServiceReadiness (ReadinessOf "passer"))
  (<- peeker tuple (ProcessesOf "peeker"))
  (Fenced :rows rows :passer passer :peeker peeker))


(deftest test-the-fence-drops-a-process-whose-effect-only-the-sim-could-answer
  ;; 柵: sim の外側が答える物(scheduler の Spawn / Wait・時計の Delay / GetTime)は通る。sim の世界だけが答える effect(検の
  ;; ProcessesOf)を出した process は、本番の子と同じ未処理の例外で落ちる(exit 1 — worker が起こし直しても同じく落ちる)。
  (<- seen Fenced (sim-cluster (fenced sim-foundation) (watch-fence)))
  (assert (= (get seen.rows "fence/passer" "answer") 42) seen.rows)
  (assert (>= (get seen.rows "fence/passer" "elapsedMs") 500) seen.rows)
  (assert (= seen.passer.state "Ready") seen.passer)
  (assert (>= (len seen.peeker) 2) seen.peeker)
  (assert (all (gfor p seen.peeker :if (is-not p.exit-code None) (= p.exit-code 1))) seen.peeker)
  (assert (all (gfor p seen.peeker :if (is-not p.exit-code None) (and (in "UnhandledEffect" p.detail) (in "ProcessesOf" p.detail))))
          seen.peeker)
  (assert (not-in "fence/peeker" seen.rows) seen.rows))


(defrecord Flavored
  "別スコープの検の読み: 盤の行と、答えを持たない service の process。"
  (#^ dict rows)
  (#^ tuple plain))


(defk watch-flavors []
  {:pre [] :post [(: % Flavored)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒待って、盤の flavor/ の行と plain の process を読む。"
  (<- (Delay 10.0))
  (<- rows dict (SharedRows "flavor/"))
  (<- plain tuple (ProcessesOf "plain"))
  (Flavored :rows rows :plain plain))


(deftest test-services-keep-their-own-handlers-for-the-same-effect-type
  ;; 別スコープ: sweet と sour は同じ Flavor の型に別の答えの handler を並べ、混ざらない。handler を並べない plain には他の service の
  ;; handler が届かず、答えの無い effect で落ちる。
  (<- seen Flavored (sim-cluster (flavors sim-foundation) (watch-flavors)))
  (assert (= (get seen.rows "flavor/sweet") "sweet") seen.rows)
  (assert (= (get seen.rows "flavor/sour") "sour") seen.rows)
  (assert (not-in "flavor/plain" seen.rows) seen.rows)
  (assert seen.plain seen.plain)
  (assert (all (gfor p seen.plain :if (is-not p.exit-code None) (and (= p.exit-code 1) (in "Flavor" p.detail)))) seen.plain))


(deftest test-the-environ-override-replaces-a-declared-name-and-refuses-an-undeclared-one
  ;; job ごとの environ の上書きは宣言の :environ に重なる(宿が Ask に答える値が変わる)。宣言に無い名・系に無い job・文字列でない値は
  ;; 走らせる前に断る。
  (<- seen Seen (sim-cluster (beacons sim-foundation) (watch-beacon 8) :environ {"beacon" {"STEP" "7"}}))
  (assert (= (get seen.rows "beacon/a" "step") "7") seen.rows)
  (with [raised (pytest.raises ValueError)]
    (<- (sim-cluster (beacons sim-foundation) (watch-beacon 1) :environ {"beacon" {"UNDECLARED" "x"}})))
  (assert (in "UNDECLARED" (str raised.value)) (str raised.value))
  (with [raised (pytest.raises ValueError)]
    (<- (sim-cluster (beacons sim-foundation) (watch-beacon 1) :environ {"elsewhere" {"STEP" "7"}})))
  (assert (in "elsewhere" (str raised.value)) (str raised.value))
  (with [raised (pytest.raises ValueError)]
    (<- (sim-cluster (beacons sim-foundation) (watch-beacon 1) :environ {"beacon" {"STEP" 7}})))
  (assert (in "STEP" (str raised.value)) (str raised.value)))


(defk watch-trainer []
  {:pre [] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って trainer を読む。"
  (<- (Delay 8.0))
  (<- seen Seen (seen-of "trainer" "gpu/"))
  seen)


(deftest test-a-job-whose-needs-no-worker-provides-is-not-placed
  ;; needs ⊆ provides: どの worker も gpu を提供しなければ置かれず(process が起きない・Ready にならない)、提供する worker が居れば
  ;; その worker に置かれる。
  (val cpu (SimWorker :name "cpu-1" :provides (frozenset ["cluster-net"])))
  (<- none Seen (sim-cluster (gpu-only sim-foundation) (watch-trainer) :workers #(cpu)))
  (assert (= none.processes #()) none.processes)
  (assert (!= none.readiness.state "Ready") none.readiness)
  (assert (not none.rows) none.rows)
  (val gpu (SimWorker :name "gpu-1" :provides (frozenset ["cluster-net" "gpu"])))
  (<- placed Seen (sim-cluster (gpu-only sim-foundation) (watch-trainer) :workers #(cpu gpu)))
  (assert (= (lfor p placed.processes p.worker) ["gpu-1"]) placed.processes)
  (assert (= (get placed.rows "gpu/beat" "step") "1") placed.rows))


(defhandler end-recorder [#^ list ends]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検が受け取る終わりの記録の置き場(検ごとに新しい list)。
  (EndProcess [worker pid ended]
    (.append ends ended)
    (resume None)))


(deftest test-a-program-that-cannot-be-restored-is-refused-before-it-runs
  ;; 詰める時に手元で解き直して確かめる(encode-program — 本番の declare と同じ)ので、解けない値を持つ Program の系は走らせる前に断る。
  (val broken (system-of "broken" #((job "broken" (holding-unloadable sim-foundation (Unloadable))
                                          :call (CallShape :function holding-unloadable :args [sim-foundation "unloadable"] :kwargs {})
                                          :needs #{"cluster-net"}))))
  (with [raised (pytest.raises UnsendableProgram)]
    (<- (sim-cluster broken (Delay 1.0))))
  (assert (in "解けない値" (str raised.value)) (str raised.value)))


(deftest test-the-host-ends-a-process-whose-program-it-could-not-fetch-like-the-production-entry
  ;; 宿が coordinator の置き場から Program を取れていない時: service は本番の job_entry と同じく理由を残して 3 で終わり、task は失敗の
  ;; 結果(RemoteJobFailed)を書いて 0 で終わる。
  (val ends [])
  (val ctx (RunContext "sim://coordinator" "w" "r" "svc"))
  (val child (SimChild :ctx ctx :program-path "" :environ {} :pid 7 :passable #()
                       :link (SimLink :queue (RequestQueue) :actor "svc" :revision "r" :peer "w" :versions {})))
  (<- (with-handlers [(end-recorder ends)]
        (sim-process "w" (JobSpec "svc" "doeff_cluster.worker.entry.job_entry" #("service") "r" :program (* "a" 64)) child None)))
  (<- (with-handlers [(end-recorder ends)]
        (sim-process "w" (JobSpec "task/t1" "doeff_cluster.worker.entry.job_entry" #("task") "r" :once True :program (* "b" 64)) child None)))
  (assert (= (len ends) 2) ends)
  (val service (get ends 0))
  (val task (get ends 1))
  (assert (= service.code 3) service)
  (assert (is service.result None) service)
  (assert (in "取れていない" service.detail) service)
  (assert (= task.code 0) task)
  (val outcome (decode-outcome task.result))
  (assert (isinstance outcome TaskFailed) outcome)
  (assert (= outcome.kind "RemoteJobFailed") outcome))


;; --- process の中の task(段 5b の 1)----------------------------------------------------------------------

(defrecord Spawned
  "子の task の検の読み: 世界を動かす前・直後・後の盤の行と、job の process の列。"
  (#^ dict before)
  (#^ dict just-after)
  (#^ dict later)
  (#^ tuple processes))


(defk crash-spawner []
  {:pre [] :post [(: % Spawned)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 6 秒待って盤を読み、spawner を Crash で落とし、落ちた直後と 12 秒後に盤を読む。"
  (<- (Delay 6.0))
  (<- before dict (SharedRows "spawn/"))
  (<- (Crash "spawner"))
  (<- (Delay 0.1))
  (<- just dict (SharedRows "spawn/"))
  (<- (Delay 12.0))
  (<- later dict (SharedRows "spawn/"))
  (<- processes tuple (ProcessesOf "spawner"))
  (Spawned :before before :just-after just :later later :processes processes))


(deftest test-a-crash-also-stops-the-tasks-the-process-spawned
  ;; 本番の子 process の中の task は process と一緒に消える。sim でも Crash で落とした process の中で Spawn した task(盤に世代の名の
  ;; 鍵で書き続ける)は止まり、起こし直した新しい世代の task だけが書き続ける。
  (<- seen Spawned (sim-cluster (spawners sim-foundation) (crash-spawner)))
  (val old (get seen.processes 0))
  (val new (get seen.processes -1))
  (val old-key (+ "spawn/" old.instance))
  (assert (= old.exit-code 1) seen.processes)
  (assert (>= (get seen.before old-key "n") 2) seen.before)
  (assert (= (get seen.later old-key "n") (get seen.just-after old-key "n")) #(seen.just-after seen.later))
  (assert (!= old.instance new.instance) seen.processes)
  (assert (is new.exit-code None) seen.processes)
  (assert (>= (get seen.later (+ "spawn/" new.instance) "n") 2) seen.later))


(defk watch-quitter []
  {:pre [] :post [(: % Spawned)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って盤を読み、3 秒後にもう一度読む。"
  (<- (Delay 8.0))
  (<- first dict (SharedRows "quit/"))
  (<- (Delay 3.0))
  (<- second dict (SharedRows "quit/"))
  (<- processes tuple (ProcessesOf "quitter"))
  (Spawned :before {} :just-after first :later second :processes processes))


(deftest test-a-process-that-returns-takes-its-spawned-tasks-with-it
  ;; process が値で抜けた(本番の子 process の終わり)後は、中で Spawn した task も書かない。
  (<- seen Spawned (sim-cluster (quitters sim-foundation) (watch-quitter)))
  (val first (get seen.processes 0))
  (val key (+ "quit/" first.instance))
  (assert (= first.exit-code 0) seen.processes)
  ;; 値で抜けた process は、その値を記録に残す(検が有限の周回の答えを読む sim だけの観測 — 本番は捨てる)。
  (assert (= first.value 3) first)
  (assert (<= (get seen.just-after key "n") 5) seen.just-after)
  (assert (= (get seen.later key "n") (get seen.just-after key "n")) #(seen.just-after seen.later)))


(defk ended-reader []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 読み手の process が値で抜けるまで待ち、その process の列を返す。"
  (<- first tuple (ProcessesOf "context-env-reader"))
  (var processes first)
  (while (or (not processes) (is (. (get processes 0) exit-code) None))
    (<- (Delay 1.0))
    (<- again tuple (ProcessesOf "context-env-reader"))
    (:= processes again))
  processes)


(deftest test-the-declared-runtime-env-reaches-the-child-run-context
  ;; sim-cluster の runtime-env は本番の declare の --runtime-env と同じ欄に載り、本物の worker が準備して子の run-context の
  ;; runtime-env(DOEFF_RUNTIME_ENV)として渡す。宣言しなければ子の run-context は空。
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  (<- with-env tuple (sim-cluster (context-env-readers sim-foundation) (ended-reader) :runtime-env env))
  (val read (get with-env 0))
  (assert (= read.exit-code 0) with-env)
  (assert (= (json.loads read.value) declared) read)
  (<- without tuple (sim-cluster (context-env-readers sim-foundation) (ended-reader)))
  (assert (= (. (get without 0) value) "") without))


;; --- 切り離した task を service が出す(段 5b の 2)------------------------------------------------------

(deftest test-a-service-submits-and-awaits-a-detached-task-through-the-host
  ;; service の SubmitDetached・AwaitDetached に sim の宿が本番の detached-cluster と同じ要求(PUT /programs・PUT /detached)で答え、
  ;; 本物の coordinator が task を worker に置き、worker が task の Program を走らせた答えが service に返る。
  (<- rows dict (sim-cluster (detaching sim-foundation) (watch-rows 15.0 "detached/")))
  (assert (= (get rows "detached/result") {"created" True "value" 103 "outcome" "DetachedSucceeded"}) rows))


;; --- coordinator の止まり・落ち(段 5b の 4)----------------------------------------------------------------

(defrecord Outage
  "coordinator の止まりの検の読み: 止める前の盤・止まっている間の ready・作り直した後の job の姿・Pod の一生の列。"
  (#^ dict before)
  (#^ ServiceReadiness during)
  (#^ Seen after)
  (#^ tuple runs))


(defk pause-coordinator [crash seconds]
  {:pre [(: crash bool) (: seconds float)] :post [(: % Outage)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って盤を読み、coordinator を止める(crash なら次の Persist で落とす)。2 秒後に ready を読み、止まっている秒 + 25 秒
   待って beacon を読む。"
  (<- (Delay 8.0))
  (<- before dict (SharedRows "beacon/"))
  (if crash
      (<- (CrashCoordinator seconds))
      (<- (StopCoordinator seconds)))
  (<- (Delay 2.0))
  (<- during ServiceReadiness (ReadinessOf "beacon"))
  (<- (Delay (+ seconds 25.0)))
  (<- after Seen (seen-of "beacon" "beacon/"))
  (<- runs tuple (CoordinatorRuns))
  (Outage :before before :during during :after after :runs runs))


(deftest test-a-stopped-coordinator-is-recreated-from-its-store-after-the-downtime
  ;; 優雅な停止: 止まっている間は届かない(ready は読めない)。止まっている秒の後に同じ置き場から読み直して作り直し、盤の行と Service
  ;; は残り、service は Ready に戻る。
  (<- seen Outage (sim-cluster (beacons sim-foundation) (pause-coordinator False 10.0)))
  (assert (= (len seen.runs) 2) seen.runs)
  (val first (get seen.runs 0))
  (val second (get seen.runs 1))
  (assert (= first.outcome "stopped") first)
  (assert (>= (- second.started-ms first.ended-ms) 10000) seen.runs)
  (assert (is second.ended-ms None) second)
  (assert (= seen.during.state "Missing") seen.during)
  (assert (= seen.after.readiness.state "Ready") seen.after.readiness)
  (assert (> (get seen.after.rows "beacon/a" "n") 0) seen.after.rows)
  ;; 条 C1(architecture.hy の :invariants): 止める前に読めた行は作り直した後も残る。
  (assert seen.before seen)
  (<- lost tuple (acknowledged-writes-survive seen.before seen.after.rows))
  (assert (= lost #()) lost))


(deftest test-a-coordinator-that-fails-to-persist-drops-its-replies-and-is-recreated
  ;; Persist の失敗: 返事をせずに落ちる(その拍の書きの送り手には接続の失敗 — 盤に書けなかった beacon は例外で落ちる)。止まっている秒の
  ;; 後に置き場から作り直し、service は起こし直されて Ready に戻る。
  (<- seen Outage (sim-cluster (beacons sim-foundation) (pause-coordinator True 5.0)))
  (assert (= (len seen.runs) 2) seen.runs)
  (val first (get seen.runs 0))
  (val second (get seen.runs 1))
  (assert (in "Persist の失敗" first.outcome) first)
  (assert (>= (- second.started-ms first.ended-ms) 5000) seen.runs)
  (assert (= seen.after.readiness.state "Ready") seen.after.readiness)
  (assert (any (gfor p seen.after.processes (and (= p.exit-code 1) (in "RemoteJobFailed" p.detail)))) seen.after.processes)
  (assert (is (. (get seen.after.processes -1) exit-code) None) seen.after.processes))


(deftest test-the-coordinator-writes-and-rereads-the-store-the-caller-makes
  ;; 置き場の差し替えの口(#989 — 使い手の反例の壊れた置き場のため): sim-cluster は store が作る置き場を 1 回の走りに 1 つだけ作り、
  ;; coordinator はそこへ書き、止めた後の作り直しも同じ置き場から読み直す(盤の行が残り、service は Ready に戻る)。
  (val made [])
  (<- seen Outage (sim-cluster (beacons sim-foundation) (pause-coordinator False 10.0)
                               :store (fn [] (let [store (MemoryWalStore)] (.append made store) store))))
  (assert (= (len made) 1) made)
  (val store (get made 0))
  (assert (> store.seq 0) store.seq)
  (assert (in "board/beacon/a" store.kv) (sorted store.kv))
  (assert (= (len seen.runs) 2) seen.runs)
  (assert (= seen.after.readiness.state "Ready") seen.after.readiness)
  (assert (> (get seen.after.rows "beacon/a" "n") 0) seen.after.rows))


(defclass PretendsToPersist [MemoryWalStore]
  "壊れた置き場(条 C1 の反例・#1976 の #35): Persist に何も書かずに答える — fsync したふり。coordinator は書けたと思って返事を返すが、
   作り直しで読み直す置き場には何も無い。"
  (defn #^ None persist [self #^ (get dict #(str object)) delta] None))


(deftest test-a-counterexample-store-that-pretends-to-persist-breaks-c1
  ;; 条 C1 の失敗ケース: 置き場の差し替えの口(#989)に Persist を捨てる置き場を差すと、止める前に返事を返した盤の行が作り直した後に
  ;; 無く、条 C1 の判断がその行を名指す(同じ筋書きの本物の置き場では空 — 上の test-a-stopped-coordinator-is-recreated-…)。
  (<- seen Outage (sim-cluster (beacons sim-foundation) (pause-coordinator False 10.0) :store PretendsToPersist))
  (assert seen.before seen)
  (<- lost tuple (acknowledged-writes-survive seen.before seen.after.rows))
  (assert (in "beacon/a" lost) #(lost seen.after.rows)))


(defrecord QuietOutage
  "条 C15・C16 の検の読み: 書き手の止まった盤と Service の名を、止める前(rows-before・services-before)と作り直した後(rows-after・
   services-after)に読んだ物。"
  (#^ dict rows-before)
  (#^ dict rows-after)
  (#^ tuple services-before)
  (#^ tuple services-after))


(defk quiet-board-across-a-stop []
  {:pre [] :post [(: % QuietOutage)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C15・C16 の記録を集めるため: beacon を版 2(盤の行の step = 2)へ宣言し直し、その後 beacon の居ない系(relay)へ宣言し直して
   beacon/ の行の書き手を止める。盤と Service の名を読み、coordinator を 10 秒止め、作り直しの後に 25 秒待ってもう 1 度読む。"
  (<- (Delay 8.0))
  (<- _v2 tuple (Redeclare (beacons-v2 sim-foundation)))
  (<- (Delay 10.0))
  (<- _quiet tuple (Redeclare (relay sim-foundation)))
  (<- (Delay 10.0))
  (<- rows-before dict (SharedRows "beacon/"))
  (<- state-before dict (ReadCoordinator "/state"))
  (<- (StopCoordinator 10.0))
  (<- (Delay 35.0))
  (<- rows-after dict (SharedRows "beacon/"))
  (<- state-after dict (ReadCoordinator "/state"))
  (QuietOutage :rows-before rows-before :rows-after rows-after
               :services-before (tuple (gfor j (get state-before "jobs") (get j "name")))
               :services-after (tuple (gfor j (get state-after "jobs") (get j "name")))))


(deftest test-a-quiet-board-and-the-declared-services-survive-a-stop
  ;; 条 C15・C16(architecture.hy の :invariants): 書き手の止まった盤の行は、作り直した後も止める前と同じ値(版 2 の step = 2)で、
  ;; 受け付けた Service(relay の 2 つ)も在る。
  (<- seen QuietOutage (sim-cluster (beacons sim-foundation) (quiet-board-across-a-stop)))
  (assert (= (get seen.rows-before "beacon/a" "step") "2") seen.rows-before)
  (assert seen.services-before seen)
  (<- changed tuple (acknowledged-values-survive seen.rows-before seen.rows-after))
  (assert (= changed #()) #(changed seen))
  (<- missing tuple (declared-services-survive seen.services-before seen.services-after))
  (assert (= missing #()) #(missing seen)))


(defclass KeepsFirstValue [MemoryWalStore]
  "壊れた置き場(条 C15 の反例・#1976 の写しの C1 の残り): 盤の行は最初に書いた値だけを残し、後の書きを受けた(返事は返る)のに
   置き場の値を変えない — 作り直しで古い値を読み直す。"
  (defn #^ None persist [self #^ (get dict #(str object)) delta]
    (.persist (super) (dfor #(k v) (.items delta) :if (or (not (.startswith k "board/")) (not-in k self.kv)) k v))))


(deftest test-a-counterexample-store-that-keeps-the-first-value-breaks-c15
  ;; 条 C15 の失敗ケース: 置き場の差し替えの口(#989)に盤の行の最初の値だけを残す置き場を差すと、作り直した coordinator の盤の行が
  ;; 止める前の値(step = 2)でなく最初の値になり、条 C15 がその行を名指す(C1 は鍵が残るので緑のまま)。
  (<- seen QuietOutage (sim-cluster (beacons sim-foundation) (quiet-board-across-a-stop) :store KeepsFirstValue))
  (<- lost tuple (acknowledged-writes-survive seen.rows-before seen.rows-after))
  (assert (= lost #()) #(lost seen))
  (<- changed tuple (acknowledged-values-survive seen.rows-before seen.rows-after))
  (assert (in "beacon/a" changed) #(changed seen)))


(defclass ForgetsServices [MemoryWalStore]
  "壊れた置き場(条 C16 の反例・#1976 の写しの C1 の残り): 書きは受けるが、作り直しの読み直しで Service の宣言(service/ の鍵)を
   渡さない — coordinator は盤を残したまま宣言を失って起き直す。"
  (defn #^ (get dict #(str object)) table [self]
    (dfor #(k v) (.items self.kv) :if (not (.startswith k "service/")) k v))
  (defn #^ dict load [self] (.table self)))


(deftest test-a-counterexample-store-that-forgets-services-breaks-c16
  ;; 条 C16 の失敗ケース: 置き場の差し替えの口(#989)に読み直しで Service の宣言を渡さない置き場を差すと、作り直した coordinator の
  ;; GET /state に止める前の Service が無く、条 C16 がその名を名指す。
  (<- seen QuietOutage (sim-cluster (beacons sim-foundation) (quiet-board-across-a-stop) :store ForgetsServices))
  (<- missing tuple (declared-services-survive seen.services-before seen.services-after))
  (assert missing seen))


(defk revision-across-a-stop [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C5 の記録を集めるため: 8 秒待って GET /state の版を読み、coordinator を seconds 秒止め、作り直しの後に 25 秒待ってもう 1 度読む
   (読んだ順の RevisionRead の列・時刻は筋書きの予定の刻)。"
  (<- (Delay 8.0))
  (<- before dict (ReadCoordinator "/state"))
  (<- (StopCoordinator seconds))
  (<- (Delay (+ seconds 25.0)))
  (<- after dict (ReadCoordinator "/state"))
  #((RevisionRead :at-ms 8000 :revision (get before "revision"))
    (RevisionRead :at-ms (int (* (+ 8.0 seconds 25.0) 1000)) :revision (get after "revision"))))


(deftest test-the-coordinator-revision-never-goes-back-across-a-stop
  ;; 条 C5(architecture.hy の :invariants): 止まりの前に読んだ版より、作り直しの後の版が小さくない(本物の置き場は版を読み直す)。
  (<- reads tuple (sim-cluster (beacons sim-foundation) (revision-across-a-stop 10.0)))
  (assert (> (. (get reads 0) revision) 0) reads)
  (<- drops tuple (revision-never-goes-back reads))
  (assert (= drops #()) drops))


(defclass ForgetsOnReload [MemoryWalStore]
  "壊れた置き場(条 C5 の反例・#1976 の #36): 書きは受けるが、作り直しの読み直しで「何も無い」と答える — coordinator は空から起き直す。"
  (defn #^ bool exists [self] False))


(deftest test-a-counterexample-store-that-forgets-on-reload-breaks-c5
  ;; 条 C5 の失敗ケース: 置き場の差し替えの口(#989)に読み直しで何も返さない置き場を差すと、作り直した coordinator の版が止まりの前より
  ;; 小さくなり、条 C5 の判断がその読みの組を名指す(同じ筋書きの本物の置き場では空 — 上の test-the-coordinator-revision-…)。
  (<- reads tuple (sim-cluster (beacons sim-foundation) (revision-across-a-stop 10.0) :store ForgetsOnReload))
  (<- drops tuple (revision-never-goes-back reads))
  (assert (= (len drops) 1) #(drops reads)))


(defk service-versions-across-a-stop [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C12 の記録を集めるため: 8 秒待って GET /state の Service ごとの resourceVersion を読み、coordinator を seconds 秒止め、作り直しの後に
   25 秒待ってもう 1 度読む(読んだ順の ServiceVersionRead の列・時刻は筋書きの予定の刻)。"
  (<- (Delay 8.0))
  (<- before dict (ReadCoordinator "/state"))
  (<- (StopCoordinator seconds))
  (<- (Delay (+ seconds 25.0)))
  (<- after dict (ReadCoordinator "/state"))
  (val read-of (fn [at-ms state] (tuple (gfor job (get state "jobs")
                                             (ServiceVersionRead :at-ms at-ms :service (get job "name")
                                                                 :version (get job "resourceVersion"))))))
  (+ (read-of 8000 before) (read-of (int (* (+ 8.0 seconds 25.0) 1000)) after)))


(deftest test-service-versions-never-go-back-across-a-stop
  ;; 条 C12(architecture.hy の :invariants): 止まりの前に読んだ Service ごとの resourceVersion より、作り直しの後の版が小さくない(本物の
  ;; 置き場は資源の版の記録を読み直す)。
  (<- reads tuple (sim-cluster (beacons sim-foundation) (service-versions-across-a-stop 10.0)))
  (assert (any (gfor r reads (> r.version 1))) reads)
  (<- drops tuple (service-versions-never-go-back reads))
  (assert (= drops #()) drops))


(defclass ResetsServiceVersionsOnReload [MemoryWalStore]
  "壊れた置き場(条 C12 の反例・#1976 の写しの C2 の残り): 書きは受けるが、作り直しの読み直しで資源の版の記録(meta/)の resourceVersion と、
   版を配る数(counter の revision)を 1 に戻して渡す — coordinator は Service を残したまま版だけ古くして起き直す。"
  (defn #^ (get dict #(str object)) table [self]
    (dfor #(k v) (.items self.kv)
          k (cond (.startswith k "meta/") (| v {"resourceVersion" 1})
                  (= k "counter") (| v {"revision" 1})
                  True v)))
  (defn #^ dict load [self] (.table self)))


(deftest test-a-counterexample-store-that-resets-service-versions-breaks-c12
  ;; 条 C12 の失敗ケース: 置き場の差し替えの口(#989)に読み直しで Service の版を 1 に戻す置き場を差すと、作り直した coordinator の
  ;; Service の resourceVersion が止まりの前より小さくなり、条 C12 がその読みの組を名指す(同じ筋書きの本物の置き場では空 — 上の
  ;; test-service-versions-never-go-back-across-a-stop)。
  (<- reads tuple (sim-cluster (beacons sim-foundation) (service-versions-across-a-stop 10.0) :store ResetsServiceVersionsOnReload))
  (<- drops tuple (service-versions-never-go-back reads))
  (assert drops reads)
  (assert (all (gfor d drops (< d.later.version d.earlier.version))) drops))


(val PROBE-SLACK-MS 1500)  ; 条 L2 の余裕: heartbeat の間隔と読みの拍の差


(defk liveness-across-a-stop []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 L2 の記録を集めるため: 8 秒待って beacon の worker を node ごと死なせ、2 秒後に coordinator を 2 秒止め、21 秒にその worker の生存を
   読む(WorkerProbe の列・時刻は筋書きの予定の刻)。21 秒は、本物の置き場なら止まりの長さだけずらした最後の連絡(8 秒 + 2 秒)から lease を
   過ぎて生きていないと答え、最後の連絡を読み直せない置き場なら作り直し(約 12 秒)から lease のうちで生きていると答える刻 — 死から
   lease + 余裕(19.5 秒)を過ぎている。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "beacon"))
  (val host (. (get before 0) worker))
  (<- (KillWorker host))
  (<- (Delay 2.0))
  (<- (StopCoordinator 2.0))
  (<- (Delay 11.0))
  (<- view dict (ReadCoordinator (+ "/workers/" host)))
  #((WorkerProbe :at-ms 21000 :worker host :alive (bool (get view "alive")) :unreachable-since-ms 8000)))


(deftest test-a-dead-worker-is-not-alive-after-the-coordinator-is-recreated
  ;; 条 L2(architecture.hy の :invariants): 死んだ worker は、coordinator が作り直された後も lease の後に生きていると答えられない(本物の
  ;; 置き場は最後の連絡の時刻を読み直す)。
  (<- probes tuple (sim-cluster (beacons sim-foundation) (liveness-across-a-stop)))
  (<- lies tuple (alive-only-while-reachable probes (. (ClusterTiming) lease-ms) PROBE-SLACK-MS))
  (assert (= lies #()) #(lies probes)))


(defclass DropsLastSeen [MemoryWalStore]
  "壊れた置き場(条 L2 の反例・#1976 の #34): worker の最後の連絡の時刻 lastSeenMs と、最後の生存の印の時刻 aliveMs(鍵 counter)を
   置かない(L643 の前の形)— 作り直した coordinator は worker の最後の連絡を知らず、読み直した時刻に連絡があったとみなす。"
  (defn #^ None persist [self #^ (get dict #(str object)) delta]
    (setv drop (fn [v name] (if (isinstance v dict) (dfor #(a b) (.items v) :if (!= a name) a b) v)))
    (.persist (super) (dfor #(k v) (.items delta)
                            k (cond (.startswith k "worker/") (drop v "lastSeenMs")
                                    (= k "counter") (drop v "aliveMs")
                                    True v)))))


(deftest test-a-counterexample-store-without-last-seen-breaks-l2
  ;; 条 L2 の失敗ケース: 置き場の差し替えの口(#989)に lastSeenMs を置かない置き場を差すと、作り直した coordinator が死んだ worker を
  ;; 生きていると答え、条 L2 の判断がその読みを名指す(同じ筋書きの本物の置き場では空 — 上の test-a-dead-worker-is-not-alive-…)。
  (<- probes tuple (sim-cluster (beacons sim-foundation) (liveness-across-a-stop) :store DropsLastSeen))
  (<- lies tuple (alive-only-while-reachable probes (. (ClusterTiming) lease-ms) PROBE-SLACK-MS))
  (assert (= (len lies) 1) #(lies probes)))


(defrecord PlacedAfterAStop
  "条 L1 の検の読み: 読めた置き先の列(PlacementSeen)と、死なせた worker の届かなくなった時刻(WorkerGone — 死んだ process の終わりの刻)。"
  (#^ tuple placements)
  (#^ tuple gone))


(defk placement-after-a-stop []
  {:pre [] :post [(: % PlacedAfterAStop)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 L1 の記録を集めるため: 8 秒に beacon の worker を死なせ、10 秒から coordinator を 2 秒止め、21 秒に service beacon-b を足した系を
   宣言し直して、その少し後に置き先を読む。21 秒の理由は liveness-across-a-stop と同じ(本物の置き場なら死んだ worker を生きていないと
   読み、最後の連絡を読み直せない置き場なら生きていると読む刻)。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "beacon"))
  (val host (. (get before 0) worker))
  (<- (KillWorker host))
  (<- (Delay 2.0))
  (<- (StopCoordinator 2.0))
  (<- (Delay 11.0))
  (<- (Redeclare (beacons-plus sim-foundation)))
  (<- (Delay 0.5))
  (<- state dict (ReadCoordinator "/state"))
  (<- after tuple (ProcessesOf "beacon"))
  (val ended (next (gfor p after :if (= p.exit-code -9) p.ended-ms)))
  (PlacedAfterAStop
    :placements (tuple (gfor #(job p) (.items (get state "placements"))
                             (PlacementSeen :job job :worker (get p "worker") :since-ms (get p "since_ms"))))
    :gone #((WorkerGone :worker host :since-ms ended))))


(deftest test-the-recreated-coordinator-places-no-new-job-on-a-dead-worker
  ;; 条 L1(architecture.hy の :invariants): 作り直した coordinator は、死んだ worker へ新しい job を置かない(本物の置き場は最後の連絡の
  ;; 時刻を読み直す — 置ける worker が他に無いので beacon-b は置かれない)。
  (<- seen PlacedAfterAStop (sim-cluster (beacons sim-foundation) (placement-after-a-stop)))
  (<- wrong tuple (places-only-on-reachable seen.placements seen.gone (. (ClusterTiming) lease-ms) PROBE-SLACK-MS))
  (assert (= wrong #()) #(wrong seen)))


(deftest test-a-counterexample-store-without-last-seen-breaks-l1
  ;; 条 L1 の失敗ケース: 最後の連絡を読み直せない置き場(DropsLastSeen)では、作り直した coordinator が死んだ worker を生きていると読み、
  ;; 足した beacon-b をそこへ置き、条 L1 の判断がその置き先を名指す。
  (<- seen PlacedAfterAStop (sim-cluster (beacons sim-foundation) (placement-after-a-stop) :store DropsLastSeen))
  (<- wrong tuple (places-only-on-reachable seen.placements seen.gone (. (ClusterTiming) lease-ms) PROBE-SLACK-MS))
  (assert (in "beacon-b" (lfor p wrong p.job)) #(wrong seen)))


(defk spans-on-one-worker []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C6 の記録を集めるため: 15 秒待って、beacons-plus の 2 つの service(beacon・beacon-b)の process の生きていた区間を読む。"
  (<- (Delay 15.0))
  (<- a tuple (ProcessesOf "beacon"))
  (<- b tuple (ProcessesOf "beacon-b"))
  (tuple (gfor p (+ a b) (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


(val ONE-SLOT (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :capacity 1))


(deftest test-a-worker-runs-no-more-jobs-than-its-capacity
  ;; 条 C6(architecture.hy の :invariants): capacity 1 の worker 1 台に service 2 つの系を置くと、coordinator は 1 つだけ置き、動く数は
  ;; capacity を越えない。
  (<- spans tuple (sim-cluster (beacons-plus sim-foundation) (spans-on-one-worker) :workers #(ONE-SLOT)))
  (assert spans spans)
  (<- over tuple (running-within-capacity spans #((WorkerCapacity :worker "w1" :capacity 1))))
  (assert (= over #()) #(over spans)))


(deftest test-a-counterexample-worker-that-overstates-its-capacity-breaks-c6
  ;; 条 C6 の失敗ケース: heartbeat で capacity を多く名乗る壊れた worker(SimWorker の overstates-capacity)では、coordinator が名乗りどおり
  ;; 2 つ置き、本当の capacity 1 を越えて動き、条 C6 の判断がその瞬間を名指す。
  (<- spans tuple (sim-cluster (beacons-plus sim-foundation) (spans-on-one-worker)
                               :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :capacity 1 :overstates-capacity 5))))
  (<- over tuple (running-within-capacity spans #((WorkerCapacity :worker "w1" :capacity 1))))
  (assert (= (len over) 1) #(over spans)))


(defk placements-after [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C7 の記録を集めるため: seconds 秒待って GET /state の置き先を読む(PlacementSeen の列)。"
  (<- (Delay seconds))
  (<- state dict (ReadCoordinator "/state"))
  (tuple (gfor #(job p) (.items (get state "placements"))
               (PlacementSeen :job job :worker (get p "worker") :since-ms (get p "since_ms")))))


(val BEACON-NEEDS #((JobNeeds :job "beacon" :needs (frozenset ["cluster-net"]))))
(val GPU-ONLY-ABILITY #((WorkerAbility :worker "g1" :provides (frozenset ["gpu"]))))


(deftest test-a-job-is-not-placed-on-a-worker-without-its-needs
  ;; 条 C7(architecture.hy の :invariants): job の needs(cluster-net)を提供しない worker しか居なければ、coordinator はそこへ置かない。
  (<- placements tuple (sim-cluster (beacons sim-foundation) (placements-after 15.0)
                                    :workers #((SimWorker :name "g1" :provides (frozenset ["gpu"])))))
  (<- wrong tuple (placed-only-where-eligible placements BEACON-NEEDS GPU-ONLY-ABILITY))
  (assert (= wrong #()) #(wrong placements)))


(deftest test-a-counterexample-worker-that-claims-abilities-it-lacks-breaks-c7
  ;; 条 C7 の失敗ケース: heartbeat で持たない能力を名乗る壊れた worker(SimWorker の claims-provides)では、coordinator が名乗りどおり置き、
  ;; 条 C7 の判断がその置き先を名指す(本当の能力は gpu だけ)。
  (<- placements tuple (sim-cluster (beacons sim-foundation) (placements-after 15.0)
                                    :workers #((SimWorker :name "g1" :provides (frozenset ["gpu"])
                                                          :claims-provides (frozenset ["gpu" "cluster-net"])))))
  (<- wrong tuple (placed-only-where-eligible placements BEACON-NEEDS GPU-ONLY-ABILITY))
  (assert (= (lfor p wrong p.job) ["beacon"]) #(wrong placements)))


(val GPU-EXCLUSIVE #((WorkerExclusive :worker "g1" :exclusive (frozenset ["gpu"]))))


(deftest test-an-exclusive-worker-takes-no-job-that-does-not-need-its-ability
  ;; 条 C10(architecture.hy の :invariants): gpu を専用の能力に持つ worker しか居なければ、gpu を要らない beacon はそこへ置かれない。
  (<- placements tuple (sim-cluster (beacons sim-foundation) (placements-after 15.0)
                                    :workers #((SimWorker :name "g1" :provides (frozenset ["cluster-net" "gpu"])
                                                          :exclusive (frozenset ["gpu"])))))
  (<- wrong tuple (exclusive-workers-take-only-their-jobs placements BEACON-NEEDS GPU-EXCLUSIVE))
  (assert (= wrong #()) #(wrong placements)))


(deftest test-a-counterexample-worker-that-hides-its-exclusive-ability-breaks-c10
  ;; 条 C10 の失敗ケース: heartbeat で専用の能力を名乗らない壊れた worker(SimWorker の claims-exclusive = 空)では、coordinator が gpu を
  ;; 要らない beacon をそこへ置き、条 C10 の判断がその置き先を名指す(本当の専用の能力は gpu)。
  (<- placements tuple (sim-cluster (beacons sim-foundation) (placements-after 15.0)
                                    :workers #((SimWorker :name "g1" :provides (frozenset ["cluster-net" "gpu"])
                                                          :exclusive (frozenset ["gpu"]) :claims-exclusive (frozenset)))))
  (<- wrong tuple (exclusive-workers-take-only-their-jobs placements BEACON-NEEDS GPU-EXCLUSIVE))
  (assert (= (lfor p wrong p.job) ["beacon"]) #(wrong placements)))


(defk beacon-processes-after [seconds]
  {:pre [(: seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C13 の記録を集めるため: seconds 秒待って、beacon の子 process が本当に動いた区間を読む(JobProcess の列 — 真実の側)。"
  (<- (Delay seconds))
  (<- seen tuple (ProcessesOf "beacon"))
  (tuple (gfor p seen (JobProcess :job "beacon" :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


;; 条 C13 の筋書きの 2 台: gpu を専用に持つ g1 と、cluster-net を持つ w1(beacon は cluster-net を要る)。
(val GPU-AND-NET-ABILITIES #((WorkerAbility :worker "g1" :provides (frozenset ["gpu"]))
                             (WorkerAbility :worker "w1" :provides (frozenset ["cluster-net"]))))


(deftest test-a-job-process-runs-only-on-an-eligible-worker
  ;; 条 C13(architecture.hy の :invariants): gpu だけを持つ g1 と cluster-net を持つ w1 が居れば、beacon の子 process は w1 でだけ動く。
  (<- processes tuple (sim-cluster (beacons sim-foundation) (beacon-processes-after 15.0)
                                   :workers #((SimWorker :name "g1" :provides (frozenset ["gpu"]))
                                              (SimWorker :name "w1" :provides (frozenset ["cluster-net"])))))
  (assert processes processes)
  (<- wrong tuple (ran-only-where-eligible processes BEACON-NEEDS GPU-AND-NET-ABILITIES GPU-EXCLUSIVE))
  (assert (= wrong #()) #(wrong processes)))


(deftest test-a-counterexample-worker-that-claims-abilities-it-lacks-breaks-c13
  ;; 条 C13 の失敗ケース: heartbeat で持たない能力を名乗る壊れた worker(SimWorker の claims-provides)しか居なければ、beacon の子 process が
  ;; 本当は cluster-net を持たない g1 で動き、条 C13 の判断がその process を名指す。
  (<- processes tuple (sim-cluster (beacons sim-foundation) (beacon-processes-after 15.0)
                                   :workers #((SimWorker :name "g1" :provides (frozenset ["gpu"])
                                                         :claims-provides (frozenset ["gpu" "cluster-net"])))))
  (<- wrong tuple (ran-only-where-eligible processes BEACON-NEEDS GPU-AND-NET-ABILITIES #()))
  (assert wrong processes)
  (assert (all (gfor p wrong (= p.worker "g1"))) wrong))


(val DRAIN-TTL 60.0)  ; 条 C11 の筋書きの drain の期限(秒)


(defrecord DrainedAndRedeclared
  "条 C11 の検の読み: 読めた置き先の列(PlacementSeen)と drain の窓(DrainWindow)。"
  (#^ tuple placements)
  (#^ tuple drains))


(defk placements-during-a-drain [worker]
  {:pre [(: worker str)] :post [(: % DrainedAndRedeclared)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C11 の記録を集めるため: 8 秒待って worker の drain を頼み、2 秒後に service beacon-b を足した系を宣言し直し、さらに 2 秒後に置き先を読む
   (drain の窓は頼んだ刻から DRAIN-TTL 秒)。"
  (<- (Delay 8.0))
  (<- since int (now-epoch-ms))
  (<- (DrainWorker worker DRAIN-TTL))
  (<- (Delay 2.0))
  (<- (Redeclare (beacons-plus sim-foundation)))
  (<- (Delay 2.0))
  (<- placements tuple (placements-after 0.0))
  (DrainedAndRedeclared :placements placements
                        :drains #((DrainWindow :worker worker :since-ms since :until-ms (+ since (int (* DRAIN-TTL 1000)))))))


(deftest test-a-draining-worker-takes-no-new-job
  ;; 条 C11(architecture.hy の :invariants): drain を頼んだ worker 1 台の世界で系に service を足しても、coordinator は drain の期限の内に
  ;; そこへ置かない(beacon-b は置かれない)。
  (<- seen DrainedAndRedeclared (sim-cluster (beacons sim-foundation) (placements-during-a-drain "w1")
                                             :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"])))))
  (<- wrong tuple (no-new-place-while-draining seen.placements seen.drains))
  (assert (= wrong #()) #(wrong seen)))


(deftest test-a-counterexample-worker-that-claims-a-new-generation-every-beat-breaks-c11
  ;; 条 C11 の失敗ケース: heartbeat ごとに新しい世代を名乗る壊れた worker(SimWorker の fresh-boot-every-beat)では、coordinator は drain を
  ;; 別の世代の頼みとして付けないか解き、足した beacon-b を drain の期限の内にそこへ置き、条 C11 の判断がその置き先を名指す。
  (<- seen DrainedAndRedeclared (sim-cluster (beacons sim-foundation) (placements-during-a-drain "w1")
                                             :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"])
                                                                   :fresh-boot-every-beat True))))
  (<- wrong tuple (no-new-place-while-draining seen.placements seen.drains))
  (assert (in "beacon-b" (lfor p wrong p.job)) #(wrong seen)))


(val FAILOVER-SLACK-MS 20000)  ; 条 C8 の余裕: 移し替えの期限の後、新しい担い手で process が起きるまで(木の用意・検め・起動 + 拍)


(defrecord KilledCarrier
  "条 C8 の検の読み: beacon の process の区間の列(JobProcess)・担い手の死(WorkerGone — 死んだ process の終わりの刻)・死なせた worker の名。"
  (#^ tuple processes)
  (#^ tuple deaths)
  (#^ str host))


(defk carrier-killed-then-waited []
  {:pre [] :post [(: % KilledCarrier)] :tags {:context "doeff-cluster-test" :role "program"}}
  "条 C8 の記録を集めるため: 8 秒待って beacon の担い手を node ごと死なせ、移し替えの期限 + 余裕(80 秒)より後の 85 秒後に beacon の
   process の区間を読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "beacon"))
  (val host (. (get before 0) worker))
  (<- (KillWorker host))
  (<- (Delay 85.0))
  (<- after tuple (ProcessesOf "beacon"))
  (val ended (next (gfor p after :if (= p.exit-code -9) p.ended-ms)))
  (KilledCarrier :processes (tuple (gfor p after (JobProcess :job "beacon" :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms)))
                 :deaths #((WorkerGone :worker host :since-ms ended))
                 :host host))


;; 条 C8 の期限は本番の移し替えの期限から作る(数を検に写さない)。
(val FAILOVER-DEADLINE-MS (+ (. (ClusterTiming) reassign-after-ms) FAILOVER-SLACK-MS))


(deftest test-the-job-of-a-dead-carrier-moves-to-a-live-worker-in-time
  ;; 条 C8(architecture.hy の :invariants): 2 台のうち担い手を死なせると、もう 1 台(本当に受けられる)へ期限のうちに移って動く。
  (<- seen KilledCarrier (sim-cluster (beacons sim-foundation) (carrier-killed-then-waited) :workers TWO-WORKERS))
  (val takers (frozenset (gfor w TWO-WORKERS :if (!= w.name seen.host) w.name)))
  (<- stranded tuple (moves-to-a-live-worker seen.processes seen.deaths takers FAILOVER-DEADLINE-MS))
  (assert (= stranded #()) #(stranded seen)))


(deftest test-a-counterexample-worker-that-hides-its-abilities-breaks-c8
  ;; 条 C8 の失敗ケース: もう 1 台が heartbeat で能力を名乗らない壊れた worker(SimWorker の claims-provides = 空)だと、coordinator は
  ;; 移せる先が無いと読んで job を担い手から動かさず、本当は受けられる w2 が生きているのに期限を過ぎ、条 C8 の判断がその job を名指す。
  (<- seen KilledCarrier (sim-cluster (beacons sim-foundation) (carrier-killed-then-waited)
                                      :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]))
                                                 (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :claims-provides (frozenset)))))
  (assert (= seen.host "w1") seen)
  (<- stranded tuple (moves-to-a-live-worker seen.processes seen.deaths (frozenset ["w2"]) FAILOVER-DEADLINE-MS))
  (assert (= (lfor s stranded s.job) ["beacon"]) #(stranded seen)))


(deftest test-a-store-maker-that-does-not-make-a-memory-store-is-refused
  ;; 置き場を作る関数が MemoryWalStore でない値を返せば、走らせる前に断る(emulated-handlers と load-state が読む口が無い)。
  (with [raised (pytest.raises TypeError)]
    (<- (sim-cluster (beacons sim-foundation) (Delay 1.0) :store (fn [] {}))))
  (assert (in "MemoryWalStore" (str raised.value)) (str raised.value)))


;; --- worker の死・止め・網の切断・drain(段 5b の 4)----------------------------------------------------

(val TWO-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]))
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]))))


(defrecord Moved
  "worker を動かす検の読み: 動かした effect の答え・動かす前と後の process・盤の行・動かした worker の coordinator の見え方。"
  (#^ (| int dict None) answer)
  (#^ str host)
  (#^ tuple before)
  (#^ tuple mid)
  (#^ tuple after)
  (#^ dict just-rows)
  (#^ dict later-rows)
  (#^ dict view))


(defk kill-host [job prefix]
  {:pre [(: job str) (: prefix str)] :post [(: % Moved)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って job の process の worker を node ごと死なせ、直後と 70 秒後(lease と移し替えの時間の後)に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf job))
  (val host (. (get before 0) worker))
  (<- n int (KillWorker host))
  (<- (Delay 0.1))
  (<- just dict (SharedRows prefix))
  (<- (Delay 70.0))
  (<- later dict (SharedRows prefix))
  (<- after tuple (ProcessesOf job))
  (<- view dict (ReadCoordinator (+ "/workers/" host)))
  (Moved :answer n :host host :before before :mid #() :after after :just-rows just :later-rows later :view view))


(deftest test-a-dead-worker-takes-its-processes-and-their-tasks-and-the-job-moves
  ;; worker が node ごと死ぬ: 子 process は exit -9(中で Spawn した task も止まる)・heartbeat が止まり、coordinator は lease の後に
  ;; 生きていないと数え、移し替えの時間の後に job を生きている worker へ置く。
  (<- seen Moved (sim-cluster (spawners sim-foundation) (kill-host "spawner" "spawn/") :workers TWO-WORKERS))
  (assert (= seen.answer 1) seen.answer)
  (val old (get seen.after 0))
  (val new (get seen.after -1))
  (assert (= old.exit-code -9) seen.after)
  (val old-key (+ "spawn/" old.instance))
  (assert (= (get seen.later-rows old-key "n") (get seen.just-rows old-key "n")) #(seen.just-rows seen.later-rows))
  (assert (!= new.worker seen.host) seen.after)
  (assert (is new.exit-code None) seen.after)
  (assert (>= (get seen.later-rows (+ "spawn/" new.instance) "n") 1) seen.later-rows)
  (assert (not (get seen.view "alive")) seen.view))


(defk stop-and-start-host []
  {:pre [] :post [(: % Moved)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って pulse の worker を優雅に止め(抜けるまで待つ)、新しい世代で起こし直して 10 秒後に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- (StopWorker host))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- again bool (StartWorker host))
  (<- twice bool (StartWorker host))
  (<- (Delay 10.0))
  (<- after tuple (ProcessesOf "pulse"))
  (<- view dict (ReadCoordinator (+ "/workers/" host)))
  (Moved :answer {"again" again "twice" twice} :host host :before before :mid mid :after after :just-rows {} :later-rows {}
         :view view))


(deftest test-a-stopped-worker-stops-its-jobs-and-a-new-generation-takes-them-back
  ;; 優雅な停止(本番の SIGTERM): 抜けるまでに全 job を止めの合図(-15)で回収する。StartWorker は新しい世代(boot)で起こし、動いて
  ;; いる worker には偽を返す。新しい世代が job を起こし直す。
  (<- seen Moved (sim-cluster (pulses sim-foundation) (stop-and-start-host)))
  (val first (get seen.mid 0))
  (assert (= first.exit-code -15) seen.mid)
  (assert (= seen.answer {"again" True "twice" False}) seen.answer)
  (val last (get seen.after -1))
  (assert (is last.exit-code None) seen.after)
  (assert (!= last.instance first.instance) seen.after)
  (assert (get seen.view "alive") seen.view)
  (assert (.endswith (get seen.view "boot") "-boot2") seen.view))


(defk cut-host []
  {:pre [] :post [(: % Moved)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って pulse の worker の網を 90 秒切り、10 秒後と 70 秒後に読む。"
  (<- (Delay 8.0))
  (<- before tuple (ProcessesOf "pulse"))
  (val host (. (get before 0) worker))
  (<- (CutWorker host 90.0))
  (<- (Delay 10.0))
  (<- mid tuple (ProcessesOf "pulse"))
  (<- (Delay 60.0))
  (<- after tuple (ProcessesOf "pulse"))
  (<- view dict (ReadCoordinator (+ "/workers/" host)))
  (Moved :answer None :host host :before before :mid mid :after after :just-rows {} :later-rows {} :view view))


(deftest test-a-cut-off-worker-keeps-its-process-until-the-fence-and-the-job-moves
  ;; 網の切断: heartbeat が届かない間も子 process は動き続け(10 秒後)、fence(20 秒)を越えると本物の worker_policy の判断で lease を
  ;; 持たない job を止める(-15)。coordinator は移し替えの時間の後に、網のつながった worker へ置く。他に置ける worker が在る job の形 —
  ;; 置ける worker が 1 台の job は印で止めず置き先も外さない(#2804 — tests/test_keep_when_cut_off.hy)。
  (<- seen Moved (sim-cluster (pulses sim-foundation) (cut-host) :workers TWO-WORKERS))
  (val first (get seen.mid 0))
  (assert (is first.exit-code None) seen.mid)
  (val stopped (get seen.after 0))
  (assert (= stopped.exit-code -15) seen.after)
  (assert (>= (- stopped.ended-ms (+ (. (get seen.before 0) started-ms) 0)) 20000) seen.after)
  (val moved (get seen.after -1))
  (assert (!= moved.worker seen.host) seen.after)
  (assert (is moved.exit-code None) seen.after)
  (assert (not (get seen.view "alive")) seen.view))


(defk drain-host []
  {:pre [] :post [(: % Moved)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒待って beacon の worker の drain を頼み、25 秒後に読む。"
  (<- (Delay 10.0))
  (<- before tuple (ProcessesOf "beacon"))
  (val host (. (get before 0) worker))
  (<- answer dict (DrainWorker host))
  (<- (Delay 25.0))
  (<- after tuple (ProcessesOf "beacon"))
  (<- view dict (ReadCoordinator (+ "/workers/" host)))
  (Moved :answer answer :host host :before before :mid #() :after after :just-rows {} :later-rows {} :view view))


(deftest test-a-drained-worker-hands-its-handoff-service-to-another-worker
  ;; drain(本番の preStop と同じ頼み): coordinator は drain 中の worker の上の入れ替えの service を、もう 1 台の worker に並べて起こし、
  ;; 新しい世代が Ready になってから旧を止める。worker は drain 中と見える(ready でない)。
  (<- seen Moved (sim-cluster (handoff-beacons sim-foundation) (drain-host) :workers TWO-WORKERS))
  (assert (isinstance seen.answer dict) seen.answer)
  (assert (= (get seen.answer "status") 200) seen.answer)
  (assert (get seen.view "draining") seen.view)
  (assert (not (get seen.view "ready")) seen.view)
  (val old (get seen.after 0))
  (val new (get seen.after -1))
  (assert (= old.exit-code -15) seen.after)
  (assert (!= new.worker seen.host) seen.after)
  (assert (is new.exit-code None) seen.after))


(deftest test-a-counterexample-worker-that-ignores-the-fence-leaves-two-live-processes
  ;; 反例(fence の意味): coordinator に届かない間も job を止めない壊れた worker(ignores-fence)では、移し替えの後に同じ job の process が
  ;; 2 つ同時に動く — 本物の worker_policy の fence がそれを防いでいることの裏返し。
  (val workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :ignores-fence True)
                 (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :ignores-fence True)))
  (<- seen Moved (sim-cluster (pulses sim-foundation) (cut-host) :workers workers))
  (val live (lfor p seen.after :if (is p.exit-code None) p))
  (assert (= (sorted (sfor p live p.worker)) ["w1" "w2"]) seen.after))


(defk start-late []
  {:pre [] :post [(: % Moved)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 止まったまま始まる worker の上の pulse を 10 秒後に読み、worker を起こして 10 秒後にもう一度読む。"
  (<- (Delay 10.0))
  (<- before tuple (ProcessesOf "pulse"))
  (<- started bool (StartWorker "late"))
  (<- (Delay 10.0))
  (<- after tuple (ProcessesOf "pulse"))
  (<- view dict (ReadCoordinator "/workers/late"))
  (Moved :answer (int started) :host "late" :before before :mid #() :after after :just-rows {} :later-rows {} :view view))


(deftest test-a-worker-that-starts-down-takes-the-job-only-after-it-is-started
  ;; starts-down: 後から加わる node。起こすまで job は置かれず(process が無い)、StartWorker の後に名乗って job を受ける。
  (<- seen Moved (sim-cluster (pulses sim-foundation) (start-late)
                              :workers #((SimWorker :name "late" :provides (frozenset ["cluster-net"]) :starts-down True))))
  (assert (= seen.before #()) seen.before)
  (assert (= seen.answer 1) seen.answer)
  (assert (= (lfor p seen.after p.worker) ["late"]) seen.after)
  (assert (is (. (get seen.after 0) exit-code) None) seen.after)
  (assert (get seen.view "alive") seen.view))


;; --- 期限つきの待ち(AwaitReadiness・AwaitJobProcess — 書きで起きる・#3053)------------------------------------

(defrecord Recovered
  "待つ effect だけで見た job 1 つの起こし直し: first = 宣言の後の準備・before = 最初の process・crashed = Crash の答え・after = 起こし
   直しの次の process・again = その後の準備。"
  (#^ (| ServiceReadiness ReadinessWaitExpired) first)
  (#^ (| JobProcessSeen JobProcessWaitExpired) before)
  (#^ int crashed)
  (#^ (| JobProcessSeen JobProcessWaitExpired) after)
  (#^ (| ServiceReadiness ReadinessWaitExpired) again)
  (#^ float first-wait-seconds))


(defk crash-and-await [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % Recovered)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: job name が Ready になるのを待ち、最初の process を待ち、Crash で落として次の process ともう一度の Ready を待つため(どの待ちも
   読み直しのループを書かない — 答えるのは cluster の handler)。"
  (<- asked int (now-epoch-ms))
  (<- first (| ServiceReadiness ReadinessWaitExpired) (AwaitReadiness name "Ready" seconds))
  (<- answered int (now-epoch-ms))
  (<- before (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess name #() seconds))
  (when (not (isinstance before JobProcessSeen))
    (raise (AssertionError (+ "最初の process が名乗られない: " (repr before)))))
  (<- n int (Crash name))
  (<- after (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess name #(before.pid) seconds))
  (<- again (| ServiceReadiness ReadinessWaitExpired) (AwaitReadiness name "Ready" seconds))
  (Recovered :first first :before before :crashed n :after after :again again
             :first-wait-seconds (/ (- answered asked) 1000.0)))


(deftest test-the-waits-see-a-crashed-service-restart-without-polling-the-clock
  ;; 待つ effect の答え: Ready になった時の準備・最初の process・落とした後の別の pid の process・もう一度の Ready。
  (<- seen Recovered (sim-cluster (beacons sim-foundation) (crash-and-await "beacon" 30.0)))
  (assert (and (isinstance seen.first ServiceReadiness) (= seen.first.state "Ready")) seen.first)
  ;; 書きで起きる: Ready の待ちは、coordinator が Ready を数えた書きの時に答え、期限(30 秒)まで眠らない — 起こしを外すと期限で
  ;; 起きてから読み直すので、ここが 30 秒になる。
  (assert (< seen.first-wait-seconds 20.0) seen.first-wait-seconds)
  (assert (= seen.crashed 1) seen)
  (assert (and (isinstance seen.after JobProcessSeen) (!= seen.after.pid seen.before.pid)) seen)
  (assert (and (isinstance seen.again ServiceReadiness) (= seen.again.state "Ready")) seen.again))


(defrecord Expired
  "起きない事を待った答え: never-ready = 来ない準備の状態の待ち・no-restart = 落としていない job の次の process の待ち。"
  (#^ (| ServiceReadiness ReadinessWaitExpired) never-ready)
  (#^ (| JobProcessSeen JobProcessWaitExpired) no-restart))


(defk await-what-never-comes [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % Expired)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(失敗ケース): Ready になった job name について、来ない状態(Missing)と、落としていないので起きない次の process を seconds 秒
   待つため — 期限で値が返り、黙って待ち続けない。"
  (<- (AwaitReadiness name "Ready" 30.0))
  (<- first (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess name #() 30.0))
  (when (not (isinstance first JobProcessSeen))
    (raise (AssertionError (+ "最初の process が名乗られない: " (repr first)))))
  (<- never (| ServiceReadiness ReadinessWaitExpired) (AwaitReadiness name "Missing" seconds))
  (<- none (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess name #(first.pid) seconds))
  (Expired :never-ready never :no-restart none))


(deftest test-a-wait-for-what-never-comes-answers-expired-at-its-deadline
  (<- seen Expired (sim-cluster (beacons sim-foundation) (await-what-never-comes "beacon" 6.0)))
  (assert (isinstance seen.never-ready ReadinessWaitExpired) seen.never-ready)
  (assert (= seen.never-ready.last.state "Ready") seen.never-ready)
  (assert (>= seen.never-ready.waited-seconds 6.0) seen.never-ready)
  (assert (isinstance seen.no-restart JobProcessWaitExpired) seen.no-restart)
  (assert (= seen.no-restart.waited-seconds 6.0) seen.no-restart))
