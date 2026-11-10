;; jobs の層の空きを working set で数える — 模擬の検(2026-11-10)。
;;
;; 実例(11-10 13:4x JST の helios): jobs の層は memory.max 8192 MiB・memory.current 6731 MiB(うち inactive_file 2112 MiB)で、置いた task は
;; 627 MiB の 1 本だけ。heartbeat の名乗る空きは 上限 − memory.current で、process の居ない page cache を使いに数えたので、層が空でも
;; 要り 5632 MiB の task は入らず、置き先はその task を置かなかった。層の memory.max に当たると kernel は task を止める前に回収できる
;; page cache を返すので、上限で決まる空きは 上限 − working set(memory.current − inactive_file — kubelet と同じ数え)。
;;   - worker の読み(worker/protocol/eviction_line の jobs-limited): 新しい受けの上限を working set を土台に数え、heartbeat の名乗る空き
;;     (worker/core/load_rules の jobs-room-of)と調整ループの観測する空きは 受けの上限 − working set。
;;   - jobs の層の memory.max は今までどおり(上限で決まる回は宣言の値)。
;;   - 追い出しの線で決まる回は、線の used-bytes が既に working set で数えるので、inactive_file の多少で空きは変わらない(page cache を
;;     2 度空きに数えない)。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect HttpRequest HttpResponse])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.load_rules [jobs-room-of])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell eviction-noted])

;; 模擬の機体(Node の宣言の memory は helios の 11-10 の値に近い形)。
(val CAPACITY (* 61048136 1024))
(val ALLOCATABLE (* 59000000 1024))
(val PODS-MAX (- CAPACITY (* 1152 MIB)))
(val MARGIN (* 512 MIB))
(val JOBS-CAP (* 8192 MIB))
;; 11-10 13:4x JST の helios の jobs の層。
(val JOBS-CURRENT (* 6731 MIB))
(val JOBS-INACTIVE (* 2112 MIB))
;; 11-10 15:1x JST の helios の jobs の層の slab_reclaimable。
(val JOBS-SLAB (* 1070 MIB))
;; 追い出しまでの空き − 余裕(広い = 上限で決まる・狭い = 線で決まる)。
(val WIDE-ROOM (* 20000 MIB))
(val NARROW-ROOM (* 1000 MIB))
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN-DIR (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN-DIR "/jobs"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/helios")


(defhandler helios-api []
  "模擬の helios の Node の宣言を答える偽の API server。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "61048136Ki"} "allocatable" {"memory" "59000000Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))))


(defhandler log-dropped []
  "log の行を捨てる答え手(この検は log を数えない)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (SlogEffect []
    (resume None)))


(defk helios-files [room inactive [slab 0]]
  {:pre [(: room int) (: inactive int) (: slab int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の helios の cgroup を作るため: 追い出しまでの空き − 余裕が room の線・jobs の層の memory.current JOBS-CURRENT のうち inactive_file
   inactive と slab_reclaimable slab(線の使いは working set で数えるので、Pod の層の使いも同じ inactive_file を持つ)。"
  (val pods (- ALLOCATABLE (+ room MARGIN)))
  (val files #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str PODS-MAX)))
               (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str (+ pods inactive))))
               (MemoryFile :path (+ PODS "/memory.stat")
                           :content (.encode (.format "anon {}\nfile {}\ninactive_file {}\n" pods inactive inactive)))
               (MemoryFile :path (+ HOST "/memory.stat")
                           :content (.encode (.format "anon {}\nfile {}\ninactive_file {}\n" (+ pods (* 600 MIB)) inactive inactive)))
               (MemoryFile :path (+ JOBS "/memory.current") :content (.encode (str JOBS-CURRENT)))
               (MemoryFile :path (+ JOBS "/memory.stat")
                           :content (.encode (.format "anon {}\nfile {}\ninactive_file {}\nslab_reclaimable {}\n" (- JOBS-CURRENT inactive slab) inactive inactive slab)))
               (MemoryFile :path (+ JOBS "/memory.max") :content b"max\n")
               (MemoryFile :path TOKEN :content b"token\n")))
  (val dirs (sorted (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                          :if (!= d f.path) d)))
  (MemoryFiles :dirs (tuple dirs) :files files))


(val SETTINGS (EvictionSettings :api "https://api.test" :token-file TOKEN :node "helios" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                                :jobs-cap-bytes JOBS-CAP :margin-bytes MARGIN))


(defk tick-then-files [cell]
  {:pre [(: cell EvictionCell)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の頭(eviction-noted)を 1 回まわし、その後の模擬の file を読むため。"
  (<- (eviction-noted SETTINGS cell True 5000))
  (<- files MemoryFiles (ReadMemoryFiles))
  files)


(defk tick-on [room inactive [slab 0]]
  {:pre [(: room int) (: inactive int) (: slab int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の helios で見張りの回を 1 回まわし、#(heartbeat の名乗る空き MiB・jobs の層の memory.max の中身) を得るため。"
  (val cell (EvictionCell))
  (<- start MemoryFiles (helios-files room inactive slab))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (helios-api) (log-dropped)]
                          (tick-then-files cell)))
  (<- named (| int None) (jobs-room-of cell.line cell.jobs-intake cell.jobs-held))
  (val limit (.decode (. (next (gfor f files.files :if (= f.path (+ JOBS "/memory.max")) f)) content) "utf-8"))
  #(named limit))


(deftest test-the-reclaimable-page-cache-does-not-fill-the-jobs-room
  ;; 受入: 上限で決まる回、名乗る空きは 上限 − working set = 8192 − (6731 − 2112) = 3573 MiB。失敗ケース(直す前): 上限 − memory.current
  ;; = 1461 MiB を名乗り、inactive_file 2112 MiB が空きを塞いだ。jobs の層の memory.max は宣言の上限のまま。
  (<- #(named limit) tuple (tick-on WIDE-ROOM JOBS-INACTIVE))
  (assert (= named (// (- JOBS-CAP (- JOBS-CURRENT JOBS-INACTIVE)) MIB) 3573) named)
  (assert (= limit (str JOBS-CAP)) limit))


(deftest test-a-line-bound-room-does-not-count-the-page-cache-twice
  ;; 線で決まる回は、線の使いが既に working set で数える(page cache を空きに数え済み)ので、inactive_file の多少で名乗る空きは変わらない
  ;; — working set を土台にしても page cache を 2 度空きに数えない。
  (<- #(cold _) tuple (tick-on NARROW-ROOM 0))
  (<- #(cached _) tuple (tick-on NARROW-ROOM JOBS-INACTIVE))
  (assert (= cold cached) #(cold cached))
  (assert (= cold (// NARROW-ROOM MIB)) cold))


(deftest test-the-reclaimable-slab-does-not-fill-the-jobs-room
  ;; 受入: 上限で決まる回、名乗る空きは 上限 − (working set − slab_reclaimable)= 8192 − (6731 − 2112 − 1070) = 4643 MiB。失敗ケース
  ;; (11-10 15:1x JST の helios): task 0 本の層の working set 4212 MiB のうち 1070 MiB が git と uv の file 操作の残した slab_reclaimable で、
  ;; 上限 − working set では要り 5632 MiB + 床 512 MiB に届かなかった。jobs の層の memory.max は宣言の上限のまま。
  (<- #(named limit) tuple (tick-on WIDE-ROOM JOBS-INACTIVE JOBS-SLAB))
  (assert (= named (// (- JOBS-CAP (- JOBS-CURRENT JOBS-INACTIVE JOBS-SLAB)) MIB) 4643) named)
  (assert (= limit (str JOBS-CAP)) limit))
