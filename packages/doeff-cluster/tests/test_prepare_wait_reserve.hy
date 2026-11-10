;; keeper が版の準備を待つ間の取り置き — 模擬の検(2026-11-10)。
;;
;; 実例(11-09 の k3s-1): 22:24 UTC に版の file を 2330420f4 にした。keeper は「空き − 余裕 = 403 MiB < 準備が線へ足しうる量 568 MiB」で
;; 準備を待った。worker は準備が走るまで何も取り置かず、heartbeat の名乗る空きは待つ間も 403 MiB のままで、置き先は空いた分へ常駐の
;; service 2 つ(patrol-watch と rules — RSS 計 約 370 MiB)を置いた。keeper の空きは 335 MiB(足しうる量 529 MiB)に減り、準備は起きなかった。
;;   - 判断(worker/core/eviction_rules): 版の file の版がこの世代の版と違い、準備の層に process が居ない間だけ待っている・待つ間の
;;     取り置き = 足しうる量。
;;   - worker の読み(worker/protocol/eviction_line の eviction-noted): 待つ間は、新しい受けに名乗る jobs の層の空きが足しうる量の分だけ
;;     小さい。keeper の読む空きと jobs の層の memory.max は待たない時と同じ(keeper の判じを自分の取り置きで塞がず、走っている task を
;;     準備のために止めない)。
;;   - heartbeat と置き先(coordinator/core/cluster_policy の memory-misfit): 取り置きを名乗る worker に新しく常駐の job を置かず、ほかの
;;     worker へ置く。置ける先がほかに無ければ置かずに訳を名指す。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect HttpRequest HttpResponse])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster [ClusterTiming JobSpec ClusterJob WorkerInfo ClusterState])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [jobs-placed register-heartbeat unplaced-jobs])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine LineKind prepare-waiting prepare-wait-reserve])
(import doeff_cluster._worker.core.load_rules [MachineLoad jobs-room-of prepare-wait-of])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell eviction-noted])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import tests.program_rows [heartbeat-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
;; keeper の世代の版と、版の file に書いた新しい版(11-09 の k3s-1)。
(val OWN (* "a" 40))
(val NEXT "2330420f4")
(val NEXT-FULL (+ NEXT (* "0" 31)))
;; k3s-1 の Node の宣言と層(tests/test_eviction_prepare_layer と同じ値)。
(val CAPACITY (* 4005200 1024))
(val ALLOCATABLE (* 2723152 1024))
(val PODS-MAX (- CAPACITY (* 1152 MIB)))
(val MARGIN (* 320 MIB))
(val JOBS-USED (* 147 MIB))
(val JOBS-CAP (* 896 MIB))
(val PREPARE-CAP (* 640 MIB))
;; 11-09 の keeper の待ちの足しうる量 568 MiB = 上限 640 − 層の名残の working set 72 MiB。
(val ADDABLE (* 568 MIB))
(val RESIDUE (- PREPARE-CAP ADDABLE))
;; keeper の空き(追い出しまでの空き − 余裕)が足しうる量に足りない線: 空き 483 MiB < 568 MiB — keeper は待つ。jobs の層の受けの上限が
;; 0 や宣言の上限で切られない値にする(名乗る空きの差が取り置きそのものに出る)。
(val KEEPER-ROOM (* 483 MIB))
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN-DIR (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN-DIR "/jobs"))
(val PREPARE (+ OWN-DIR "/prepare"))
(val ROOM-FILE "/keeper/prepare-room")
(val VERSION-FILE "/config/worker-version")
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/k3s-1")


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-the-worker-waits-only-while-the-version-file-names-another-version-and-no-prepare-runs
  ;; 版の file が別の版(前後の空白・改行は keeper と同じく除く)なら待っている。同じ版・無い・綴りの違う file・keeper の世代でない worker は
  ;; 待っていない(keeper も替えない)。
  (<- other bool (prepare-waiting (+ NEXT-FULL "\n") OWN))
  (<- same bool (prepare-waiting (+ " " OWN "\n") OWN))
  (<- missing bool (prepare-waiting None OWN))
  (<- short bool (prepare-waiting NEXT OWN))
  (<- upper bool (prepare-waiting (.upper NEXT-FULL) OWN))
  (<- no-keeper bool (prepare-waiting NEXT-FULL None))
  (assert (= #(other same missing short upper no-keeper) #(True False False False False False)))
  ;; 待っていて準備がまだ走っていない間だけ足しうる量を取り置く。走り出した準備は prepare-reserve が取り置く。
  (<- held int (prepare-wait-reserve True False ADDABLE))
  (<- running int (prepare-wait-reserve True True ADDABLE))
  (<- idle int (prepare-wait-reserve False False ADDABLE))
  (assert (= #(held running idle) #(ADDABLE 0 0))))


;; --- worker の読み(eviction-noted)--------------------------------------------------------------------------------------------------

(defhandler k3s-1-api []
  "k3s-1 の Node の宣言を答える偽の API server。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "4005200Ki"} "allocatable" {"memory" "2723152Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))))


(defhandler log-dropped []
  "log の行を捨てる答え手(この検は log を数えない)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (SlogEffect []
    (resume None)))


(defk k3s-1-files [version prepare-used populated]
  {:pre [(: version str) (: prepare-used int) (: populated bool)] :post [(: % MemoryFiles)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の k3s-1 の cgroup と版の file を作るため: 追い出しまでの空き − 余裕が KEEPER-ROOM の線・jobs の層の使い JOBS-USED・準備の層の使い
   prepare-used(inactive_file 0)と process の有無 populated・版の file の中身 version。"
  (val pods (- ALLOCATABLE (+ KEEPER-ROOM MARGIN)))
  (val files #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str PODS-MAX)))
               (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str pods)))
               (MemoryFile :path (+ PODS "/memory.stat") :content (.encode (.format "anon {}\nfile 0\ninactive_file 0\n" pods)))
               (MemoryFile :path (+ HOST "/memory.stat")
                           :content (.encode (.format "anon {}\nfile 0\ninactive_file 0\n" (+ pods (* 600 MIB)))))
               (MemoryFile :path (+ JOBS "/memory.current") :content (.encode (str JOBS-USED)))
               (MemoryFile :path (+ JOBS "/memory.stat") :content b"anon 1\ninactive_file 0\n")
               (MemoryFile :path (+ JOBS "/memory.max") :content b"max\n")
               (MemoryFile :path (+ PREPARE "/memory.current") :content (.encode (str prepare-used)))
               (MemoryFile :path (+ PREPARE "/memory.stat") :content b"anon 1\ninactive_file 0\n")
               (MemoryFile :path (+ PREPARE "/memory.max") :content (.encode (str PREPARE-CAP)))
               (MemoryFile :path (+ PREPARE "/cgroup.events")
                           :content (.encode (.format "populated {}\nfrozen 0\n" (if populated 1 0))))
               (MemoryFile :path VERSION-FILE :content (.encode version))
               (MemoryFile :path TOKEN :content b"token\n")))
  (val dirs (sorted (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                          :if (!= d f.path) d)))
  (MemoryFiles :dirs (tuple (+ (list dirs) ["/keeper"])) :files files))


(val SETTINGS (EvictionSettings :api "https://api.test" :token-file TOKEN :node "k3s-1" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                                :jobs-cap-bytes JOBS-CAP :prepare-dir PREPARE :prepare-cap-bytes PREPARE-CAP :room-file ROOM-FILE
                                :margin-bytes MARGIN :version-file VERSION-FILE :own-version OWN))


(defk tick-then-files [cell]
  {:pre [(: cell EvictionCell)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の頭(eviction-noted)を 1 回まわし、その後の模擬の file を読むため。"
  (<- (eviction-noted SETTINGS cell True 5000))
  (<- files MemoryFiles (ReadMemoryFiles))
  files)


(defk content-at [files path]
  {:pre [(: files MemoryFiles) (: path str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file の path の中身を文字で読むため。"
  (.decode (. (next (gfor f files.files :if (= f.path path) f)) content) "utf-8"))


(defk tick-on [version prepare-used populated]
  {:pre [(: version str) (: prepare-used int) (: populated bool)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の k3s-1 で見張りの回を 1 回まわし、#(入れ物 回の後の file) を得るため。"
  (val cell (EvictionCell))
  (<- start MemoryFiles (k3s-1-files version prepare-used populated))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (k3s-1-api) (log-dropped)]
                          (tick-then-files cell)))
  #(cell files))


(defk named-room [cell]
  {:pre [(: cell EvictionCell)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "入れ物から heartbeat が名乗る jobs の層の空き(MiB)を求めるため(coordinator_link と同じ数え — 線は入れ物の最後の線)。"
  (<- room (| int None) (jobs-room-of cell.line cell.jobs-intake cell.jobs-held))
  room)


(deftest test-a-waiting-worker-names-a-room-smaller-by-what-the-prepare-can-add
  ;; 受入: keeper が準備を待つ間(版の file が新しい版・準備の層は名残 72 MiB だけで process が居ない)、heartbeat の名乗る jobs の層の空きは、
  ;; 同じ線で待っていない時より足しうる量 568 MiB だけ小さい。失敗ケース(直す前): 待つ間も待たない時と同じ 483 MiB を名乗り、置き先は
  ;; そこへ新しい task と常駐の service を置いた。
  (<- #(idle idle-files) tuple (tick-on OWN RESIDUE False))
  (<- #(waiting waiting-files) tuple (tick-on NEXT-FULL RESIDUE False))
  (<- idle-room (| int None) (named-room idle))
  (<- waiting-room (| int None) (named-room waiting))
  (assert (= idle-room (// KEEPER-ROOM MIB)) idle-room)
  (assert (= (- idle-room waiting-room) (// ADDABLE MIB)) #(idle-room waiting-room))
  ;; 待つ間の取り置きは heartbeat の欄にも載る(待っていない世代は載せない)。
  (<- named (| int None) (prepare-wait-of waiting.line waiting.prepare-wait))
  (<- unnamed (| int None) (prepare-wait-of idle.line idle.prepare-wait))
  (assert (= #(named unnamed) #((// ADDABLE MIB) None)))
  ;; keeper の読む空きと jobs の層の memory.max は待たない時と同じ — keeper の判じを自分の取り置きで塞がず、走っている task を止めない。
  (<- idle-keeper str (content-at idle-files ROOM-FILE))
  (<- waiting-keeper str (content-at waiting-files ROOM-FILE))
  (assert (= idle-keeper waiting-keeper "483 568\n") #(idle-keeper waiting-keeper))
  (<- idle-max str (content-at idle-files (+ JOBS "/memory.max")))
  (<- waiting-max str (content-at waiting-files (+ JOBS "/memory.max")))
  (assert (= idle-max waiting-max) #(idle-max waiting-max)))


(deftest test-a-running-prepare-is-not-held-twice
  ;; 準備が走り出した回(準備の層に process が居る)は、待つ間の取り置きを消し、走る準備の取り置き(上限 − 今の使い)だけを持つ。
  (<- #(running _) tuple (tick-on NEXT-FULL RESIDUE True))
  (assert (= #(running.prepare-wait running.reserve) #(0 ADDABLE)) #(running.prepare-wait running.reserve)))


;; --- heartbeat と置き先 ---------------------------------------------------------------------------------------------------------------

(defk k3s-measured [waiting]
  {:pre [(: waiting (| int None))] :post [(: % MeasuredLoad)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "小さい機体(線 2659 MiB)の測りに、待つ間の取り置き waiting(MiB — None = 待っていない)を添えた形を作るため。"
  (MeasuredLoad :at-ms 0 :cpus 2 :cpu-busy 0.1 :memory-total-MiB 2659 :memory-available-MiB 403 :cpu-some-avg10 0.0
                :memory-some-avg10 0.0 :task-memory-MiB {} :jobs-room-MiB 403 :prepare-wait-MiB waiting))


(defk services-placed [k3s-1-waiting with-k3s-3]
  {:pre [(: k3s-1-waiting (| int None)) (: with-k3s-3 bool)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "常駐の service patrol-watch(settle 250 MiB)と rules(settle 120 MiB)を、予算 1792 MiB の k3s-1(待つ間の取り置き k3s-1-waiting)と、
   with-k3s-3 なら k3s-3 に置き先の 1 拍で置いた結果を得るため: #(job の名 → 置いた worker の名)。"
  (val k3s-1 (WorkerInfo "k3s-1" #() 0 :memory-mib 1792 :measured (! (k3s-measured k3s-1-waiting))))
  (val other (WorkerInfo "k3s-3" #() 0 :memory-mib 1792 :measured (! (k3s-measured None))))
  (<- workers (table-from-rows (| {"k3s-1" k3s-1} (if with-k3s-3 {"k3s-3" other} {}))))
  (val jobs #((ClusterJob (JobSpec "patrol-watch" "m" #() "rev") :peak-mib 500 :settle-mib 250)
              (ClusterJob (JobSpec "rules" "m" #() "rev") :peak-mib 240 :settle-mib 120)))
  (val state (ClusterState jobs workers))
  (val placed (! (jobs-placed 0 state T)))
  (dfor name (.keys placed) name (. (.row placed name) worker)))


(deftest test-the-placer-puts-no-new-service-on-a-worker-waiting-for-its-prepare
  ;; 受入: 待つ間の取り置きを名乗る k3s-1 には新しく常駐の service を置かず、k3s-3 へ置く。失敗ケース(直す前 — 名乗りの無い k3s-1 と
  ;; 同じ): 予算 1792 MiB の残りに入るので、k3s-1 にも置いた(担っている数の少ない順・同じなら名の順。k3s-1 だけなら 2 つとも —
  ;; 11-09 の patrol-watch と rules)。
  (<- before dict (services-placed None True))
  (assert (= before {"patrol-watch" "k3s-1" "rules" "k3s-3"}) before)
  (<- before-alone dict (services-placed None False))
  (assert (= before-alone {"patrol-watch" "k3s-1" "rules" "k3s-1"}) before-alone)
  (<- after dict (services-placed 568 True))
  (assert (= after {"patrol-watch" "k3s-3" "rules" "k3s-3"}) after)
  ;; 置ける先がほかに無ければ置かず、待つ訳を名指す。
  (<- alone dict (services-placed 568 False))
  (assert (= alone {}) alone))


(deftest test-the-waiting-reserve-travels-in-the-heartbeat-and-names-why-a-service-waits
  ;; worker は待つ間の取り置きを measured の欄 prepareWaitMiB で名乗り(待っていない時は欄が無い)、coordinator は受けて置き先に使う。
  (val load (MachineLoad :at-ms 0 :cpus 2 :cpu-busy 0.1 :memory-total-mib 2659 :memory-available-mib 403
                         :cpu-some-avg10 0.0 :cpu-full-avg10 None :memory-some-avg10 0.0 :memory-full-avg10 None))
  (<- bare dict (measured-json load))
  (assert (not-in "prepareWaitMiB" bare) bare)
  (<- spelled dict (measured-json (replace load :prepare-wait-mib 568)))
  (assert (= (get spelled "prepareWaitMiB") 568) spelled)
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "k3s-1" "memoryMiB" 1792 "measured" spelled})) 0)))
  (assert (= (. (.row heard.workers "k3s-1") measured prepare-wait-MiB) 568))
  (val old (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "k3s-1" "memoryMiB" 1792 "measured" bare})) 0)))
  (assert (is (. (.row old.workers "k3s-1") measured prepare-wait-MiB) None))
  ;; 置けない service の訳は、待つ間の取り置きを名指す。
  (val waiting (replace heard :jobs #((ClusterJob (JobSpec "patrol-watch" "m" #() "rev") :peak-mib 500 :settle-mib 250))))
  (val why (get (unplaced-jobs 0 waiting T) "patrol-watch"))
  (assert (in "版の準備が空きを待つ間の取り置き 568 MiB" why) why))
