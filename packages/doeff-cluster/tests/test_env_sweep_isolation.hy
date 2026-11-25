;; worker の env の掃除(SweepEnvs の全部の片づけ)の失敗が、job を持つ worker の process を終わらせない形の検。
;;
;; 掃除の task(数え・選び・退け・残りの片づけ・mirror・venv・固定の file の書き・uv の cache の prune)の 1 つが例外で落ちた時、
;; 以前の env-host は FailPromise を SweepEnvs の sweep-answer で周期の Program に上げてしまい、worker の process ごと終了して
;; その worker の job を全部道連れにした。だから:
;;   - 掃除の全部の片づけを周期の外の task で走らせ、例外は失敗の結末(片づけの名と訳)として周期へ返し、worker の process も走っている
;;     job も止めない
;;   - 失敗した片づけの名(step)と訳(detail)を warning の log(SWEEP-FAILED-LOG)と worker の状態の報告(EnvReport → heartbeat の
;;     envSweepFailure → coordinator の Worker の資源の status と GET /state)に載せ、見張りが読めるようにする
;;   - 失敗した掃除はすぐには繰り返さず、間(SWEEP-RETRY-MS)を空けて撃ち直す。次の掃除が全部通れば失敗の報告を消す
;;   - 1 つの片づけ(例: mirror の掃除)が落ちても、同じ掃除の他の片づけ(古い root の消し)は進み、job は止まらない
;; 土台: 本番の調整ループ run-worker・process-host・env-host・台本の子 process・memory の file system・仮想の時計・coordinator の
;; responded と worker-row と state-view-json。
(require doeff-hy.macros [deff deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass replace])
(import httpx)
(import json)
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects [SlogEffect StopRequested state scheduled])
(import tests.stop_fixtures [stop-signal-never-comes])
(import doeff_core_effects [memory-file-handler MemoryFile MemoryFiles StatPath PathKind FileFailed])
(import doeff_core_effects [ProcessOutcome RunProcess timed-out-outcome ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms ENV-MARKER JobSpec CodeView CodeState WorldView WorkerPolicy
                       ReadDesired ObserveWorld EnvReport PublishStatus PrepareCode ReleaseLeases
                       ObserveProcesses ObserveEnvDisk process-host env-host tick-pauses
                       ClusterState ClusterTiming responded http-request])
(import doeff_cluster._shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster._worker.intent.worker_model [DesiredJobs EnvDisk])
(import doeff_cluster._worker.protocol.process_host [HostSettings STOP-TIMING-LOG])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster._worker.protocol.env_store)
(import doeff_cluster._worker.protocol.env_store [EnvSettings MeasuredRoots SWEEP-DONE-LOG SWEEP-FAILED-LOG])
(import doeff_cluster._worker.protocol.heartbeat [env-report env-heartbeat-part])
(import doeff_cluster._worker.core.env_upkeep [MIRROR-UNUSED-SECONDS SWEEP-RETRY-MS SweepFailure])
(import doeff_cluster._worker.core.program [run-worker])
(import doeff_cluster._coordinator.core.resource_policy [worker-row])
(import doeff_cluster._coordinator.core.cluster_policy [state-view])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import tests.host_rig [host-settings])
(import tests.link_rig [LinkRig])
(import tests.room_rows [ROOM-JSONS])
(import tests.wake_fixtures [wakes-every])

(val STATE "/state")
(val SPEC (JobSpec "svc" "jobs.svc" #() "rev-a"))
(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
(val FENCE-MS 20000)
(val ROOT-A "aaaaaaaaaaaaaaaaaaaaaaaa")
(val ROOT-B "bbbbbbbbbbbbbbbbbbbbbbbb")
(val ROOT-C "cccccccccccccccccccccccc")
(val ROOT-D "dddddddddddddddddddddddd")
(val ALL-ROOTS #(ROOT-A ROOT-B ROOT-C ROOT-D))
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}})
(val STALE-MIRROR "0123456789abcdef.git")
(val START-EPOCH-S 1_800_000_000.0)
(val T (ClusterTiming))


(defrecord FailedSweepLog
  "掃除の失敗の log 1 行の欄。"
  (#^ str level)
  (#^ str step)
  (#^ str detail)
  (#^ int at-ms))


(defrecord ReportedEnv
  "ReadDesired が受けた EnvReport 1 つと刻。"
  (#^ int at-ms)
  (#^ (| dict None) report))


(defrecord IsolationRun
  "筋書き 1 回の見え方: stop-reasons = job の止めの訳・failed-logs = 掃除の失敗の log・swept = 掃除の終わりの log の数・
   reports = ReadDesired が受けた EnvReport の列・widest-ms = heartbeat のいちばん広い間・left = 残っている root。"
  (#^ (get tuple #(str ...)) stop-reasons)
  (#^ (get tuple #(FailedSweepLog ...)) failed-logs)
  (#^ int swept)
  (#^ (get tuple #(ReportedEnv ...)) reports)
  (#^ int widest-ms)
  (#^ (get tuple #(str ...)) left))


(defeffect NotedSweepLogs
  "ここまでに出た止めの訳・掃除の失敗の log・掃除の終わりの数を読む — 検だけの問い。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedReports
  "ここまで ReadDesired が受けた EnvReport の列と heartbeat のいちばん広い間を読む — 検だけの問い。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler sweep-logs-noted
  "外の世界の log の代役: 止めの訳・掃除の失敗の log・掃除の終わりの log を覚える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var stops #())
  (session var failed #())
  (session var swept 0)
  (SlogEffect []
    (val fields effect.kwargs)
    (cond
      (= effect.msg STOP-TIMING-LOG)
        (:= stops (+ stops #((get fields "reason"))))
      (= effect.msg SWEEP-FAILED-LOG)
        (do (<- now int (now-epoch-ms))
            (:= failed (+ failed #((FailedSweepLog :level (str (.get fields "level" ""))
                                                   :step (str (.get fields "step" ""))
                                                   :detail (str (.get fields "detail" ""))
                                                   :at-ms now)))))
      (= effect.msg SWEEP-DONE-LOG)
        (:= swept (+ swept 1))
      True None)
    (resume None))
  (NotedSweepLogs []
    (resume #(stops failed swept))))


(defhandler isolation-world [#^ int stop-ms #^ tuple [extra-codes #()]]
  "模擬の宿: 毎拍 SPEC を宣言し、env-host が答える本物の EnvReport を ReadDesired で受けて控える(EnvReport は覆わない)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに止める刻と固定させる追加の観測を変える(Ask で区別できない)。
  (session var reports #())
  (session var last-ms None)
  (session var widest 0)
  (ReadDesired [env-report stopping]
    (<- now int (now-epoch-ms))
    (when (is-not last-ms None)
      (:= widest (max widest (- now last-ms))))
    (:= last-ms now)
    (:= reports (+ reports #((ReportedEnv :at-ms now :report env-report))))
    (resume (DesiredJobs #(SPEC))))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (if (>= now stop-ms) "signal 15" None)))
  (PublishStatus [statuses note] (resume None))
  (ReleaseLeases [job instance] (resume None))
  (PrepareCode [revision] (resume None))
  (NotedReports [] (resume #(reports widest)))
  (ObserveWorld []
    (<- processes tuple (ObserveProcesses))
    (<- disk EnvDisk (ObserveEnvDisk))
    (resume (WorldView (+ #((CodeView SPEC.revision CodeState.READY STATE)) extra-codes) processes :env-disk disk))))


(defk runs-until-stopped [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本の job の子 process: 止めるまで走り続ける。"
  (<- running ProcessOutcome (timed-out-outcome "" ""))
  running)


(defk world-files []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "memory の置き場に、完成した root 4 つ(c・d が消してよい)と 7 日より古い使われない bare mirror 1 つを置くため(memory の置き場は mtime 0 なので
   START-EPOCH-S では 7 日より古い)。"
  (val mirror-dir (+ STATE "/mirrors/" STALE-MIRROR))
  (MemoryFiles :free (** 2 40) :total (** 2 41)
               :dirs (+ #(STATE (+ STATE "/roots") (+ STATE "/mirrors") mirror-dir)
                        (tuple (gfor n ALL-ROOTS (+ STATE "/roots/" n))))
               :files (tuple (+ (lfor n ALL-ROOTS (MemoryFile :path (+ STATE "/roots/" n "/" ENV-MARKER)
                                                              :content (.encode (json.dumps MARKER))))
                                (lfor n ALL-ROOTS (MemoryFile :path (+ STATE "/roots/" n "/lib.py")
                                                              :content b"x = 1\n"))
                                [(MemoryFile :path (+ mirror-dir "/HEAD") :content b"ref: refs/heads/main\n")]))))


(defk left-roots []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "残っている root の名を返すため。"
  (var left #())
  (for [name ALL-ROOTS]
    (<- seen (StatPath (+ STATE "/roots/" name)))
    (when (and (not (isinstance seen FileFailed)) (= seen.kind PathKind.DIRECTORY))
      (:= left (+ left #(name)))))
  left)


(defk worker-run []
  {:pre [] :post [(: % IsolationRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループ run-worker を回し、止めの訳・掃除の失敗の log・EnvReport の列・残った root を返すため。"
  (<- (run-worker POLICY))
  (<- logs tuple (NotedSweepLogs))
  (<- rep tuple (NotedReports))
  (<- left tuple (left-roots))
  (IsolationRun :stop-reasons (get logs 0) :failed-logs (get logs 1) :swept (get logs 2)
                :reports (get rep 0) :widest-ms (get rep 1) :left left))


(defk run-isolated-worker [duration-ms [min-free 0] [extra-codes #()]]
  {:pre [(: duration-ms int) (: min-free int) (: extra-codes tuple)] :post [(: % IsolationRun)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "模擬の宿で run-worker を duration-ms ミリ秒回すため。"
  (<- host HostSettings (host-settings (Path STATE) :policy POLICY))
  (val script (ProcessScript :commands #((ScriptedCommand :name (. (Path host.python) name) :run runs-until-stopped)
                                         (ScriptedCommand :name "nice" :run runs-until-stopped))))
  (val settings (EnvSettings :state STATE :uv-cache (+ STATE "/uv-cache") :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :uv "no-uv" :roots-cap-bytes 0 :min-free-bytes min-free))
  (<- files MemoryFiles (world-files))
  (val start-ms (int (* START-EPOCH-S 1000)))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock (datetime-of-epoch-ms start-ms)))
                                  sweep-logs-noted (memory-file-handler files) (scripted-process-handler script)
                                  (process-host host) (env-host settings)
                                  (isolation-world (+ start-ms duration-ms) extra-codes) stop-signal-never-comes tick-pauses (wakes-every 1000)]
                                 (worker-run)))))


(deftest test-a-failing-mirror-sweep-keeps-the-worker-and-its-job-running-and-reports-the-failure [monkeypatch]
  ;; removed-mirror が TypeError で落ちても、worker の process も job も止まらず、
  ;; 失敗した掃除の名と訳が warning の log と worker の状態の報告(EnvReport → heartbeat → coordinator の Worker の status と GET /state)に出る。
  ;; 次の掃除は間(SWEEP-RETRY-MS)を空けて撃ち直され、他の片づけ(古い root c・d の消し)も進む。
  (val calls [])
  (defk broken-removed-mirror [settings name now-ms]
    {:pre [(: settings EnvSettings) (: name str) (: now-ms int)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
    "mirror の掃除で TypeError を投げる模擬のバグ。"
    (.append calls now-ms)
    (raise (TypeError "unsupported operand type(s) for +: 'Call' and 'str'")))
  (.setattr monkeypatch doeff_cluster._worker.protocol.env_store "removed_mirror" broken-removed-mirror)
  (<- got IsolationRun (run-isolated-worker 90000))
  ;; 1. job は途絶も墜落もせず、終わりの worker-stopping でだけ止まる。
  (assert (= got.stop-reasons #("worker-stopping" "worker-stopping")) got.stop-reasons)
  (assert (< got.widest-ms FENCE-MS) got.widest-ms)
  ;; 2. 同じ掃除の別の片づけ(古い root c・d の消し)は進んだ。
  (assert (= got.left #(ROOT-A ROOT-B)) got.left)
  ;; 3. 掃除の失敗の log が warning で、片づけの名と訳を載せて出た。
  (assert (>= (len got.failed-logs) 2) got.failed-logs)
  (for [entry got.failed-logs]
    (assert (= entry.level "warning") entry)
    (assert (= entry.step "remove-mirrors") entry)
    (assert (in "TypeError" entry.detail) entry)
    (assert (in "unsupported operand type(s)" entry.detail) entry))
  ;; 4. 次の掃除は間(SWEEP-RETRY-MS)を空けて撃ち直され、拍ごとの暴走にならない。
  (assert (<= 2 (len calls) 4) calls)
  (for [#(a b) (zip calls (cut calls 1 None))]
    (assert (>= (- b a) SWEEP-RETRY-MS) calls))
  ;; 5. worker の状態の報告(EnvReport → env-heartbeat-part → coordinator の Worker の status と GET /state)に失敗の step・detail・sinceMs が載る。
  (val failing-reports (lfor r got.reports :if (and (is-not r.report None) (in "sweepFailure" r.report)) r.report))
  (assert failing-reports got.reports)
  (val part (env-heartbeat-part (get failing-reports -1) "linux-x86_64"))
  (val wire-failure (.get part "envSweepFailure"))
  (assert (isinstance wire-failure dict) part)
  (assert (= (get wire-failure "step") "remove-mirrors") wire-failure)
  (assert (in "TypeError" (get wire-failure "detail")) wire-failure)
  (assert (isinstance (get wire-failure "sinceMs") int) wire-failure)
  ;; coordinator がその heartbeat を受け、GET /resources/Worker の status と GET /state の workers に載せる。
  (val hb-body (| {"name" "w-node-a" "provides" ["net"] "measured" (get ROOM-JSONS 2) "statuses" []} part))
  (<- req (http-request "POST" "/heartbeat" {} hb-body :actor "c-test"))
  (val after (get (responded (ClusterState) req 1000 T) 0))
  (val w-info (.row after.workers "w-node-a"))
  (val row-status (get (worker-row after w-info 1000 T) "status"))
  (assert (= (.get row-status "envSweepFailure") wire-failure) row-status)
  (<- view (state-view after 1000 T))
  (<- state-body (state-view-json view))
  (assert (= (.get (get (get state-body "workers") "w-node-a") "envSweepFailure") wire-failure) state-body))


(deftest test-a-failing-measure-sweep-keeps-the-job-running-and-clears-failure-on-recovery [monkeypatch]
  ;; 数え(root-infos)が 1 回目だけ例外で落ちても、worker と job は止まらず、失敗の報告を載せ、SWEEP-RETRY-MS の後の撃ち直しが通ると
  ;; 失敗の報告が消え、古い root c・d が消える。
  (val original doeff_cluster._worker.protocol.env_store.root-infos)
  (val attempts [])
  (defk flaky-root-infos [settings known]
    {:pre [(: settings EnvSettings) (: known tuple)] :post [(: % MeasuredRoots)] :tags {:context "doeff-cluster-test" :role "program"}}
    "1 回目だけ RuntimeError を投げ、2 回目から本物の数えを返すため。"
    (.append attempts 1)
    (when (= (len attempts) 1)
      (raise (RuntimeError "measure boom")))
    (<- res MeasuredRoots (original settings known))
    res)
  (.setattr monkeypatch doeff_cluster._worker.protocol.env_store "root_infos" flaky-root-infos)
  (<- got IsolationRun (run-isolated-worker 60000))
  ;; job は止まらない。
  (assert (= got.stop-reasons #("worker-stopping" "worker-stopping")) got.stop-reasons)
  ;; 1 回目の失敗が log と状態の報告に出る。
  (assert (= (len got.failed-logs) 1) got.failed-logs)
  (val first-fail (get got.failed-logs 0))
  (assert (= first-fail.step "measure-roots") first-fail)
  (assert (= first-fail.detail "RuntimeError: measure boom") first-fail)
  (val saw-failure (any (gfor r got.reports
                              (and (is-not r.report None)
                                   (= (.get (.get r.report "sweepFailure" {}) "step") "measure-roots")))))
  (assert saw-failure got.reports)
  ;; 2 回目の撃ち直しが通り、SWEEP-DONE-LOG が出て、古い root が消え、最後の状態の報告からは sweepFailure が消える。
  (assert (>= got.swept 1) got)
  (assert (= got.left #(ROOT-A ROOT-B)) got.left)
  (val last-report (. (get got.reports -1) report))
  (assert (and (is-not last-report None) (not-in "sweepFailure" last-report)) last-report))


(deftest test-failures-in-choose-set-aside-pins-and-prune-never-kill-the-worker [monkeypatch]
  ;; 選び(root-users)・退け(set-aside)・固定の書き(wrote-pins)・uv prune(pruned-after)のどれで例外が起きても、
  ;; worker も job も止まらず、落ちた片づけの名と訳が log と EnvReport に出る。
  (val pin-code #((CodeView (+ "env-" ROOT-A) CodeState.PREPARING None)))
  (for [#(attr expected-step min-free extra-codes) #(#("root_users" "choose-roots" 0 #())
                                                     #("set_aside" "set-aside-roots" 0 #())
                                                     #("wrote_pins" "write-pins" 0 pin-code)
                                                     #("pruned_after" "prune-cache" (** 2 45) #()))]
    (defk boom [#* args #** kwargs]
      {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
      "指定の片づけで例外を投げるため。"
      (raise (ValueError (.format "boom in {}" expected-step))))
    (.setattr monkeypatch doeff_cluster._worker.protocol.env_store attr boom)
    (<- got IsolationRun (run-isolated-worker 20000 :min-free min-free :extra-codes extra-codes))
    (.undo monkeypatch)
    (assert (= got.stop-reasons #("worker-stopping" "worker-stopping")) #(expected-step got.stop-reasons))
    (assert (any (gfor e got.failed-logs (and (= e.step expected-step) (in "ValueError" e.detail)))) #(expected-step got.failed-logs))
    (assert (any (gfor r got.reports
                       (and (is-not r.report None)
                            (= (.get (.get r.report "sweepFailure" {}) "step") expected-step))))
            #(expected-step got.reports))))


(deftest test-coordinator-reports-and-clears-env-sweep-failure-without-churning-version
  ;; healthy な worker には envSweepFailure を載せず、失敗を名乗った worker だけ status と GET /state に載せ、
  ;; 同じ失敗の繰り返しで Worker の status が揺れず、回復の heartbeat で消える。
  (val failure {"step" "remove-mirrors" "detail" "TypeError: bad" "sinceMs" 500})
  (<- healthy-req (http-request "POST" "/heartbeat" {} {"name" "w1" "provides" ["net"] "measured" (get ROOM-JSONS 2) "statuses" []} :actor "c-test"))
  (val s1 (get (responded (ClusterState) healthy-req 1000 T) 0))
  (val row1 (worker-row s1 (.row s1.workers "w1") 1000 T))
  (assert (not-in "envSweepFailure" (get row1 "status")) row1)
  ;; 失敗を名乗る heartbeat
  (<- fail-req (http-request "POST" "/heartbeat" {} {"name" "w1" "provides" ["net"] "measured" (get ROOM-JSONS 2) "statuses" []
                                                     "envSweepFailure" failure} :actor "c-test"))
  (val s2 (get (responded s1 fail-req 2000 T) 0))
  (val row2 (worker-row s2 (.row s2.workers "w1") 2000 T))
  (assert (= (.get (get row2 "status") "envSweepFailure") failure) row2)
  ;; 同じ失敗の 2 回目の heartbeat で status の形は同じ(版を進めない)。
  (val s3 (get (responded s2 fail-req 3000 T) 0))
  (val row3 (worker-row s3 (.row s3.workers "w1") 3000 T))
  (assert (= row2 row3) #(row2 row3))
  ;; 回復(envSweepFailure 無し)で status と GET /state から消える。
  (val s4 (get (responded s3 healthy-req 4000 T) 0))
  (val row4 (worker-row s4 (.row s4.workers "w1") 4000 T))
  (assert (not-in "envSweepFailure" (get row4 "status")) row4)
  (<- view4 (state-view s4 4000 T))
  (<- state4 (state-view-json view4))
  (assert (not-in "envSweepFailure" (get (get state4 "workers") "w1")) state4))


(deftest test-coordinator-link-sends-env-sweep-failure-in-heartbeat [tmp-path]
  ;; coordinator への口(coordinator-link)は EnvReport の sweepFailure を heartbeat の envSweepFailure として送り、
  ;; 掃除が通っている時は欄を載せない。
  (val sent [])
  (deff handle [#^ httpx.Request request]  ; cannot be a defk: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、空の宣言で答えるため。"
    (.append sent (json.loads request.content))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val link (LinkRig "http://coord" "w-node-a" #() 60000 :task-dir (str (/ tmp-path "tasks"))
                     :handles-envs True :transport (httpx.MockTransport handle)))
  (setv link.state.env-report (env-report #() "ok" (frozenset)))
  (.poll link)
  (assert (not-in "envSweepFailure" (get sent 0)) (get sent 0))
  (val fail (SweepFailure :step "remove-mirrors" :detail "TypeError: boom" :since-ms 12345 :at-ms 12345))
  (setv link.state.env-report (env-report #() "ok" (frozenset) (frozenset) fail))
  (.poll link)
  (assert (= (.get (get sent 1) "envSweepFailure") {"step" "remove-mirrors" "detail" "TypeError: boom" "sinceMs" 12345}) (get sent 1)))
