;; 手元の runner sim-cluster(doeff_cluster.local — ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5)。
;;
;; sim の土台(tests.fixtures.envs の sim-foundation)で作った系の値を、本物の coordinator(emulated-handlers)と本物の run-worker(偽の宿)の
;; 上で、仮想の時計で走らせる。検の筋書き(scenario)は同じ scheduler・同じ時計で並んで走り、検の effect(Crash・Redeclare・ReportsOf・
;; ReadinessOf・ProcessesOf・SharedRows・ReadCoordinator・StopCoordinator・CrashCoordinator・CoordinatorRuns・KillWorker・StopWorker・
;; StartWorker・CutWorker・DrainWorker)で世界を動かし・読む。時間を進めるのは Delay。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import pytest)
(import doeff [with-handlers])
(import doeff_time [Delay])
(import doeff_cluster.coordinator_handler_sets [RequestQueue])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.remote_model [UnsendableProgram TaskFailed decode-outcome])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.local [sim-cluster sim-process SimChild SimLink EndProcess SimWorker SimProcess SimReport SimReadiness
                             SimCoordinatorRun Crash Redeclare ReportsOf ReadinessOf ProcessesOf SharedRows ReadCoordinator
                             StopCoordinator CrashCoordinator CoordinatorRuns KillWorker StopWorker StartWorker CutWorker DrainWorker])
(import doeff_cluster.service_model [System CallShape job system-of])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons beacons-v2 handoff-beacons handoff-beacons-v2 relay flavors fenced gpu-only
                                    holding-unloadable Unloadable spawners quitters pulses detaching])


(defrecord Seen
  "筋書きが読んだ job 1 つの姿: coordinator の ready・届いた報告・process・盤の行。"
  (#^ SimReadiness readiness)
  (#^ tuple reports)
  (#^ tuple processes)
  (#^ dict rows))


(defk seen-of [name prefix]
  {:pre [(: name str) (: prefix str)] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの読み: job name の ready・報告・process と、盤の prefix の行を読む。"
  (<- readiness SimReadiness (ReadinessOf name))
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
  (assert (= changed.after.readiness.state "Ready") changed.after.readiness))


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
  (#^ SimReadiness passer)
  (#^ tuple peeker))


(defk watch-fence []
  {:pre [] :post [(: % Fenced)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 10 秒待って、passer の盤の行と ready・peeker の process を読む。"
  (<- (Delay 10.0))
  (<- rows dict (SharedRows "fence/"))
  (<- passer SimReadiness (ReadinessOf "passer"))
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
                       :link (SimLink :queue (RequestQueue) :actor "svc" :revision "r" :peer "w")))
  (<- (with-handlers [(end-recorder ends)]
        (sim-process "w" (JobSpec "svc" "doeff_cluster.job_entry" #("service") "r" :program (* "a" 64)) child None)))
  (<- (with-handlers [(end-recorder ends)]
        (sim-process "w" (JobSpec "task/t1" "doeff_cluster.job_entry" #("task") "r" :once True :program (* "b" 64)) child None)))
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
  (#^ SimReadiness during)
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
  (<- during SimReadiness (ReadinessOf "beacon"))
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
  (assert (> (get seen.after.rows "beacon/a" "n") 0) seen.after.rows))


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
  ;; 持たない job を止める(-15)。coordinator は移し替えの時間の後に、網のつながった worker へ置く。
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
