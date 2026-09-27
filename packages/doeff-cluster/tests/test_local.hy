;; 手元の runner sim-cluster(doeff_cluster.local — ADR-DOE-CLUSTER-001・計画 2.6・10.2・段 5)。
;;
;; sim の土台(tests.fixtures.envs の sim-foundation)で作った系の値を、本物の coordinator(emulated-handlers)と本物の run-worker(偽の宿)の
;; 上で、仮想の時計で走らせる。検の筋書き(scenario)は同じ scheduler・同じ時計で並んで走り、検の effect(Crash・Redeclare・ReportsOf・
;; ReadinessOf・ProcessesOf・SharedRows)で世界を動かし・読む。時間を進めるのは Delay。
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
(import doeff_cluster.local [sim-cluster sim-process SimChild EndProcess SimWorker SimProcess SimReport SimReadiness
                             Crash Redeclare ReportsOf ReadinessOf ProcessesOf SharedRows])
(import doeff_cluster.service_model [System CallShape job system-of])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons beacons-v2 handoff-beacons handoff-beacons-v2 relay flavors fenced gpu-only
                                    holding-unloadable Unloadable])


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
  (with [(pytest.raises TypeError)]
    (<- (sim-cluster (beacons sim-foundation) (watch-beacon 1) :environ {"beacon" {"STEP" 7}}))))


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
  (val child (SimChild :ctx ctx :program-path "" :environ {} :queue (RequestQueue) :pid 7))
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
