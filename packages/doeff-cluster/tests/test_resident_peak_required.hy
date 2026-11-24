;; 常駐の job の memory の宣言の山(peakMiB・settleMiB)は、宣言の口だけでなく保存の読み直しと worker の spec の受けでも必須(2026-11-15)。
;;
;; 失敗ケース: worker の memory の見張りは、常駐の job の基準の大きさ(平常の大きさと宣言の山の大きい方)が無い時、その job を止める
;; 候補にした。worker の起動の直後は平常の大きさが空なので、宣言の山を持たない常駐の job は、機体が DANGER に入ると平常の大きさで
;; 動いていても止められうる。宣言の山を持たない常駐の job が生まれる道は、coordinator の保存の読み直し(job-from-json)が旧い
;; coordinator の保存した欄の無い行を peak-mib None の job として受けて置く事だけだった(送り手の書く口と defsystem は既に断る)。
;;   - 保存の読み直しは、欄の無い行を置かずに理由つきで止める(RefusedJob — 黙って既定の値で埋めない)。宣言し直しで置き換わる。
;;   - worker は、peakMiB の無い常駐の job の行を持つ heartbeat の返事を読めない返事として断る(版を入れ替えない)。
;;   - 見張りは、宣言の在る常駐の job を必ず基準の大きさで判じる(基準の無い枝を持たない)。退いた世代の改名した process は退く前の
;;     job の宣言で判じる。宣言の外の process(宣言から外れて止まる途中の process)は task と同じく候補。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-cluster.macros [defsystem])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects [state MemoryFiles memory-file-handler ProcRow scripted-proc-table-handler])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster [ClusterTiming JobSpec DeclaredReply sim-cluster SimWorker ReadCoordinator ProcessesOf StopWorker StartWorker])
(import doeff_cluster._shared.intent.protocol [BodyInvalid])
(import doeff_cluster._coordinator.core.cluster_policy [job-from-json job-to-json])
(import doeff_cluster._coordinator.protocol.state_json [read-service-rows])
(import doeff_cluster._worker.protocol.declared [declared-job-spec declared-job-specs declared-reply-of-json DeclaredReplyMalformed])
(import doeff_cluster._worker.core.memory_rules [JobRoot JobSteady KillUnit MemoryLimits MIB UnitKind _job-uses _kill-units
                                                declared-roots victim-of])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell MEMORY-KILL-LOG MEMORY-NO-VICTIM-LOG MEMORY-SWARM-KILL-LOG])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulse-program])
(import tests.program_rows [SAMPLE-RUN])
(import tests.test_declared_memory_spared [dispatch-rows])
(import tests.test_memory_watch [row node-files memory-world watch-then-seen lines-named WatchSeen])


(val ROW {"name" "writer" "revision" "abc" "needs" ["net"] "run" SAMPLE-RUN "peakMiB" 600 "settleMiB" 300})


(deftest test-a-stored-row-without-the-memory-fields-is-refused-with-its-reason
  ;; 欄の在る行は今どおり読めて、書き戻しは欄を必ず書く。
  (val job (job-from-json ROW))
  (assert (= #(job.peak-mib job.settle-mib) #(600 300)) job)
  (val spelled (job-to-json job))
  (assert (= #((get spelled "peakMiB") (get spelled "settleMiB")) #(600 300)) spelled)
  ;; 旧い coordinator の保存した行(欄の無い行・片方だけの行)は ClusterJob にならない。
  (val bare (dfor #(k v) (.items ROW) :if (not-in k #("peakMiB" "settleMiB")) k v))
  (for [row [bare (| bare {"peakMiB" 600}) (| bare {"settleMiB" 300}) (| bare {"peakMiB" None "settleMiB" None})]]
    (with [raised (pytest.raises BodyInvalid)]
      (job-from-json row))
    (assert (in "peakMiB" (str raised.value)) raised.value))
  ;; 保存の読み直しは欄の無い行を置かず、理由つきの RefusedJob にする(落とさない・既定の値で埋めない)。
  (val read (read-service-rows [ROW (| bare {"name" "legacy"})]))
  (val jobs (get read 0))
  (val refused (get read 1))
  (assert (= (tuple (gfor j jobs j.spec.name)) #("writer")) jobs)
  (val legacy (.row refused "legacy"))
  (assert (is-not legacy None) refused)
  (assert (in "peakMiB" legacy.reason) legacy.reason)
  (assert (= (.get legacy.row "name") "legacy") legacy.row))


(val WIRE {"name" "writer" "entry" "m" "revision" "rev" "peakMiB" 600})


(deftest test-the-worker-refuses-a-resident-job-row-without-its-declared-peak
  (<- read JobSpec (declared-job-spec WIRE False))
  (assert (= read.peak-mib 600) read)
  ;; 欄の無い行・整数でない行・負の行は、宣言なしとして受けずに、読めない返事として断る。
  (for [bad [{} {"peakMiB" None} {"peakMiB" True} {"peakMiB" -1} {"peakMiB" "600"}]]
    (val row (| (dfor #(k v) (.items WIRE) :if (!= k "peakMiB") k v) bad))
    (with [raised (pytest.raises DeclaredReplyMalformed)]
      (! (declared-job-spec row False)))
    (assert (in "peakMiB" (str raised.value)) raised.value))
  ;; 返事の読みでも同じ — 1 行でも欄が無ければ返事ごと読めない(版を入れ替えない)。
  (<- reply DeclaredReply (declared-reply-of-json {"jobs" [WIRE (dfor #(k v) (.items WIRE) :if (!= k "peakMiB") k v)] "draining" False}))
  (with [(pytest.raises DeclaredReplyMalformed)]
    (! (declared-job-specs reply))))


(defk dispatch-victim-of [body-mib declared steadies [root (JobRoot :name "dispatch" :pid 60)]]
  {:pre [(: body-mib int) (: declared dict) (: steadies tuple) (: root JobRoot)] :post [(: % (| KillUnit None))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "dispatch の本体が body-mib・宣言の山の表が declared(名 → byte)・平常の大きさが steadies・根が root(pid 60)の時に、見張りが止める
   単位を選ぶため。"
  (<- rows tuple (dispatch-rows body-mib))
  (<- roots tuple (declared-roots #(root) declared))
  (<- units tuple (_kill-units rows roots))
  (<- uses tuple (_job-uses rows roots))
  (<- victim (| KillUnit None) (victim-of units uses steadies (MemoryLimits)))
  victim)


(deftest test-a-process-outside-the-declarations-is-a-candidate-and-a-declared-job-is-judged-by-its-base
  ;; 宣言の在る常駐の job は、平常の大きさが無くても宣言の山を基準に判じる(1.5 倍未満なら止めない・以上なら止める)。
  (<- spared (| KillUnit None) (dispatch-victim-of 700 {"dispatch" (* 565 MIB)} #()))
  (assert (is spared None) spared)
  (<- jumped (| KillUnit None) (dispatch-victim-of 900 {"dispatch" (* 565 MIB)} #()))
  (assert (= #(jumped.job jumped.kind) #("dispatch" UnitKind.LEAF)) jumped)
  ;; 退いた世代の改名した process は、退く前の job の宣言の山で判じる(入れ替えの間も宣言の 1.5 倍未満なら止めない)。
  (val retired (JobRoot :name "dispatch#retired-g1" :pid 60 :retired-from "dispatch"))
  (<- kept (| KillUnit None) (dispatch-victim-of 700 {"dispatch" (* 565 MIB)} #() retired))
  (assert (is kept None) kept)
  ;; 宣言の外の process(返事の宣言に名の無い根 — 宣言から外れて止まる途中の process)は、平常の大きさが在っても task と同じく候補。
  (<- outside (| KillUnit None) (dispatch-victim-of 700 {} #((JobSteady :job "dispatch" :bytes (* 1900 MIB)))))
  (assert (= #(outside.job outside.kind) #("dispatch" UnitKind.LEAF)) outside))


(defsystem peak-declared-svc [#^ Callable foundation]
  "模擬の世界で worker を起こし直す検の系: 宣言の山 600 MiB を持つ常駐の job svc 1 つ。"
  (svc (pulse-program foundation) :replicas 1 :peak-mib 600 :settle-mib 300 :needs #{"cluster-net"} :readiness {"windowSeconds" 5}))


(defk svc-rows [body-mib]
  {:pre [(: body-mib int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "常駐の job svc(shim 10 → uv 11 → 本体 12 — 本体は body-mib)の表を作るため。svc の和 = body-mib + 15 MiB。"
  (<- tini ProcRow (row 1 0 1 "tini"))
  (<- shim ProcRow (row 10 1 10 "python"))
  (<- uv ProcRow (row 11 10 5 "uv"))
  (<- body ProcRow (row 12 11 body-mib "hy"))
  #(tini shim uv body))


(defk restart-worker-and-read-state []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker w1 を止めて新しい世代で起こし直し、起き直した後の coordinator の GET /state と svc の process の履歴を返すため。"
  (<- (Delay 6.0))
  (<- before tuple (ProcessesOf "svc"))
  (<- (StopWorker "w1"))
  (<- restarted bool (StartWorker "w1"))
  (<- (Delay 10.0))
  (<- after tuple (ProcessesOf "svc"))
  (<- st dict (ReadCoordinator "/state"))
  #(restarted before after st))


(defk watch-fresh-generation-in-danger [body-mib peak-mib]
  {:pre [(: body-mib int) (: peak-mib int)] :post [(: % WatchSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "起き直した直後の worker(平常の大きさが空の新しい MemoryCell・eviction.declared に coordinator の宣言の山 peak-mib を持つ)で、
   DANGER の機体(空き 1.0 GiB / 32 GiB)に対して本物の memory-watch-loop(guard=True)を回すため。"
  (<- rows tuple (svc-rows body-mib))
  (<- files MemoryFiles (node-files 1.0))
  (val cell (MemoryCell))
  (setv cell.eviction.declared {"svc" (* peak-mib MIB)})
  (<- seen WatchSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (scripted-proc-table-handler rows)
                                     (memory-world cell 0 4000)]
                       (watch-then-seen (MemoryWatchSettings) cell)))
  seen)


(deftest test-a-restarted-worker-in-sim-cluster-carries-declared-peak-and-spares-the-resident-job-in-danger
  ;; 1. 模擬の世界(sim-cluster)で worker w1 を止めて起こし直し、起き直し後も svc が動いており、coordinator の GET /state の
  ;;    全 job が peakMiB を持つ事を確かめる。
  (val timing (ClusterTiming))
  (val workers #((SimWorker :name "w1" :provides #{"cluster-net"})))
  (<- outcome tuple (sim-cluster :notice-broker (MemoryBroker) (peak-declared-svc sim-foundation)
                                 (restart-worker-and-read-state) :timing timing :workers workers))
  (val restarted (get outcome 0))
  (val before (get outcome 1))
  (val after (get outcome 2))
  (val st (get outcome 3))
  (assert (is restarted True) restarted)
  (val live-before (tuple (gfor p before :if (is p.exit-code None) p)))
  (val live-after (tuple (gfor p after :if (is p.exit-code None) p)))
  (assert (= (len live-before) 1) before)
  (assert (= (len live-after) 1) after)
  (assert (!= (. (get live-before 0) instance) (. (get live-after 0) instance)) #(live-before live-after))
  ;; GET /state の全 job に peakMiB と settleMiB が載っている(欠け 0 本)。
  (val jobs (get st "jobs"))
  (val rows (if (isinstance jobs dict) (tuple (.values jobs)) (tuple jobs)))
  (assert (> (len rows) 0) st)
  (assert (= (len (tuple (gfor j rows :if (or (is (.get j "peakMiB") None) (is (.get j "settleMiB") None)) j))) 0) rows)
  (val svc-row (if (isinstance jobs dict) (get jobs "svc") (next (gfor j rows :if (= (.get j "name") "svc") j))))
  (val peak-mib (get svc-row "peakMiB"))
  (assert (= peak-mib 600) svc-row)
  ;; 2. 起き直した直後の worker(平常の大きさが空・eviction.declared に peakMiB=600 を持つ)が DANGER(1.0/32 GiB)に入っても、
  ;;    宣言の内の svc(565 MiB)と、宣言を少し越えた 1.5 倍未満の svc(715 MiB < 900 MiB)は止めない。
  (<- within WatchSeen (watch-fresh-generation-in-danger 550 peak-mib))
  (<- within-kills tuple (lines-named within.lines MEMORY-KILL-LOG))
  (<- within-swarms tuple (lines-named within.lines MEMORY-SWARM-KILL-LOG))
  (<- within-spared tuple (lines-named within.lines MEMORY-NO-VICTIM-LOG))
  (assert (= #(within-kills within-swarms) #(#() #())) within.lines)
  (assert (= (len within-spared) 1) within.lines)
  (assert (= (get (get within-spared 0) "spared_declared") "svc=565/600") within-spared)
  (assert (= (len within.table) 4) within.table)
  (<- under-ratio WatchSeen (watch-fresh-generation-in-danger 700 peak-mib))
  (<- under-kills tuple (lines-named under-ratio.lines MEMORY-KILL-LOG))
  (<- under-swarms tuple (lines-named under-ratio.lines MEMORY-SWARM-KILL-LOG))
  (<- under-spared tuple (lines-named under-ratio.lines MEMORY-NO-VICTIM-LOG))
  (assert (= #(under-kills under-swarms) #(#() #())) under-ratio.lines)
  (assert (= (len under-spared) 1) under-ratio.lines)
  (assert (= (get (get under-spared 0) "spared_steady") "svc=715/600") under-spared)
  (assert (= (len under-ratio.table) 4) under-ratio.table)
  ;; 3. 対照: 宣言の山の 1.5 倍(900 MiB)以上に跳ねた svc(965 MiB)は、起き直しの直後でも見張りが止める。
  (<- over-ratio WatchSeen (watch-fresh-generation-in-danger 950 peak-mib))
  (<- over-kills tuple (lines-named over-ratio.lines MEMORY-KILL-LOG))
  (assert (= (len over-kills) 1) over-ratio.lines)
  (assert (= (get (get over-kills 0) "job") "svc") over-kills))
