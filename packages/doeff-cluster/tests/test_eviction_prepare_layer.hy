;; keeper の版の準備の層と、機体ごとの余裕 — 模擬の検。
;;
;; 実例(11-02): node-e の keeper が doeff 6e1996649 の root を準備する bytecode の焼きが、jobs の層の memory.max(61〜139 MiB)で OOM に
;; なり、300 秒ごとの撃ち直しも同じだった。準備は jobs の層に置かれ、worker は 5 秒ごとに jobs の上限を「jobs の使い + 追い出しまでの
;; 空き − 余裕 1 GiB」に掛け直していた — node-e(allocatable 2,659 MiB)の平常の空き約 1,040 MiB から 1 GiB を引くと、準備に残るのは
;; jobs の使い(node-boot-facts の 147 MiB)の上の数十 MiB だった。
;;   - 判断(worker/core/eviction_rules): 準備の取り置き(準備が走る間は準備の層の上限 − 今の使い)を jobs の上限から引く・keeper に渡す
;;     準備の空き = 空き − 余裕 − 取り置き・余裕 = 機体の宣言の床と、測った外の増えの分位の 2 回分の大きい方。
;;   - worker の読み(worker/protocol/eviction_line の eviction-noted): node-e の値の模擬の cgroup の file と Node の宣言で回を回し、
;;     準備の層の memory.max に書かない・jobs の層の上限から取り置きを引く・keeper の file に空きと準備の層の上限を書く・外の増えを残す。
;;     前の形(準備の層が無く、余裕 1 GiB)の同じ機体では、jobs の上限が準備の山(手元で測った 453 MiB)を下回る(対照)。
;;   - keeper(deploy/boot.sh の keeper_swap)の待ちは tests/test_keeper_prepare_room.py。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import posixpath)
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect HttpRequest HttpResponse])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [EVICTION-MARGIN-BYTES MARGIN-MIN-SAMPLES MARGIN-TICKS EvictionLine LineKind
                                                  jobs-limit prepare-reserve prepare-room prepare-addable margin-of layer-memory-max])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell eviction-noted MARGIN-LOG])

;; node-e の Node の宣言(11-02 の Node の status と kubelet の予約 system 384Mi・kube 768Mi・evictionHard 100Mi)。kubepods の memory.max =
;; capacity − 予約。
(val CAPACITY (* 4005200 1024))
(val ALLOCATABLE (* 2723152 1024))
(val PODS-MAX (- CAPACITY (* 1152 MIB)))
;; 11-02 の worker の log の平常の値: 追い出しまでの空き 約 1,040 MiB・jobs の層の使い(node-boot-facts)147 MiB。
(val ROOM (* 1040 MIB))
(val JOBS-USED (* 147 MIB))
;; 準備の山: 手元で doeff 6e1996649 の root を冷えた bytecode の保存先で準備し、焼きを 2 並列(k3s の worker の CPU の上限 2)で回した
;; process の木の anon の最大 453 MiB(uv sync の山は 275 MiB)。準備の層の上限は宣言の値(nodes/node-e/cgroup.yaml)。
(val PREPARE-PEAK (* 453 MIB))
(val NODE-E-LAYOUT (+ "worker:memory.min=256M,cpu.weight=1000 services:memory.min=1536M,cpu.weight=1000 "
                     "jobs:memory.max=896M,cpu.weight=100 prepare:memory.max=640M,cpu.weight=100"))
;; node-e の宣言の余裕(nodes/node-e/cgroup.yaml の WORKER_EVICTION_MARGIN)。
(val NODE-E-MARGIN (* 320 MIB))
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN "/jobs"))
(val PREPARE (+ OWN "/prepare"))
(val ROOM-FILE "/keeper/prepare-room")
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/node-e")


(defk line-of [room]
  {:pre [(: room int)] :post [(: % EvictionLine)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-e の allocatable の線で、追い出しまでの空きが room の線を作るため。"
  (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes ALLOCATABLE :used-bytes (- ALLOCATABLE room) :room-bytes room))


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-a-running-prepare-reserves-up-to-its-cap-and-the-jobs-layer-gives-way
  (<- cap (| int None) (layer-memory-max NODE-E-LAYOUT "prepare"))
  (assert (= cap (* 640 MIB)) cap)
  ;; 走っている準備は、上限までの残りを取り置く。走っていない(終わった準備の page cache が残る)層は取り置かない。
  (<- running int (prepare-reserve cap (* 200 MIB) True))
  (<- idle int (prepare-reserve cap (* 90 MIB) False))
  (<- over int (prepare-reserve cap (+ cap MIB) True))
  (assert (= #(running idle over) #((* 440 MIB) 0 0)))
  ;; jobs の上限 = jobs の使い + 空き − 余裕 − 取り置き(静的な上限 896M と 0 の間)。
  (<- line EvictionLine (line-of ROOM))
  (<- with-prepare int (jobs-limit JOBS-USED line NODE-E-MARGIN NODE-E-MARGIN (* 896 MIB) running 0))
  (assert (= with-prepare (- (+ JOBS-USED ROOM) NODE-E-MARGIN running)) (// with-prepare MIB))
  ;; 空きが余裕と取り置きの和を割れば、jobs の上限は jobs の使いより下がる(縮むのは jobs の層 — 準備の層の上限は宣言の値のまま)。
  (<- narrow EvictionLine (line-of (* 600 MIB)))
  (<- squeezed int (jobs-limit JOBS-USED narrow NODE-E-MARGIN NODE-E-MARGIN (* 896 MIB) running 0))
  (assert (< squeezed JOBS-USED) (// squeezed MIB)))


(deftest test-what-a-prepare-can-add-is-its-cap-less-the-working-set-already-counted-on-the-line
  ;; 準備が線へ足しうる量 = 上限 − 層の今の working set。上限を越えた読みは 0。
  (val cap (* 640 MIB))
  (<- idle int (prepare-addable cap 0))
  (<- residue int (prepare-addable cap (* 288 MIB)))
  (<- over int (prepare-addable cap (+ cap MIB)))
  (assert (= #(idle residue over) #(cap (* 352 MIB) 0)) #(idle residue over)))


(deftest test-a-prepare-layer-holding-only-residue-asks-the-keeper-for-its-cap-less-the-residue
  ;; 受入: process の居ない名残だけの層(node-e の 11-09 の形 — 名残 288 MiB)。線の使いは名残を含むので、keeper の file の第 2 の数は
  ;; 上限 640 でなく足しうる量 352 MiB。失敗ケース: 上限を丸ごと書くと、名残を 2 度数えて空き 400 MiB でも keeper が待ち続ける。
  (val pods (- ALLOCATABLE (+ (* 400 MIB) NODE-E-MARGIN)))
  (<- residue MemoryFiles (node-e-files pods JOBS-USED (* 288 MIB) False))
  (<- settings EvictionSettings (settings-of True NODE-E-MARGIN))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler residue) (node-e-api) (log-kept)]
                          (ticks-then-files settings (EvictionCell) 1)))
  (<- room str (content-at files ROOM-FILE))
  (assert (= room "400 352\n") room))


(deftest test-the-normal-room-of-a-4-gib-node-fits-the-prepare-peak
  ;; 受入: node-e の平常の空き(約 1 GiB)で、keeper に渡す準備の空きが準備の層の上限(と準備の山)以上 — keeper は準備を起こす。
  (<- cap (| int None) (layer-memory-max NODE-E-LAYOUT "prepare"))
  (<- line EvictionLine (line-of ROOM))
  (<- room int (prepare-room line NODE-E-MARGIN 0))
  (assert (>= room cap PREPARE-PEAK) #((// room MIB) (// cap MIB)))
  ;; 前の形(1 GiB を一律に引き、準備を jobs の層に置く)の jobs の上限は、同じ空きで準備の山を下回る(11-02 の OOM)。
  (<- before int (jobs-limit JOBS-USED line EVICTION-MARGIN-BYTES EVICTION-MARGIN-BYTES (* 1536 MIB) 0 0))
  (assert (< (- before JOBS-USED) PREPARE-PEAK) (// before MIB))
  ;; 前の世代の起動の直後(空き 690 MiB)は、待つ(準備の空き < 上限)— OOM で殺す代わりに keeper が待つ。
  (<- after-boot EvictionLine (line-of (* 690 MIB)))
  (<- tight int (prepare-room after-boot NODE-E-MARGIN 0))
  (assert (< tight cap) (// tight MIB)))


(deftest test-the-margin-is-the-declared-floor-until-the-measured-growth-says-more
  (val floor NODE-E-MARGIN)
  ;; 測りが MARGIN-MIN-SAMPLES 回に満たない間は宣言の床。
  (<- early int (margin-of (tuple (gfor _ (range (- MARGIN-MIN-SAMPLES 1)) (* 900 MIB))) floor))
  (assert (= early floor) early)
  ;; 増えの p999 × MARGIN-TICKS が床の下なら床。
  (<- calm int (margin-of (tuple (gfor i (range 2000) (* (% i 7) MIB))) floor))
  (assert (= calm floor) calm)
  ;; 機体に伸びる Pod が載った(1000 回のうち 2 回が 300 MiB)— 分位(順位 ceil(0.999 × 1000) = 999 番目)が 300 MiB で、余裕は 600 MiB。
  (val grown (tuple (+ (lfor _ (range 998) MIB) (lfor _ (range 2) (* 300 MIB)))))
  (<- raised int (margin-of grown floor))
  (assert (= raised (* MARGIN-TICKS 300 MIB)) (// raised MIB))
  ;; 1 回だけの跳ねは分位に入らない(順位 999 番目は 1 MiB)。
  (val once (tuple (+ (lfor _ (range 999) MIB) [(* 3 1024 MIB)])))
  (<- spike int (margin-of once floor))
  (assert (= spike floor) (// spike MIB)))


;; --- worker の読み(eviction-noted)--------------------------------------------------------------------------------------------------

(defhandler node-e-api []
  "node-e の Node の宣言を答える偽の API server。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "4005200Ki"} "allocatable" {"memory" "2723152Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))))


(defeffect ReadLogNames
  "log の行の名を出した順に問う — テストだけの effect。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler log-kept []
  "log の行の名を溜め、ReadLogNames に答える答え手(検が数える)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var names #())
  (SlogEffect []
    (:= names (+ names #(effect.msg)))
    (resume None))
  (ReadLogNames []
    (resume names)))


(defk node-e-files [pods-used jobs-used prepare-used populated]
  {:pre [(: pods-used int) (: jobs-used int) (: prepare-used int) (: populated bool)] :post [(: % MemoryFiles)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-e の cgroup と ServiceAccount の file を作るため: kubepods の使い pods-used(inactive_file 0)・root は Pod の外に 600 MiB・
   jobs と準備の層の使い・準備の層に process が居るか。"
  (val files #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str PODS-MAX)))
               (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str pods-used)))
               (MemoryFile :path (+ PODS "/memory.stat") :content (.encode (.format "anon {}\nfile 0\ninactive_file 0\n" pods-used)))
               (MemoryFile :path (+ HOST "/memory.stat")
                           :content (.encode (.format "anon {}\nfile 0\ninactive_file 0\n" (+ pods-used (* 600 MIB)))))
               (MemoryFile :path (+ JOBS "/memory.current") :content (.encode (str jobs-used)))
               (MemoryFile :path (+ JOBS "/memory.stat") :content b"anon 1\ninactive_file 0\n")
               (MemoryFile :path (+ JOBS "/memory.max") :content b"max\n")
               (MemoryFile :path (+ PREPARE "/memory.current") :content (.encode (str prepare-used)))
               (MemoryFile :path (+ PREPARE "/memory.stat") :content b"anon 1\ninactive_file 0\n")
               (MemoryFile :path (+ PREPARE "/memory.max") :content b"671088640\n")
               (MemoryFile :path (+ PREPARE "/cgroup.events")
                           :content (.encode (.format "populated {}\nfrozen 0\n" (if populated 1 0))))
               (MemoryFile :path TOKEN :content b"token\n")))
  (val dirs (sorted (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                          :if (!= d f.path) d)))
  (MemoryFiles :dirs (tuple (+ (list dirs) ["/keeper"])) :files files))


(defk settings-of [prepare margin]
  {:pre [(: prepare bool) (: margin int)] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-e の線の設定を作るため。prepare = 準備の層を数えるか(偽 = 前の形 — 準備は jobs の層・jobs の上限は前の宣言 1536M)。"
  (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-e" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                    :jobs-cap-bytes (if prepare (* 896 MIB) (* 1536 MIB)) :prepare-dir (if prepare PREPARE None)
                    :prepare-cap-bytes (if prepare (* 640 MIB) None) :room-file (if prepare ROOM-FILE None) :margin-bytes margin))


(defk ticks-then-files [settings cell ticks]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: ticks int)] :post [(: % MemoryFiles)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の頭(eviction-noted)を 5 秒おきに ticks 回まわし、その後の模擬の file を読むため。"
  (for [i (range ticks)]
    (<- (eviction-noted settings cell True (* 5000 (+ i 1)))))
  (<- files MemoryFiles (ReadMemoryFiles))
  files)


(defk ticks-then-logs [settings cell ticks]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: ticks int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の頭を ticks 回まわし、出した log の行の名を読むため。"
  (<- (ticks-then-files settings cell ticks))
  (<- names tuple (ReadLogNames))
  names)


(defk content-at [files path]
  {:pre [(: files MemoryFiles) (: path str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file の path の中身を文字で読むため。"
  (.decode (. (next (gfor f files.files :if (= f.path path) f)) content) "utf-8"))


(deftest test-a-running-prepare-keeps-its-layer-and-the-jobs-layer-gives-way-to-its-peak
  ;; 準備が走り出した回(準備の層に process が居て 200 MiB)。空き 1,040 MiB は準備の 200 MiB を含んだ後の値。
  (val pods (- ALLOCATABLE ROOM))
  (val cell (EvictionCell))
  (<- start MemoryFiles (node-e-files pods JOBS-USED (* 200 MIB) True))
  (<- settings EvictionSettings (settings-of True NODE-E-MARGIN))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-e-api) (log-kept)]
                          (ticks-then-files settings cell 3)))
  ;; 準備の層の memory.max(宣言の 640M)は書かない — kernel は準備を山まで通す。
  (<- prepare-max str (content-at files (+ PREPARE "/memory.max")))
  (assert (= prepare-max "671088640\n") prepare-max)
  ;; jobs の上限 = 147 + 1040 − 320 − (640 − 200) = 427 MiB。準備が山(上限)まで伸びても、Pod の和は線 − 余裕に収まる。
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (val reserve (* 440 MIB))
  (assert (= (int jobs-max) (- (+ JOBS-USED ROOM) NODE-E-MARGIN reserve)) jobs-max)
  (assert (<= (+ (- pods JOBS-USED (* 200 MIB)) (int jobs-max) (* 640 MIB)) (- ALLOCATABLE NODE-E-MARGIN)))
  ;; keeper の file: 準備に使える空き(1040 − 320 − 440 = 280 MiB)と準備が線へ足しうる量(640 − 200 = 440 MiB)。
  (<- room str (content-at files ROOM-FILE))
  (assert (= room "280 440\n") room)
  ;; 外の working set は回ごとに同じ — 増え 0 が 2 回残り、余裕は宣言の床のまま(余裕の行は出ない)。
  (assert (= #(cell.growths cell.margin cell.reserve) #(#(0 0) NODE-E-MARGIN reserve)) cell.growths)
  (<- names tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-e-api) (log-kept)]
                    (ticks-then-logs settings (EvictionCell) 3)))
  (assert (not-in MARGIN-LOG names) names))


(deftest test-an-idle-prepare-layer-tells-the-keeper-the-room-fits-and-the-old-shape-does-not
  ;; 準備の走っていない平常の回: keeper の file は 1040 − 320 = 720 MiB ≥ 足しうる量 640 − 90 = 550 MiB — keeper は準備を起こす。
  (val pods (- ALLOCATABLE ROOM))
  (<- idle MemoryFiles (node-e-files pods JOBS-USED (* 90 MIB) False))
  (<- settings EvictionSettings (settings-of True NODE-E-MARGIN))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler idle) (node-e-api) (log-kept)]
                          (ticks-then-files settings (EvictionCell) 1)))
  (<- room str (content-at files ROOM-FILE))
  (assert (= room "720 550\n") room)
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (= (int jobs-max) (min (* 896 MIB) (- (+ JOBS-USED ROOM) NODE-E-MARGIN))) jobs-max)
  ;; 前の形(準備の層が無く余裕 1 GiB — 11-02 の node-e): jobs の上限は使いの上に 16 MiB しか残さず、準備の山 453 MiB は jobs の層で OOM。
  (<- before-files MemoryFiles (node-e-files pods JOBS-USED 0 False))
  (<- before-settings EvictionSettings (settings-of False EVICTION-MARGIN-BYTES))
  (<- old MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler before-files) (node-e-api) (log-kept)]
                        (ticks-then-files before-settings (EvictionCell) 1)))
  (<- old-max str (content-at old (+ JOBS "/memory.max")))
  (assert (< (- (int old-max) JOBS-USED) PREPARE-PEAK) (// (int old-max) MIB)))
