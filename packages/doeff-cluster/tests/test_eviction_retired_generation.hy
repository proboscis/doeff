;; 退いた世代の見張りは cgroup の層の上限を書かない — 模擬の検。
;;
;; 実例(2026-11-10): keeper が worker の版を入れ替えた後、前の世代は退いた handoff の process を終わりまで回すため同じ container に何時間も
;; 残り、その見張りが新しい世代と同じ jobs の層の memory.max を書き続けた。前の世代は置き先が新しい世代に置いた task の要りを heartbeat の
;; 返事で受けないので取り置きを 0 と数え、上に載る系が残す線を割った回に上限を jobs の今の使いまで(実例では 0 まで)下げた。新しい世代に
;; 置いた要り 6144 MiB の task の実行環境の準備が、jobs の層の上限で終了 -9 に止まり続けた。
;; 2026-11-12 から、上に載る系が残す線は jobs の層の memory.max を絞らない(worker/core/eviction_rules の頭の註)。2 つの世代の数える上限が
;; 違うのは、測った余裕が違う時: 前の世代は何時間も測った外の増えから余裕を宣言の床より上げていて、起きたばかりの新しい世代は床のまま。
;; この検の台本はその形(前の世代の余裕 3072 MiB・新しい世代 1024 MiB)で、線を割った読み(KEEP-ROOM)は両方に渡したまま。
;;   - worker の読み(worker/protocol/eviction_line の eviction-noted): 同じ模擬の cgroup の file の上で 2 つの世代の見張りを交互に回す。
;;     新しい世代の上限は置いた task の使い + 取り置きより上で、keeper が印 draining を置いた前の世代は書かない — 最後の値は新しい世代の値。
;;     印の無い間は前の世代だけが書き(新しい世代は前の世代が居る間は書かない — 世代の書き手)、2 つの世代が交互に書く回が無い。
;;     印を読まない前の形は、前の世代の書きが取り置きより下の値で新しい世代の上限を上書きする(失敗ケース)。
;;   - 印の path(deploy/boot.sh の keeper_retire が置く所と、世代へ渡す DOEFF_WORKER_RETIRED_FILE)が揃う事は
;;     tests/test_keeper_retired_mark.py。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import doeff [with-handlers])
(import doeff_core_effects [FileFailed SlogEffect WriteText HttpRequest HttpResponse])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster._worker.core.memory_rules [JobUse MIB])
(import doeff_cluster._worker.core.eviction_rules [EVICTION-MARGIN-BYTES MARGIN-MIN-SAMPLES layer-memory-max task-needs-of
                                                  writing-elder-of])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell eviction-noted RETIRED-LOG ELDER-WRITES-LOG])

(val GIB (* 1024 MIB))
;; node-a の Node の宣言(test_eviction_line.hy と同じ値)と jobs の層の宣言。
(val CAPACITY (* 29662296 1024))
(val ALLOCATABLE (* 20749400 1024))
(val PODS-MAX (- CAPACITY (* 7 GIB)))
(val LAYOUT "worker:memory.min=256M,cpu.weight=1000 services:memory.min=256M,cpu.weight=1000 jobs:memory.max=10752M,cpu.weight=100")
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN "/jobs"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/node-a")
;; keeper の世代の dir(deploy/boot.sh の $DOEFF_KEEPER_DIR/gens/<世代>)と、前の世代の退きの印。
(val ELDER-DIR "/keeper/gens/pod1.1000")
(val ELDER-MARK (+ ELDER-DIR "/draining"))
(val NEWER-DIR "/keeper/gens/pod1.2000")
(val NEWER-MARK (+ NEWER-DIR "/draining"))
;; 平常の空き(Pod の和 12 GiB — 追い出しまでの空き約 7.8 GiB)・jobs の層の使い 2 GiB・上に載る系が残す線を 1 GiB 割った読み。
(val PODS-USED (* 12 GIB))
(val JOBS-USED (* 2 GIB))
(val KEEP-ROOM (- GIB))
;; 新しい世代に置いた task: 要り 6144 MiB・準備の途中で 512 MiB 使っている(取り置き 5632 MiB)。
(val NEED-MIB 6144)
(val TASK-USE (* 512 MIB))
(val PENDING (- (* NEED-MIB MIB) TASK-USE))
;; 追い出しまでの空き(allocatable の線)と、2 つの世代の数える上限。前の世代は外の増え 1536 MiB/回を MARGIN-MIN-SAMPLES 回測っていて
;; 余裕は 2 回分の 3072 MiB — 上限は新しい世代に置いた task の使い + 取り置きより下。新しい世代は宣言の床 1024 MiB。
(val ROOM (- ALLOCATABLE PODS-USED))
(val ELDER-GROWTH (* 1536 MIB))
(val ELDER-LIMIT (+ JOBS-USED (- ROOM (* 2 ELDER-GROWTH))))
(val NEWER-LIMIT (+ JOBS-USED (- ROOM EVICTION-MARGIN-BYTES)))
(val STAT "anon {}\nfile 0\ninactive_file 0\n")


(defhandler node-a-api []
  "node-a の Node の宣言を答える偽の API server。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "29662296Ki"} "allocatable" {"memory" "20749400Ki"}}}))
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


(defk shared-files []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "2 つの世代が分け合う模擬の cgroup の file を作るため(kubepods・root・jobs の層 — page cache 0)と、keeper の世代の dir(印は無い)。"
  (val files #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str PODS-MAX)))
               (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str PODS-USED)))
               (MemoryFile :path (+ PODS "/memory.stat") :content (.encode (.format STAT PODS-USED)))
               (MemoryFile :path (+ HOST "/memory.stat") :content (.encode (.format STAT (+ PODS-USED (* 2 GIB)))))
               (MemoryFile :path (+ JOBS "/memory.current") :content (.encode (str JOBS-USED)))
               (MemoryFile :path (+ JOBS "/memory.stat") :content (.encode (.format STAT JOBS-USED)))
               (MemoryFile :path (+ JOBS "/memory.max") :content b"max\n")
               (MemoryFile :path TOKEN :content b"token\n")))
  (val parents (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                     :if (!= d f.path) d))
  (MemoryFiles :dirs (tuple (sorted (| parents #{"/keeper" "/keeper/gens" ELDER-DIR NEWER-DIR}))) :files files))


(defk settings-of [mark]
  {:pre [(: mark (| str None))] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "1 つの世代の見張りの線の設定を作るため。mark = keeper がこの世代を退かせる時に置く印の file(None = 印を読まない前の形)。"
  (<- cap (| int None) (layer-memory-max LAYOUT "jobs"))
  (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-a" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                    :jobs-cap-bytes cap :retired-file mark))


(defk newer-cell []
  {:pre [] :post [(: % EvictionCell)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "新しい世代の入れ物を作るため: heartbeat の返事で置き先が置いた task の要りを受けている。"
  (val cell (EvictionCell))
  (<- needs tuple (task-needs-of #({"id" "t1" "memoryMiB" NEED-MIB})))
  (setv cell.needs needs cell.uses #((JobUse :job "task/t1" :pid 20 :bytes TASK-USE :processes 1)))
  cell)


(defk elder-cell []
  {:pre [] :post [(: % EvictionCell)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "前の世代の入れ物を作るため: drain された後は heartbeat の返事を受けず、置いた task の要りを知らない(退いた handoff の process だけ)。
   起きてから長く、外の増えを測って余裕を宣言の床より上げている。"
  (val cell (EvictionCell))
  (setv cell.uses #((JobUse :job "handoff/retired-4" :pid 10 :bytes (* 256 MIB) :processes 1))
        cell.growths (* #(ELDER-GROWTH) MARGIN-MIN-SAMPLES))
  cell)


(defk both-ticked [newer elder newer-cell elder-cell from-ms ticks]
  {:pre [(: newer EvictionSettings) (: elder EvictionSettings) (: newer-cell EvictionCell) (: elder-cell EvictionCell) (: from-ms int)
         (: ticks int)]
   :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "2 つの世代の見張りの回の頭(eviction-noted)を、新しい世代・前の世代の順に 5 秒おきに ticks 回まわし、その後の jobs の層の memory.max を
   読むため。どちらの世代の heartbeat の測りも、その回に上に載る系が残す線を割った keep-room を読んでいる。"
  (for [now-ms (range from-ms (+ from-ms (* 5000 ticks)) 5000)]
    (for [cell #(newer-cell elder-cell)]
      (setv cell.keep-room KEEP-ROOM cell.keep-room-at-ms now-ms))
    (<- (eviction-noted newer newer-cell True now-ms))
    (<- (eviction-noted elder elder-cell True now-ms)))
  (<- files MemoryFiles (ReadMemoryFiles))
  (.decode (. (next (gfor f files.files :if (= f.path (+ JOBS "/memory.max")) f)) content) "utf-8"))


(defk retire-then-tick [newer elder newer-cell elder-cell]
  {:pre [(: newer EvictionSettings) (: elder EvictionSettings) (: newer-cell EvictionCell) (: elder-cell EvictionCell)]
   :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "keeper の版の入れ替えを模すため: 印の無い間に 2 回、keeper が前の世代の dir に印を置いた後に 3 回まわす。答え = #(印の前の
   memory.max 印の後の memory.max log の行の名)。"
  (<- before str (both-ticked newer elder newer-cell elder-cell 5000 2))
  (<- (WriteText ELDER-MARK ""))
  (<- after str (both-ticked newer elder newer-cell elder-cell 15000 3))
  (<- names tuple (ReadLogNames))
  #(before after names))


(defk run-world [newer elder newer-cell elder-cell]
  {:pre [(: newer EvictionSettings) (: elder EvictionSettings) (: newer-cell EvictionCell) (: elder-cell EvictionCell)]
   :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "分け合う模擬の file と Node の宣言の上で retire-then-tick を回すため。"
  (<- files MemoryFiles (shared-files))
  (<- answer tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (node-a-api) (log-kept)]
                     (retire-then-tick newer elder newer-cell elder-cell)))
  answer)


(deftest test-a-retired-generation-does-not-write-the-jobs-layer-under-the-newer-generations-reservation
  (<- newer EvictionSettings (settings-of NEWER-MARK))
  (<- elder EvictionSettings (settings-of ELDER-MARK))
  (<- young EvictionCell (newer-cell))
  (<- old EvictionCell (elder-cell))
  (<- answer tuple (run-world newer elder young old))
  (val before (get answer 0))
  (val after (get answer 1))
  (val names (get answer 2))
  ;; 新しい世代の見張りは、上に載る系が残す線を割った回も追い出しの線だけで上限を数え、置いた task の使い + 取り置きより上に残す。
  (assert (= young.pending PENDING) (// young.pending MIB))
  (assert (= young.jobs-limit NEWER-LIMIT) (// young.jobs-limit MIB))
  (assert (>= NEWER-LIMIT (+ JOBS-USED PENDING)) (// NEWER-LIMIT MIB))
  ;; 印の無い間は前の世代が書き手で、新しい世代は書かない — 前の世代の数えた値(取り置き 0・上げた余裕で数えた上限)が残る。
  (assert (< (int before) (+ JOBS-USED PENDING)) (// (int before) MIB))
  (assert (= (int before) ELDER-LIMIT) (// (int before) MIB))
  ;; keeper が印を置いた後は、前の世代は書かず、新しい世代の上限が残る。前の世代は線を数え続け(log と測りのため)、退きを 1 度だけ言う。
  (assert (= (int after) NEWER-LIMIT) (// (int after) MIB))
  (assert old.retired)
  (assert (not young.retired))
  (assert (is-not old.line None))
  (assert (= (.count names RETIRED-LOG) 1) names)
  ;; 新しい世代は前の世代が書いていると読んだ時と、居なくなったと読んだ時に 1 行ずつ言う。
  (assert (= (.count names ELDER-WRITES-LOG) 2) names))


(deftest test-the-old-shape-without-the-mark-lets-the-elder-overwrite-the-newer-limit
  ;; 失敗ケース: 印を読まない前の形(retired-file None)では、keeper が印を置いた後も前の世代が書き続け、最後の値は新しい世代に置いた
  ;; task の使い + 取り置きより下(前の世代の数えた上限)。
  (<- newer EvictionSettings (settings-of None))
  (<- elder EvictionSettings (settings-of None))
  (<- young EvictionCell (newer-cell))
  (<- old EvictionCell (elder-cell))
  (<- answer tuple (run-world newer elder young old))
  (val after (get answer 1))
  (val names (get answer 2))
  (assert (= (int after) ELDER-LIMIT) (// (int after) MIB))
  (assert (< ELDER-LIMIT (+ JOBS-USED PENDING)) (// ELDER-LIMIT MIB))
  (assert (not old.retired))
  (assert (not-in RETIRED-LOG names) names))


(defeffect WriterIs
  "これから見張りの回を回す世代の名を告げる — テストだけの effect。"
  {:fields [(: name str)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect ReadWrites
  "jobs の層の memory.max への書きを、書いた世代の名と値の組で書いた順に問う — テストだけの effect。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler jobs-writes-kept []
  "jobs の層の memory.max への書きを、その時に回していた世代の名と組で溜め(書き自体は外の模擬の file へ渡す)、ReadWrites に答える答え手。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var writer "")
  (session var writes #())
  (WriterIs [name]
    (:= writer name)
    (resume None))
  (WriteText [path text]
    :when (= path (+ JOBS "/memory.max"))
    (:= writes (+ writes #(#(writer (int text)))))
    (<- answer (| FileFailed None) effect)
    (resume answer))
  (ReadWrites []
    (resume writes)))


(defk switch-ticked [newer elder newer-cell elder-cell]
  {:pre [(: newer EvictionSettings) (: elder EvictionSettings) (: newer-cell EvictionCell) (: elder-cell EvictionCell)]
   :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "keeper の版の入れ替えを模すため: 新しい世代が起きてから ready になるまで(印の無い間)3 回、keeper が前の世代の dir に印を置いた後に
   3 回、どちらも新しい世代・前の世代の順に 5 秒おきに回す。答え = #(書きの組の tuple 最後の memory.max)。"
  (for [#(at-ms marked) #(#(5000 False) #(10000 False) #(15000 False) #(20000 True) #(25000 True) #(30000 True))]
    (when (and marked (not elder-cell.retired))
      (<- (WriteText ELDER-MARK "")))
    (for [cell #(newer-cell elder-cell)]
      (setv cell.keep-room KEEP-ROOM cell.keep-room-at-ms at-ms))
    (<- (WriterIs "newer"))
    (<- (eviction-noted newer newer-cell True at-ms))
    (<- (WriterIs "elder"))
    (<- (eviction-noted elder elder-cell True at-ms)))
  (<- writes tuple (ReadWrites))
  (<- files MemoryFiles (ReadMemoryFiles))
  #(writes (int (.decode (. (next (gfor f files.files :if (= f.path (+ JOBS "/memory.max")) f)) content) "utf-8"))))


(deftest test-only-one-generation-writes-the-jobs-layer-across-the-switch
  ;; 実例(2026-11-10): 2 つの世代の見張りが同じ jobs の層の memory.max を 5 秒ごとに書き合い、上限が新しい世代の値と前の世代の縮めの値
  ;; (0 も)の間を行き来した。前の世代にも置いた task が在る(要り 2048 MiB・使い 256 MiB — 取り置きは上げた余裕で数えた空きの内)。
  (<- newer EvictionSettings (settings-of NEWER-MARK))
  (<- elder EvictionSettings (settings-of ELDER-MARK))
  (<- young EvictionCell (newer-cell))
  (<- old EvictionCell (elder-cell))
  (<- elder-needs tuple (task-needs-of #({"id" "t0" "memoryMiB" 2048})))
  (setv old.needs elder-needs old.uses #((JobUse :job "task/t0" :pid 10 :bytes (* 256 MIB) :processes 1)))
  (<- files MemoryFiles (shared-files))
  (<- answer tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (node-a-api) (log-kept)
                                   (jobs-writes-kept)]
                     (switch-ticked newer elder young old)))
  (val writers (tuple (gfor #(who _) (get answer 0) who)))
  ;; 印の前の 3 回は前の世代だけ、印の後の 3 回は新しい世代だけが書く — 書き手は 1 度だけ替わり、交互に書く回が無い。
  (assert (= writers #("elder" "elder" "elder" "newer" "newer" "newer")) (get answer 0))
  (assert (= (tuple (gfor #(who limit) (get answer 0) :if (= who "elder") limit)) #(ELDER-LIMIT ELDER-LIMIT ELDER-LIMIT)) (get answer 0))
  (assert (= (get answer 1) NEWER-LIMIT) (// (get answer 1) MIB))
  (assert (is young.elder None))
  (assert young.elder-gone))


(deftest test-the-oldest-unmarked-generation-is-the-writer
  ;; 書き手の決まり: 自分より前に起きた印の無い世代のいちばん古い 1 つ。後に起きた世代と、形の違う名は数えない。
  (<- none-older (| str None) (writing-elder-of "pod1.2000" #("pod1.3000")))
  (<- oldest (| str None) (writing-elder-of "pod1.3000" #("pod1.2000" "pod1.1000" "pod1.4000")))
  (<- odd-names (| str None) (writing-elder-of "pod1.2000" #("pod1" "pod1.x10" "")))
  (<- odd-own (| str None) (writing-elder-of "local" #("pod1.1000")))
  (assert (is none-older None))
  (assert (= oldest "pod1.1000") oldest)
  (assert (is odd-names None))
  (assert (is odd-own None)))
