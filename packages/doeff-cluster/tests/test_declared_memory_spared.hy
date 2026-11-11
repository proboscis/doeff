;; 宣言の memory の内で動く常駐の job を、機体の memory の見張りが止めない(2026-11-11)。
;;
;; 失敗ケース: 見張りは常駐の job の単位を、今の memory が平常の大きさ(観測の最大)の steady-ratio 1.5 倍以上の時か、平常の大きさが
;; まだ無い時に止める候補にする。平常の大きさは観測から決まるので、暇な時間(約 300 MiB)の後に宣言の CLI を job の process の中で
;; 走らせる job(CLI の間 約 2 GiB — 宣言の peak-mib の内)は候補になり、DANGER の機体で止められうる。
;;   - coordinator は宣言の peak-mib を heartbeat の返事の job の行(peakMiB)で worker へ運び、worker は JobSpec の比べない欄に読む
;;     (指紋 spec-hash に入らない)。欄の無い返事(旧い coordinator)は宣言なし。
;;   - 宣言の内の常駐の job は、平常の大きさの 2 倍でも・平常の大きさが無くても止める候補でない。宣言を越えた job は今どおり。
;;   - 群れの止めも、宣言の内の job には当てない。止める単位が無い行は、宣言の内なので止めなかった job を名指す。
;;
;; 失敗ケース(2026-11-11 19:14〜19:21 UTC): 平常の大きさは worker の process の中だけに在り、worker の起動で空になる。平常の大きさが
;; まだ無い job は、宣言の山を少しでも越えていれば止める候補だったので、宣言 565 MiB で 591 MiB の常駐の job が、worker の起動の直後の
;; DANGER の機体で 7 分に 5 回止められ、起こし直しが止まった(止めた間の最大は平常に残さないので、平常の大きさを覚えられない)。
;;   - 宣言を越えた job は、平常の大きさと宣言の山の大きい方(基準の大きさ)の steady-ratio 倍以上の時だけ止める候補。
;;   - 宣言も平常の大きさも無い job は今どおり止める候補。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFiles memory-file-handler])
(import doeff_core_effects [ProcRow scripted-proc-table-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster [JobSpec])
(import doeff_cluster [ClusterJob ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [Placement])
(import doeff_cluster._coordinator.core.cluster_policy [jobs-for])
(import doeff_cluster._coordinator.protocol.replies [spec-json])
(import doeff_cluster._worker.protocol.declared [declared-job-spec])
(import doeff_cluster._worker.core.memory_rules [JobRoot JobSteady KillUnit MemoryLimits MIB UnitKind _job-uses _kill-units
                                                declared-roots spared-declared spared-steady victim-of])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell MEMORY-NO-VICTIM-LOG MEMORY-SWARM-KILL-LOG])
(import tests.state_rows [table-from-rows])
(import tests.test_memory_watch [row swarm-rows swarm-files memory-world watch-then-seen lines-named WatchSeen])


(defk dispatch-rows [body-mib]
  {:pre [(: body-mib int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "常駐の job dispatch(shim 60 → uv 61 → 本体 62 — 本体は body-mib)の表を作るため。dispatch の和 = body-mib + 15 MiB。"
  (<- shim ProcRow (row 60 1 10 "python"))
  (<- uv ProcRow (row 61 60 5 "uv"))
  (<- body ProcRow (row 62 61 body-mib "hy"))
  #(shim uv body))


(defk dispatch-victim [body-mib declared-mib steadies]
  {:pre [(: body-mib int) (: declared-mib (| int None)) (: steadies tuple)] :post [(: % (| KillUnit None))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "dispatch の本体が body-mib・宣言の memory の山が declared-mib(None = 宣言なし)・平常の大きさが steadies の時に、見張りが止める単位を
   選ぶため。"
  (<- rows tuple (dispatch-rows body-mib))
  (<- roots tuple (declared-roots #((JobRoot :name "dispatch" :pid 60))
                                  (if (is declared-mib None) {} {"dispatch" (* declared-mib MIB)})))
  (<- units tuple (_kill-units rows roots))
  (<- uses tuple (_job-uses rows roots))
  (<- victim (| KillUnit None) (victim-of units uses steadies (MemoryLimits)))
  victim)


(val STEADY-300 #((JobSteady :job "dispatch" :bytes (* 315 MIB))))


(deftest test-the-declared-peak-rides-the-job-row-to-the-worker-and-is-not-compared
  ;; coordinator の行 → 返事の job の行 → worker の JobSpec。宣言の山だけが違う spec は同じ process の形(起こし直さない)。
  (val job (ClusterJob (JobSpec "dispatch" "m" #() "rev") :peak-mib 2600 :settle-mib 300))
  (val state (ClusterState #(job) (! (table-from-rows {})) (! (table-from-rows {"dispatch" (Placement "dispatch" "w1" 1 0)}))))
  (val specs (jobs-for state "w1"))
  (assert (= (tuple (gfor s specs s.peak-mib)) #(2600)) specs)
  (<- spelled dict (spec-json (get specs 0)))
  (assert (= (get spelled "peakMiB") 2600) spelled)
  (<- read JobSpec (declared-job-spec spelled False))
  (assert (= read.peak-mib 2600) read)
  (assert (= read (replace read :peak-mib 300)))
  ;; 欄の無い返事(旧い coordinator)と、整数でない欄は宣言なし。宣言の無い spec の行は欄を書かない。
  (<- old JobSpec (declared-job-spec {"name" "dispatch" "entry" "m" "revision" "rev"} False))
  (<- odd JobSpec (declared-job-spec {"name" "dispatch" "entry" "m" "revision" "rev" "peakMiB" True} False))
  (assert (= #(old.peak-mib odd.peak-mib) #(None None)))
  (<- plain dict (spec-json old))
  (assert (not-in "peakMiB" plain) plain))


(deftest test-a-resident-job-inside-its-declared-peak-is-not-a-candidate
  ;; 平常 315 MiB の dispatch が 2015 MiB(平常の 6 倍): 宣言 2600 MiB の内なら止めない。宣言が無ければ今どおり止める。
  (<- spared (| KillUnit None) (dispatch-victim 2000 2600 STEADY-300))
  (assert (is spared None) spared)
  (<- killed (| KillUnit None) (dispatch-victim 2000 None STEADY-300))
  (assert (= #(killed.job killed.kind killed.head.pid) #("dispatch" UnitKind.LEAF 62)) killed)
  ;; 平常の大きさがまだ無くても、宣言の内なら止めない。
  (<- fresh (| KillUnit None) (dispatch-victim 2000 2600 #()))
  (assert (is fresh None) fresh)
  ;; 止める単位が無い時の log に名指す job。
  (<- rows tuple (dispatch-rows 2000))
  (<- roots tuple (declared-roots #((JobRoot :name "dispatch" :pid 60)) {"dispatch" (* 2600 MIB)}))
  (<- uses tuple (_job-uses rows roots))
  (<- named tuple (spared-declared uses (MemoryLimits)))
  (assert (= (tuple (gfor u named #(u.job (// u.bytes MIB) (// u.declared-bytes MIB)))) #(#("dispatch" 2015 2600))) named))


(deftest test-a-resident-job-beyond-its-declared-peak-is-judged-by-its-steady-size
  ;; 宣言 1000 MiB を越えた 2015 MiB の dispatch は今どおり平常の大きさで判じる: 平常 315 MiB の 1.5 倍以上なので止める。
  (<- killed (| KillUnit None) (dispatch-victim 2000 1000 STEADY-300))
  (assert (= #(killed.job killed.head.pid) #("dispatch" 62)) killed)
  ;; 宣言を越えても平常の大きさの内(平常 1900 MiB)なら、今どおり止めない。
  (<- steady (| KillUnit None) (dispatch-victim 2000 1000 #((JobSteady :job "dispatch" :bytes (* 1900 MIB)))))
  (assert (is steady None) steady))


(deftest test-a-resident-job-just-beyond-its-declared-peak-is-not-a-candidate-while-no-steady-size-is-known
  ;; worker の起動の直後(平常の大きさが空): 宣言 565 MiB で 591 MiB(宣言の 1.05 倍)の dispatch は止めない。
  (<- fresh (| KillUnit None) (dispatch-victim 576 565 #()))
  (assert (is fresh None) fresh)
  ;; 暇な時間の後(平常 315 MiB が宣言より小さい)も、宣言の山の 1.5 倍未満なら止めない。
  (<- idle (| KillUnit None) (dispatch-victim 576 565 STEADY-300))
  (assert (is idle None) idle)
  ;; 宣言の山の 1.5 倍(848 MiB)以上に跳ねた物は、平常の大きさが無くても止める。
  (<- jumped (| KillUnit None) (dispatch-victim 900 565 #()))
  (assert (= #(jumped.job jumped.kind jumped.head.pid) #("dispatch" UnitKind.LEAF 62)) jumped)
  ;; 宣言も平常の大きさも無い job は今どおり止める。
  (<- unknown (| KillUnit None) (dispatch-victim 576 None #()))
  (assert (= #(unknown.job unknown.head.pid) #("dispatch" 62)) unknown)
  ;; 止める単位が無い時の log に、基準の大きさ(宣言の山)と一緒に名指す job。
  (<- rows tuple (dispatch-rows 576))
  (<- roots tuple (declared-roots #((JobRoot :name "dispatch" :pid 60)) {"dispatch" (* 565 MIB)}))
  (<- units tuple (_kill-units rows roots))
  (<- uses tuple (_job-uses rows roots))
  (<- named tuple (spared-steady units uses #() (MemoryLimits)))
  (assert (= (tuple (gfor s named #(s.job (// s.bytes MIB)))) #(#("dispatch" 565))) named))


(deftest test-a-task-is-a-candidate-whatever-the-declarations-say
  ;; task は宣言の内に数えない(宣言の名は常駐の job の物 — 名が同じでも task の単位は今どおり止める)。
  (<- shim ProcRow (row 70 1 10 "python"))
  (<- body ProcRow (row 71 70 700 "python"))
  (<- roots tuple (declared-roots #((JobRoot :name "task/7" :pid 70)) {"task/7" (* 4000 MIB)}))
  (<- units tuple (_kill-units #(shim body) roots))
  (<- uses tuple (_job-uses #(shim body) roots))
  (<- victim (| KillUnit None) (victim-of units uses #() (MemoryLimits)))
  (assert (= #(victim.job victim.kind) #("task/7" UnitKind.TASK)) victim))


(defk swarm-watched [declared-mib]
  {:pre [(: declared-mib (| int None))] :post [(: % WatchSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "群れの台本(tests/test_memory_watch の swarm-rows — svc の和 20215 MiB・空き 2.0 GiB / 32 GiB・container 28 GiB)を、svc の宣言の山
   declared-mib(None = 宣言なし)を置いた入れ物で 4 秒まで回すため。"
  (<- rows tuple (swarm-rows))
  (<- files MemoryFiles (swarm-files 2.0 28))
  (val cell (MemoryCell))
  (setv cell.eviction.declared (if (is declared-mib None) {} {"svc" (* declared-mib MIB)}))
  (<- seen WatchSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (scripted-proc-table-handler rows)
                                     (memory-world cell 0 4000)]
                       (watch-then-seen (MemoryWatchSettings) cell)))
  seen)


(deftest test-a-swarm-inside-its-declared-peak-is-not-killed-together
  ;; 宣言 21000 MiB の内の svc(20215 MiB)は群れの止めに当てない — 止めずに 1 度だけ、宣言の内なので止めなかった job を名指す。
  (<- seen WatchSeen (swarm-watched 21000))
  (<- swarms tuple (lines-named seen.lines MEMORY-SWARM-KILL-LOG))
  (<- spared tuple (lines-named seen.lines MEMORY-NO-VICTIM-LOG))
  (assert (= #(swarms (len spared)) #(#() 1)) seen.lines)
  (assert (= (get (get spared 0) "spared_declared") "svc=20215/21000") spared)
  (<- rows tuple (swarm-rows))
  (assert (= (len seen.table) (len rows)) seen.table)
  ;; 宣言を越えた svc(宣言 10000 MiB)は今どおり群れで止める。
  (<- beyond WatchSeen (swarm-watched 10000))
  (<- beyond-swarms tuple (lines-named beyond.lines MEMORY-SWARM-KILL-LOG))
  (assert (= (tuple (gfor s beyond-swarms (get s "units"))) #(6)) beyond.lines))
