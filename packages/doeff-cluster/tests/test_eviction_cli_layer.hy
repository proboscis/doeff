;; 会話の CLI の層(clis)の memory の上限を、追い出しまでの空きと container の中の空きから掛け直す — 模擬の検。
;;
;; 実例(11-06 19:00〜21:30 JST の調べ): node-c と node-a が会話を受ける本数は機体の空きでなく、clis の静的な
;; memory.max(node-c 5G・node-a 1536M)で止まっていた。node-a は機体の空き 8〜10 GiB のうち会話 1 本ぶんしか使えず、node-h が混んだ間に
;; 始まった会話 48 本のうち node-c と node-a が受けたのは 12 本だった。
;;   - 判断(worker/core/eviction_rules の clis-limit・clis-lent・box-free・jobs-box-limit): clis の今の使いの上に残すのは需要の余白
;;     (会話の CLI の新しい受け k 本分)のうち 2 つの空きの小さい方の内の量だけ・前の回の上限を持ち越さない・床(宣言の静的な値)と clis の
;;     今の使いより下げない・天井で止める。jobs と clis の外の限る層は、今掛けている上限 − 層の working set だけを container の中の空きから
;;     取り置く(余地の宣言の在る層は回収で返らない量 + 余地に掛け直す)。
;;   - worker の読み(worker/protocol/eviction_line の eviction-noted): clis の層の memory.max を書き、床を越えて貸した量を jobs の上限から
;;     引き、線を読めない回は床へ戻す。
;;   - 模擬の機体(node-a の値): 本物の見張りの背景の task を仮想時計の下で 5 秒ごとに回し、kubepods・root・container・jobs・clis の層の
;;     cgroup の file は模擬の kernel が答える。会話は turn-host の受けの形(余白 = clis の上限 − clis の使い・余白 ÷ 1 GiB − 120 秒の内に
;;     受けた本数 ≥ 1 なら 1 本受ける)で始まり、60 秒で 1 GiB へ伸びる。模擬の kernel は層の使いが見張りの書いた memory.max を越えた
;;     会話の CLI と task を層の中で OOM kill する。ほかの Pod が 30 秒で 4 GiB 跳ねても(11-06 21:00 の node-c の probe の形を node-a の
;;     大きさに縮めた)、会話の CLI は 1 本も殺されず、Pod の和は allocatable を越えず、上限は跳ねの後に床の側へ縮む。床だけの worker
;;     (天井の宣言が無い — 前の形)は同じ機体で会話を 1 本しか受けない(対照)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass replace])  ; dataclass は defrecord の展開が名指す・replace は設定の欄を替える
(import json)
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect ReadText WriteText HttpRequest HttpResponse])
(import doeff_core_effects [state slog-discard-handler])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [ProcRow])
(import doeff_core_effects [scripted-proc-table-handler])
(import doeff_time [DelayEffect SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ObserveJobRoots])
(import doeff_cluster._worker.core.memory_rules [JobRoot JobUse MIB])
(import doeff_cluster._worker.core.eviction_rules [CLI-TURN-BYTES CLI-TURN-SLOTS CLIS-ROOM-SHARE UNDECLARED-NEED-BYTES EvictionLine LineKind LimitedLayer
                                                  TaskNeed clis-limit clis-lent box-free jobs-box-limit jobs-limit
                                                  layer-memory-max layout-limited-layers layer-hold
                                                  placed-pending task-needs-of])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell CliLayerSettings BoxLayerSettings BoxLayerLimit
                                                     CommandLayerSettings eviction-noted CLIS-LENT-LOG JOBS-SQUEEZE-LOG])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop])
(import doeff_cluster._worker.entry.main [cli-layer-settings-of cli-ceiling-environ jobs-ceiling-bytes CLI-CEILING-CHILD-VAR CLI-KEPT-FILE-CHILD-VAR])
(import doeff_cluster._worker.entry.main [box-layers-of cli-turn-room-bytes])

(val GIB (* 1024 MIB))
;; node-a の Node の宣言(test_eviction_line.hy と同じ 11-01 の値)と、cgroup の層の宣言(nodes/node-a/cgroup.yaml)・container の上限 22Gi。
(val CAPACITY (* 29662296 1024))
(val ALLOCATABLE (* 20749400 1024))
(val PODS-MAX (- CAPACITY (* 7 GIB)))
(val NODE-A-LAYOUT (+ "worker:memory.min=256M,cpu.weight=1000 services:memory.min=256M,cpu.weight=1000 jobs:memory.max=10752M,cpu.weight=100 "
                     "clis:memory.max=1536M,cpu.weight=100 commands:memory.max=1536M,cpu.weight=100 exec:memory.max=512M,cpu.weight=1000"))
(val NODE-C-LAYOUT (+ "worker:memory.min=1G,cpu.weight=1000 services:memory.min=24G,cpu.weight=1000 jobs:memory.max=8G,cpu.weight=100 "
                      "clis:memory.max=10G,cpu.weight=100 commands:memory.max=4608M,cpu.weight=100 exec:memory.max=512M,cpu.weight=1000"))
(val CONTAINER-MAX (* 22 GIB))
(val FLOOR (* 1536 MIB))
(val CEILING (* 6 GIB))
;; 模擬の機体の path(worker の container の cgroup は systemd の driver の形)。
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN "/jobs"))
(val CLIS (+ OWN "/clis"))
(val COMMANDS (+ OWN "/commands"))
(val EXEC (+ OWN "/exec"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/node-a")
;; 会話 1 本に受け手(turn-host)が見込む量(上に載る系の機体の宣言 TURN_HOST_TURN_RAM_MIB)と、受けた本数を数える窓
;; (cli_host_room_rules の 120 秒)。
(val NEED GIB)
(val RECENT-MS 120000)
;; 宣言の無い機体の需要の余白(1 本の要り × k)と、模擬の node-a の機体の宣言の余白(allocatable 約 20 GiB の小さい機体 — 1 GiB × 2 本。
;; 宣言の無い 4 本では、ほかの Pod の 4 GiB の跳ねを jobs の層だけで吸えない本数の会話を受ける)。
(val TURN-ROOM (* CLI-TURN-SLOTS CLI-TURN-BYTES))
(val NODE-A-TURN-ROOM (* 2 GIB))


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-the-cli-layer-keeps-only-its-demand-room-above-its-use-between-its-floor-and-its-ceiling
  ;; 空き 10 GiB・container の中の空きは大きい: clis の今の使い 1 GiB の上に需要の余白 4 GiB(1 本の要り 1 GiB × 4 本)だけを残す
  ;; (残りの 6 GiB は jobs の層へ)。空きの半分ではない。
  (<- grown int (clis-limit GIB (* 10 GIB) (* 12 GIB) FLOOR CEILING TURN-ROOM 0))
  (assert (= grown (+ GIB TURN-ROOM)) (/ grown GIB))
  ;; 空きが余っても使いの上は余白まで(天井 6 GiB に張り付かない)。余白が天井を越えれば天井で止まる。空きが無ければ床(今までの静的な上限)。
  (<- roomy int (clis-limit GIB (* 20 GIB) (* 20 GIB) FLOOR CEILING TURN-ROOM 0))
  (<- capped int (clis-limit (* 3 GIB) (* 20 GIB) (* 20 GIB) FLOOR CEILING TURN-ROOM 0))
  (<- none int (clis-limit (* 512 MIB) (- GIB) (* 12 GIB) FLOOR CEILING TURN-ROOM 0))
  (assert (= #(roomy capped none) #((+ GIB TURN-ROOM) CEILING FLOOR)) #(roomy capped none))
  ;; 空きが少ない回は、余白を空きの CLIS-ROOM-SHARE で切る(残りの半分は jobs の層の受けへ)。container の中の空きが線の空きより小さければ、
  ;; そちらで数える。container に上限が無ければ線の空きだけ。
  (<- boxed int (clis-limit GIB (* 6 GIB) (* 2 GIB) FLOOR CEILING TURN-ROOM 0))
  (<- unboxed int (clis-limit GIB (* 6 GIB) None FLOOR CEILING TURN-ROOM 0))
  (assert (= #(boxed unboxed) #((* 2 GIB) (* 4 GIB))) #((/ boxed GIB) (/ unboxed GIB)))
  ;; 使いが天井を越えていても、使いより下げない。
  (<- over int (clis-limit (* 7 GIB) (* 6 GIB) None FLOOR CEILING TURN-ROOM 0))
  (assert (= over (* 7 GIB)) (/ over GIB)))


(deftest test-unused-cli-lending-goes-back-to-the-jobs-intake-on-the-next-tick
  ;; 失敗ケース(前の形): 広げた上限を、前の回の上限までの残りが空きの内である間は持ち続けたので、会話の CLI が使わなくなった貸しが jobs の
  ;; 層の受けから引かれ続けた(node-c の 11-11 16:4x JST — 上限 16 GiB のうち使われていない 6771 MiB)。今の形は前の回の上限を入力に持たず、
  ;; 使いが 6 GiB から 1 GiB へ減った次の回の上限は 1 GiB + 余白(床の上)。
  (val room (* 12 GIB))
  (<- shrunk int (clis-limit GIB room room FLOOR (* 16 GIB) TURN-ROOM 0))
  (assert (= shrunk (+ GIB TURN-ROOM)) (/ shrunk GIB))
  ;; 返った貸しは、同じ container の中の空きから決まる jobs の層の受け(jobs-box-limit — 新しい受けの上限)に入る: 前の形の上限 10 GiB を
  ;; 残すより 5 GiB 大きい。
  (<- intake int (jobs-box-limit 0 room 0 shrunk GIB 0))
  (<- kept-intake int (jobs-box-limit 0 room 0 (* 10 GIB) GIB 0))
  (assert (= #(intake (- intake kept-intake)) #((- room TURN-ROOM) (* 5 GIB))) #((/ intake GIB) (/ kept-intake GIB)))
  ;; ほかの Pod が跳ねて空きが 0.5 GiB になった: 余白も空きの半分の 0.25 GiB だけ(jobs の層を縮めて残さない)。空きが負: clis の今の使いまで。
  (<- tight int (clis-limit (* 3 GIB) (// GIB 2) (* 12 GIB) FLOOR CEILING TURN-ROOM 0))
  (<- frozen int (clis-limit (* 3 GIB) (* -4 GIB) (* 12 GIB) FLOOR CEILING TURN-ROOM 0))
  (assert (= #(tight frozen) #((int (* 3.25 GIB)) (* 3 GIB))) #((/ tight GIB) (/ frozen GIB)))
  ;; 床を越えて貸した量 = 上限 − 床と今の使いの大きい方(jobs の層の上限から引く)。
  (<- lent int (clis-lent (* 4 GIB) (* 3 GIB) FLOOR))
  (<- inside int (clis-lent FLOOR (* 512 MIB) FLOOR))
  (assert (= #(lent inside) #(GIB 0)) #(lent inside)))


;; node-a の allocatable の線で、追い出しまでの空きが room の線を作るため(test_eviction_line.hy の line と同じ形)。
(val room-line (fn [room] (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes ALLOCATABLE :used-bytes (- ALLOCATABLE room) :room-bytes room)))


;; node-a の 11-09 23:00〜23:21 JST の形: clis の上限 6 GiB・使い約 1 GiB(使われていない貸し 4.5 GiB)・jobs の層に走る task 4 GiB・
;; 機体の宣言の余裕 1463 MiB が測りで 3374 MiB へ上がった。
(val NODE-A-FLOOR-MARGIN (* 1463 MIB))
(val NODE-A-MEASURED-MARGIN (* 3374 MIB))


(deftest test-unused-cli-lending-is-returned-before-the-jobs-layer-goes-below-its-use
  ;; 失敗ケース: 前の形は広げた clis の上限を「jobs の層を 0 まで縮めれば収まる間」は残したので、空きが 3.5 GiB − 余裕でも clis は 6 GiB の
  ;; まま、貸した 4.5 GiB を jobs の上限から引き、jobs の上限が走っている task の使い 4 GiB より下がった(kernel が task を OOM で止めた)。
  (val jobs-current (* 4 GIB))
  (val room (int (* 3.5 GIB)))
  (val free (- room NODE-A-MEASURED-MARGIN))
  (<- clis int (clis-limit GIB free None FLOOR CEILING TURN-ROOM 0))
  (<- lent int (clis-lent clis GIB FLOOR))
  ;; 使われていない貸しは空きの内まで返る: 1 GiB + 空き 210 MiB は床の下なので床へ戻り、床を越えた貸しは 0。
  (assert (= #(clis lent) #(FLOOR 0)) #((// clis MIB) (// lent MIB)))
  (<- jobs int (jobs-limit jobs-current (room-line room) NODE-A-MEASURED-MARGIN NODE-A-FLOOR-MARGIN None lent 0))
  (assert (>= jobs jobs-current) (// jobs MIB))
  ;; 空きが測った余裕を割っても(2 GiB)、宣言の余裕で数えた空きが残る間は jobs の上限を使いより下げない — 余裕の上がりは新しい受けで吸う:
  ;; 新しい受けに名乗る上限(測った余裕で数えた値)は使いより下で、置き先は置かない。
  (val tight (room-line (* 2 GIB)))
  (<- held int (jobs-limit jobs-current tight NODE-A-MEASURED-MARGIN NODE-A-FLOOR-MARGIN None 0 0))
  (<- intake int (jobs-limit jobs-current tight NODE-A-MEASURED-MARGIN NODE-A-MEASURED-MARGIN None 0 0))
  (assert (= held jobs-current) (// held MIB))
  (assert (< intake jobs-current) (// intake MIB))
  ;; 置いた task の取り置き 1 GiB は、宣言の余裕で数えた空き(2 GiB − 1463 MiB)の内で jobs の層に残す。
  (<- promised int (jobs-limit jobs-current tight NODE-A-MEASURED-MARGIN NODE-A-FLOOR-MARGIN None 0 GIB))
  (assert (= promised (+ jobs-current (- (* 2 GIB) NODE-A-FLOOR-MARGIN))) (// promised MIB))
  ;; jobs の使いより下げるのは、clis の貸しを返しきっても宣言の余裕で数えた空きが負の時だけ(1 GiB < 1463 MiB)。
  (<- squeezed int (jobs-limit jobs-current (room-line GIB) NODE-A-MEASURED-MARGIN NODE-A-FLOOR-MARGIN None 0 GIB))
  (assert (= squeezed (- (+ jobs-current GIB) NODE-A-FLOOR-MARGIN)) (// squeezed MIB)))


(deftest test-the-need-of-placed-tasks-is-not-lent-to-the-cli-layer
  ;; 置き先が置いた task の要り(heartbeat の返事の行の memoryMiB — 名乗らない行は UNDECLARED-NEED-BYTES)。id の無い行は読まない。
  (<- needs tuple (task-needs-of #({"id" "t1" "memoryMiB" 3584} {"id" "t2"} {"id" "t3" "memoryMiB" True} {"name" "no-id"})))
  (assert (= needs #((TaskNeed :id "t1" :bytes (* 3584 MIB)) (TaskNeed :id "t2" :bytes UNDECLARED-NEED-BYTES)
                     (TaskNeed :id "t3" :bytes UNDECLARED-NEED-BYTES)))
          needs)
  ;; 取り置き = task ごとの max(0, 要り − 今の使い): t1 は 1 GiB 使い 2.5 GiB 残り・t2 は使い無しで全部・t3 は要りを越えて使い 0。
  (val uses #((JobUse :job "task/t1" :pid 20 :bytes GIB :processes 3) (JobUse :job "task/t3" :pid 30 :bytes (* 2 GIB) :processes 1)
              (JobUse :job "svc" :pid 10 :bytes (* 4 GIB) :processes 1)))
  (<- pending int (placed-pending needs uses))
  (assert (= pending (+ (* 2560 MIB) UNDECLARED-NEED-BYTES)) (// pending MIB))
  ;; 空き 6 GiB から clis に新しく貸すのは、取り置きの外の空き(6 GiB − 3.5 GiB)の半分の内の余白だけ。
  (<- grown int (clis-limit GIB (* 6 GIB) None FLOOR CEILING TURN-ROOM pending))
  (assert (= grown (+ GIB (int (* (- (* 6 GIB) pending) CLIS-ROOM-SHARE)))) (// grown MIB))
  ;; 取り置きで空きが埋まれば clis の今の使いと床へ戻る。
  (<- returned int (clis-limit GIB (* 3 GIB) None FLOOR CEILING TURN-ROOM (* 3 GIB)))
  (assert (= returned FLOOR) (// returned MIB))
  ;; jobs の層の上限は取り置きを入れて、新しい受けに名乗る上限は入れない(置き先が自分で予約を引く — 同じ空きを 2 度数えない)。
  (val line (room-line (* 7 GIB)))
  (val free (- (* 7 GIB) GIB))
  (<- lent int (clis-lent grown GIB FLOOR))
  (<- jobs int (jobs-limit (// GIB 2) line GIB GIB None lent pending))
  (<- intake int (jobs-limit (// GIB 2) line GIB GIB None lent 0))
  (assert (>= (- jobs (// GIB 2)) pending) (// jobs MIB))
  (assert (= intake (- (+ (// GIB 2) free) lent)) (// intake MIB)))


(deftest test-the-container-room-counts-the-other-limited-layers-and-bounds-the-jobs-layer
  ;; jobs と clis の外の限る層: node-a は commands 1536M・exec 512M・node-c は commands 4608M・exec 512M。
  (<- node-a-layers tuple (layout-limited-layers NODE-A-LAYOUT #("jobs" "clis")))
  (<- node-c-layers tuple (layout-limited-layers NODE-C-LAYOUT #("jobs" "clis")))
  (assert (= node-a-layers #((LimitedLayer :name "commands" :cap-bytes FLOOR) (LimitedLayer :name "exec" :cap-bytes (* 512 MIB))))
          node-a-layers)
  (assert (= (sum (gfor l node-c-layers l.cap-bytes)) (* 5120 MIB)) node-c-layers)
  ;; 層の取り置き = 今掛けている上限 − 層の working set(使いは container の working set に数え済み)。使いのない層は上限の全部。
  (<- commands-hold int (layer-hold FLOOR 0))
  (<- exec-hold int (layer-hold (* 512 MIB) 0))
  (val node-a-held (+ commands-hold exec-hold))
  (<- used int (layer-hold FLOOR (* 1024 MIB)))
  (<- over int (layer-hold FLOOR (* 2 GIB)))
  (assert (= #(node-a-held used over) #((* 2 GIB) (* 512 MIB) 0)) #(node-a-held used over))
  ;; container の中の空き = 上限 − working set − 余裕 − held。上限の無い container は None。
  (<- box int (box-free CONTAINER-MAX (* 5 GIB) GIB node-a-held))
  (<- open (| int None) (box-free None (* 5 GIB) GIB node-a-held))
  (assert (= #(box open) #((* 14 GIB) None)) #(box open))
  ;; jobs の層の上限は、clis の上限までの残りを container の中の空きから引いた分まで。
  (<- jobs int (jobs-box-limit GIB (* 14 GIB) 0 (* 5 GIB) (* 2 GIB) 0))
  (assert (= jobs (* 12 GIB)) (/ jobs GIB))
  ;; container の中の空きが clis の残りを割っても、置いた task の取り置きは測った余裕と宣言の余裕の差 slack の内で残す。
  (<- held-for-tasks int (jobs-box-limit GIB (* 2 GIB) GIB (* 5 GIB) (* 2 GIB) (* 2 GIB)))
  (<- intake int (jobs-box-limit GIB (* 2 GIB) 0 (* 5 GIB) (* 2 GIB) 0))
  (assert (= #(held-for-tasks intake) #(GIB 0)) #((/ held-for-tasks GIB) (/ intake GIB))))


(deftest test-the-ceiling-is-declared-per-machine-and-passed-to-the-job-children
  (val env {"WORKER_CLI_LAYER_CEILING" "6G"})
  (<- clis (| CliLayerSettings None) (cli-layer-settings-of env OWN NODE-A-LAYOUT "jobs"))
  (assert (= #(clis.dir clis.floor-bytes clis.ceiling-bytes clis.container-dir clis.turn-room-bytes)
             #(CLIS FLOOR CEILING OWN TURN-ROOM))
          clis)
  (assert (= clis.layers #((BoxLayerSettings :name "commands" :dir COMMANDS :cap-bytes FLOOR)
                           (BoxLayerSettings :name "exec" :dir EXEC :cap-bytes (* 512 MIB))))
          clis.layers)
  (<- environ dict (cli-ceiling-environ env))
  (assert (= environ {CLI-CEILING-CHILD-VAR (str CEILING)}) environ)
  ;; 宣言の無い機体は掛け直さず、子へも渡さない。
  (<- absent (| CliLayerSettings None) (cli-layer-settings-of {} OWN NODE-A-LAYOUT "jobs"))
  (<- quiet dict (cli-ceiling-environ {}))
  (assert (= #(absent quiet) #(None {})) #(absent quiet))
  ;; 天井が床より小さい・clis の層の床が無い宣言は起動を断る。
  (for [#(ceiling layout) #(#("1G" NODE-A-LAYOUT) #("6G" "jobs:memory.max=1G"))]
    (var refused False)
    (try
      (<- (cli-layer-settings-of {"WORKER_CLI_LAYER_CEILING" ceiling} OWN layout "jobs"))
      (except [ValueError]
        (:= refused True)))
    (assert refused #(ceiling layout))))


;; --- worker の読み(eviction-noted)--------------------------------------------------------------------------------------------------

(defhandler node-a-api []
  "node-a の Node の宣言を答える偽の API server。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "29662296Ki"} "allocatable" {"memory" "20749400Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))))


;; page cache の無い cgroup の memory.stat の中身の形(anon の byte を入れる)。
(val STAT "anon {}\nfile 0\ninactive_file 0\n")


(defk node-a-files [pods jobs clis container]
  {:pre [(: pods int) (: jobs int) (: clis int) (: container int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の cgroup と ServiceAccount の file を作るため: kubepods・jobs・clis・container の使い(page cache 0)と、root は Pod の外に
   2 GiB。jobs と clis の外の限る層(commands・exec)は使い 0 で宣言の静的な上限のまま。"
  (val files #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str PODS-MAX)))
               (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str pods)))
               (MemoryFile :path (+ PODS "/memory.stat") :content (.encode (.format STAT pods)))
               (MemoryFile :path (+ HOST "/memory.stat") :content (.encode (.format STAT (+ pods (* 2 GIB)))))
               (MemoryFile :path (+ OWN "/memory.max") :content (.encode (str CONTAINER-MAX)))
               (MemoryFile :path (+ OWN "/memory.current") :content (.encode (str container)))
               (MemoryFile :path (+ OWN "/memory.stat") :content (.encode (.format STAT container)))
               (MemoryFile :path (+ JOBS "/memory.current") :content (.encode (str jobs)))
               (MemoryFile :path (+ JOBS "/memory.stat") :content (.encode (.format STAT jobs)))
               (MemoryFile :path (+ JOBS "/memory.max") :content b"max\n")
               (MemoryFile :path (+ CLIS "/memory.current") :content (.encode (str clis)))
               (MemoryFile :path (+ CLIS "/memory.stat") :content (.encode (.format STAT clis)))
               (MemoryFile :path (+ CLIS "/memory.max") :content (.encode (str FLOOR)))
               (MemoryFile :path (+ COMMANDS "/memory.current") :content b"0\n")
               (MemoryFile :path (+ COMMANDS "/memory.stat") :content (.encode (+ (.format STAT 0) "shmem 0\nslab_reclaimable 0\n")))
               (MemoryFile :path (+ COMMANDS "/memory.max") :content (.encode (str FLOOR)))
               (MemoryFile :path (+ EXEC "/memory.current") :content b"0\n")
               (MemoryFile :path (+ EXEC "/memory.stat") :content (.encode (+ (.format STAT 0) "shmem 0\nslab_reclaimable 0\n")))
               (MemoryFile :path (+ EXEC "/memory.max") :content (.encode (str (* 512 MIB))))
               (MemoryFile :path TOKEN :content b"token\n")))
  (val dirs (sorted (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                          :if (!= d f.path) d)))
  (MemoryFiles :dirs (tuple dirs) :files files))


(defk node-a-layers [floors]
  {:pre [(: floors str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の jobs と clis の外の限る層の設定を、機体の宣言の層の余地 floors(WORKER_LAYER_FLOORS の綴り — 空 = 掛け直さない)から
   作るため(入口と同じ組み立て)。"
  (<- layers tuple (box-layers-of {"WORKER_LAYER_FLOORS" floors} OWN NODE-A-LAYOUT "jobs"))
  layers)


(defk node-a-settings [clis]
  {:pre [(: clis bool)] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の線の設定を作るため。clis = 会話の CLI の層を掛け直すか(偽 = 天井の宣言の無い前の形)。"
  (<- jobs-cap (| int None) (layer-memory-max NODE-A-LAYOUT "jobs"))
  (<- layers tuple (node-a-layers ""))
  (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-a" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                    :jobs-cap-bytes jobs-cap
                    :clis (if clis (CliLayerSettings :dir CLIS :floor-bytes FLOOR :ceiling-bytes CEILING :container-dir OWN
                                    :turn-room-bytes NODE-A-TURN-ROOM :layers layers)
                              None)))


(defk ticked-files [settings cell ticks]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: ticks int)] :post [(: % MemoryFiles)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の頭(eviction-noted)を 5 秒おきに ticks 回まわし、その後の模擬の file を読むため。"
  (for [i (range ticks)]
    (<- (eviction-noted settings cell True (* 5000 (+ i 1)))))
  (<- files MemoryFiles (ReadMemoryFiles))
  files)


(defk content-at [files path]
  {:pre [(: files MemoryFiles) (: path str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file の path の中身を文字で読むため。"
  (.decode (. (next (gfor f files.files :if (= f.path path) f)) content) "utf-8"))


(deftest test-the-worker-writes-the-cli-layer-limit-and-lends-it-out-of-the-jobs-layer
  ;; 平常の node-a: Pod の和 12 GiB(空き 7.79 GiB)・jobs 0.5 GiB・clis 1 GiB・container 3 GiB。余裕は宣言の無い機体の 1 GiB。
  (val pods (* 12 GIB))
  (val room (- ALLOCATABLE pods))
  (val cell (EvictionCell))
  (<- start MemoryFiles (node-a-files pods (// GIB 2) GIB (* 3 GIB)))
  (<- settings EvictionSettings (node-a-settings True))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                          (ticked-files settings cell 2)))
  ;; clis の上限 = 1 GiB + 需要の余白(空き − 余裕の内)。jobs の上限 = jobs の使い + 空き − 余裕 − 床を越えて貸した量。
  (val free (- room GIB))
  (val clis-max (+ GIB (min NODE-A-TURN-ROOM (int (* free CLIS-ROOM-SHARE)))))
  (<- written str (content-at files (+ CLIS "/memory.max")))
  (assert (= (int written) clis-max) (/ (int written) GIB))
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (= (int jobs-max) (- (+ (// GIB 2) free) (- clis-max FLOOR))) (/ (int jobs-max) GIB))
  ;; 2 つの層が上限まで伸びても、Pod の和は線 − 余裕の内(同じ空きを重ねて貸さない)。重ねて数えるのは、今までと同じ床の内の残り
  ;; (床 1.5 GiB − clis の使い 1 GiB)だけ。
  (assert (<= (+ (- pods (// GIB 2) GIB) (int jobs-max) clis-max) (+ (- ALLOCATABLE GIB) (- FLOOR GIB))))
  (assert (= #(cell.clis-limit cell.lent) #(clis-max (- clis-max FLOOR))) #(cell.clis-limit cell.lent))
  ;; 前の形(天井の宣言が無い)は clis の層に書かず、jobs の上限は空きの全部。
  (<- old-settings EvictionSettings (node-a-settings False))
  (<- before MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                           (ticked-files old-settings (EvictionCell) 1)))
  (<- untouched str (content-at before (+ CLIS "/memory.max")))
  (<- old-jobs str (content-at before (+ JOBS "/memory.max")))
  (assert (= #((int untouched) (int old-jobs)) #(FLOOR (+ (// GIB 2) free))) #(untouched old-jobs)))


(deftest test-the-worker-keeps-the-need-of-placed-tasks-out-of-the-cli-layer
  ;; 平常の node-a(上の検と同じ読み)に、置き先が要り 3584 MiB の task を置き、その task はまだ 512 MiB しか使っていない(取り置き 3 GiB)。
  (val pods (* 12 GIB))
  (val room (- ALLOCATABLE pods))
  (val cell (EvictionCell))
  (<- needs tuple (task-needs-of #({"id" "t1" "memoryMiB" 3584})))
  (setv cell.needs needs cell.uses #((JobUse :job "task/t1" :pid 20 :bytes (// GIB 2) :processes 1)))
  (<- start MemoryFiles (node-a-files pods (// GIB 2) GIB (* 3 GIB)))
  (<- settings EvictionSettings (node-a-settings True))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                          (ticked-files settings cell 2)))
  (val free (- room GIB))
  (val pending (* 3 GIB))
  ;; clis に貸すのは取り置きの外の空きの内の余白だけ。
  (<- written str (content-at files (+ CLIS "/memory.max")))
  (assert (= (int written) (+ GIB (min NODE-A-TURN-ROOM (int (* (- free pending) CLIS-ROOM-SHARE))))) (// (int written) MIB))
  (assert (= cell.pending pending) (// cell.pending MIB))
  ;; jobs の層の上限は取り置きを覆い、置き先に名乗る受けの上限から取り置きを引いた空き(置き先が自分で引く)と clis の余白は、
  ;; 取り置きの外の空きを分け合う(同じ空きを 2 つの受けが重ねて約束しない — 重なるのは今までと同じ床の内の残りだけ)。
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (>= (- (int jobs-max) (// GIB 2)) pending) (// (int jobs-max) MIB))
  (val taken (- cell.jobs-intake (// GIB 2) pending))
  (assert (<= (+ taken (- (int written) GIB)) (+ (- free pending) (- FLOOR GIB))) #((// taken MIB) (// (int written) MIB))))


(deftest test-an-unreadable-line-puts-the-cli-layer-back-on-its-floor-but-not-below-its-use
  ;; Node の宣言を読めない機体(API server が無い答え手 — 線を読めない): clis の上限は床と clis の今の使いの大きい方。
  (<- small MemoryFiles (node-a-files (* 12 GIB) (// GIB 2) GIB (* 3 GIB)))
  (<- big MemoryFiles (node-a-files (* 12 GIB) (// GIB 2) (* 3 GIB) (* 5 GIB)))
  (<- settings EvictionSettings (node-a-settings True))
  (<- floor-files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler small) (unread-api) slog-discard-handler]
                                (ticked-files settings (EvictionCell) 1)))
  (<- used-files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler big) (unread-api) slog-discard-handler]
                               (ticked-files settings (EvictionCell) 1)))
  (<- at-floor str (content-at floor-files (+ CLIS "/memory.max")))
  (<- at-use str (content-at used-files (+ CLIS "/memory.max")))
  (assert (= #((int at-floor) (int at-use)) #(FLOOR (* 3 GIB))) #(at-floor at-use)))


(defhandler unread-api []
  "Node の読みを断る偽の API server(線を読めない)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    (resume (HttpResponse 503 {} b"down" "down" url 0.0))))


;; --- 模擬の機体(本物の見張り・会話の受け・模擬の kernel)------------------------------------------------------------------------

(defrecord Scene
  "模擬の node-a の memory の台本(byte と ms): others = 始めのほかの Pod の working set・spike / spike-from / spike-span / spike-until =
   ほかの Pod が spike-from から spike-span の間に spike だけ一様に伸び、spike-until から同じ間で戻る・resident = worker の本体と常駐の
   job(services の層)・jobs = jobs の層の task の使い・idle = 会話の CLI の層の、ターンの外の使い(使われていない CLI と daemon)・
   ramp-ms = 受けた会話が 0 から NEED へ伸びる間・admit-every-ms = turn-host が受けを判じる間・admit-until-ms = その刻の後は受けない・
   system = 機体の Pod の外の使い。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ int others)
  (#^ int spike)
  (#^ int spike-from)
  (#^ int spike-span)
  (#^ int spike-until)
  (#^ int resident)
  (#^ int jobs)
  (#^ int idle)
  (setv #^ int ramp-ms 60000)
  (setv #^ int admit-every-ms 10000)
  (setv #^ int admit-until-ms 10000000)
  (setv #^ int system (* 2 GIB)))


(defrecord SceneSeen
  "模擬の機体を回した後の世界: highest = 読みの刻ごとの Pod の working set の和の最大・cli-kills = clis の層の中で止めた会話の CLI の数・
   task-kills = jobs の層の中で止めた task の数・started = 受けた会話の数・most = 同時に走った会話の最大・limits = 見張りが clis の層に
   書いた上限の列(書いた順)・lowest-room = clis の上限 − 会話の要求の最小(負なら kernel が会話の CLI を止める刻があった)・lines =
   log の行(#(名 欄の dict) の列)。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ int highest)
  (#^ int cli-kills)
  (#^ int task-kills)
  (#^ int started)
  (#^ int most)
  (#^ tuple limits)
  (#^ int lowest-room)
  (#^ tuple lines))


(defeffect ReadScene
  "模擬の機体の記録を問う — テストだけの effect。"
  {:fields [] :answer SceneSeen :tags {:context "doeff-cluster-test" :role "intent"}})


(defk others-at [scene now]
  {:pre [(: scene Scene) (: now int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 now のほかの Pod の working set を求めるため(台本の跳ねと戻り)。"
  (val up (// (* scene.spike (max 0 (min scene.spike-span (- now scene.spike-from)))) scene.spike-span))
  (val down (// (* scene.spike (max 0 (min scene.spike-span (- now scene.spike-until)))) scene.spike-span))
  (+ scene.others (- up down)))


(defk cli-demand [scene starts now]
  {:pre [(: scene Scene) (: starts tuple) (: now int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "刻 now の会話の CLI の層の要求を求めるため: ターンの外の使い + 受けた会話がそれぞれ ramp-ms で NEED へ伸びた量。"
  (+ scene.idle (sum (gfor s starts (// (* NEED (max 0 (min scene.ramp-ms (- now s)))) scene.ramp-ms)))))


(defclass World []  ; class にする理由: 模擬の kernel と会話の受けが刻ごとに書き換える入れ物(答え手の節と判断の defk が同じ物を読み書きする)
  "模擬の node-a の刻ごとの様子(頭の註): 台本・clis と jobs の層の上限・受けた会話の始まりの刻・数え。"
  (defn #^ None __init__ [self #^ Scene scene #^ int clis-max]
    (setv self.scene scene self.clis-max clis-max self.jobs-max (* 10752 MIB) self.starts #() self.started 0 self.most 0
          self.next-admit 0 self.cli-kills 0 self.task-kills 0 self.task-alive True self.highest 0 self.lowest-room (* 1024 GIB)
          self.limits #() self.lines #())))


(defk world-at [world now]
  {:pre [(: world World) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の kernel の刻 now の様子(答え = #(Pod の和 jobs の使い clis の使い))を求めるため: 要求が層の上限を越えた層は、その中の 1 本を
   OOM kill して上限の内へ戻す(会話は古い順・task は 1 度止めたら戻らない)。"
  (val scene world.scene)
  (<- wanted int (cli-demand scene world.starts now))
  (setv world.lowest-room (min world.lowest-room (- world.clis-max wanted)))
  (var demand wanted)
  (while (and world.starts (> demand world.clis-max))
    (setv world.starts (cut world.starts 1 None) world.cli-kills (+ world.cli-kills 1))
    (<- left int (cli-demand scene world.starts now))
    (:= demand left))
  (when (and world.task-alive (> scene.jobs world.jobs-max))
    (setv world.task-alive False world.task-kills (+ world.task-kills 1)))
  (val clis (min world.clis-max demand))
  (val jobs (if world.task-alive scene.jobs 0))
  (<- others int (others-at scene now))
  (val pods (+ others scene.resident jobs clis))
  (setv world.highest (max world.highest pods))
  #(pods jobs clis))


(defk admitted [world now]
  {:pre [(: world World) (: now int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "turn-host の受けの形で刻 now に会話を受けるため: admit-every-ms ごとに、余白 = clis の上限 − 会話の要求・余白 ÷ NEED − 120 秒の内に
   受けた本数 ≥ 1 なら 1 本受ける。"
  (val scene world.scene)
  (when (and (>= now world.next-admit) (< now scene.admit-until-ms))
    (setv world.next-admit (+ now scene.admit-every-ms))
    (val recent (len (lfor s world.starts :if (< (- now s) RECENT-MS) s)))
    (<- demand int (cli-demand scene world.starts now))
    (when (>= (- (// (- world.clis-max demand) NEED) recent) 1)
      (setv world.starts (+ world.starts #(now)) world.started (+ world.started 1))))
  (setv world.most (max world.most (len world.starts)))
  None)


(defhandler node-a-world [#^ World world #^ MemoryCell cell #^ int run-until-ms]
  "見張りの外側の模擬の node-a(頭の註): Node の宣言を答える偽の API server・kubepods と root と container と jobs と clis の層の cgroup の
   file を答える模擬の kernel(world-at)・turn-host の形の会話の受け(admitted)・log の行を溜める・run-until-ms 以後の待ちで止めの合図。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 模擬の機体の入れ物と止めの合図の入れ物と止める刻は検ごとに決める値(Ask で読む設定ではない)。
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "29662296Ki"} "allocatable" {"memory" "20749400Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0)))
  (ReadText [path]
    :when (or (.startswith path PODS) (= path (+ HOST "/memory.stat")))
    (<- now int (now-epoch-ms))
    (<- seen tuple (world-at world now))
    (val pods (get seen 0))
    (val jobs (get seen 1))
    (val clis (get seen 2))
    (val container (+ world.scene.resident jobs clis))
    (resume (cond (= path (+ HOST "/memory.stat")) (.format STAT (+ pods world.scene.system))
                  (= path (+ PODS "/memory.max")) (str PODS-MAX)
                  (= path (+ PODS "/memory.current")) (str pods)
                  (= path (+ PODS "/memory.stat")) (.format STAT pods)
                  (= path (+ OWN "/memory.max")) (str CONTAINER-MAX)
                  (= path (+ OWN "/memory.current")) (str container)
                  (= path (+ OWN "/memory.stat")) (.format STAT container)
                  (= path (+ JOBS "/memory.current")) (str jobs)
                  (= path (+ JOBS "/memory.stat")) (.format STAT jobs)
                  (= path (+ CLIS "/memory.current")) (str clis)
                  (= path (+ CLIS "/memory.stat")) (.format STAT clis)
                  (in path #((+ COMMANDS "/memory.current") (+ EXEC "/memory.current"))) "0\n"
                  (in path #((+ COMMANDS "/memory.stat") (+ EXEC "/memory.stat"))) (+ (.format STAT 0) "shmem 0\nslab_reclaimable 0\n")
                  True (raise (ValueError (.format "模擬の node-a に無い file {}" path))))))
  (WriteText [path text]
    :when (in path #((+ JOBS "/memory.max") (+ CLIS "/memory.max")))
    (if (= path (+ JOBS "/memory.max"))
        (setv world.jobs-max (int text))
        (setv world.clis-max (int text) world.limits (+ world.limits #((int text)))))
    (resume None))
  (ObserveJobRoots []
    (resume #((JobRoot :name "svc" :pid 10) (JobRoot :name "task/1" :pid 20))))
  (SlogEffect []
    (setv world.lines (+ world.lines #(#(effect.msg (dict effect.kwargs)))))
    (resume None))
  (DelayEffect []
    ;; 待ちの刻にも数える(kubelet と kernel は worker の読みと別の刻に読む)。会話の受けも待ちの刻に判じる。
    (<- now int (now-epoch-ms))
    (<- (admitted world now))
    (<- (world-at world now))
    (when (>= now run-until-ms)
      (setv cell.closing True))
    (reperform effect))
  (ReadScene []
    (resume (SceneSeen :highest world.highest :cli-kills world.cli-kills :task-kills world.task-kills :started world.started
                       :most world.most :limits world.limits :lowest-room world.lowest-room :lines world.lines))))


(defk machine-files []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の file のうち変わらない物: /proc/meminfo・PSI・worker の container の memory・ServiceAccount の token。"
  (val meminfo (.format "MemTotal:       {} kB\nMemFree:        1024 kB\nMemAvailable:   {} kB\n" (// CAPACITY 1024)
                        (// (int (* 9.68 GIB)) 1024)))
  (val pressure "some avg10=0.00 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=2\n")
  (MemoryFiles :dirs #("/proc" "/proc/pressure" "/sys" "/sys/fs" "/sys/fs/cgroup" "/sa")
               :files #((MemoryFile :path "/proc/meminfo" :content (.encode meminfo))
                        (MemoryFile :path "/proc/pressure/memory" :content (.encode pressure))
                        (MemoryFile :path "/sys/fs/cgroup/memory.current" :content (.encode (str (* 5 GIB))))
                        (MemoryFile :path TOKEN :content b"token\n"))))


(defk scene-rows []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の process の表(見張りの止めの単位はどれも min-victim-bytes 未満で、見張りは止めない — 止めるのは模擬の kernel だけ)。"
  #((ProcRow :pid 1 :ppid 0 :start-ticks 1 :rss-bytes MIB :comm "tini")
    (ProcRow :pid 10 :ppid 1 :start-ticks 10 :rss-bytes (* 10 MIB) :comm "python")
    (ProcRow :pid 20 :ppid 1 :start-ticks 20 :rss-bytes (* 10 MIB) :comm "python")))


(defk watch-then-scene [settings cell]
  {:pre [(: settings MemoryWatchSettings) (: cell MemoryCell)] :post [(: % SceneSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを止めの合図まで回し、模擬の機体の記録を問うため。"
  (<- (memory-watch-loop settings cell))
  (<- seen SceneSeen (ReadScene))
  seen)


(defk scene-watched [scene clis run-until-ms]
  {:pre [(: scene Scene) (: clis bool) (: run-until-ms int)] :post [(: % SceneSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の見張りを模擬の node-a と仮想時計の下で止めの合図まで回すため。clis = 会話の CLI の層を掛け直すか(偽 = 前の形 — 床のまま)。"
  (<- eviction EvictionSettings (node-a-settings clis))
  (val cell (MemoryCell))
  (<- rows tuple (scene-rows))
  (<- files MemoryFiles (machine-files))
  (<- seen SceneSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files)
                                     (scripted-proc-table-handler rows) (node-a-world (World scene FLOOR) cell run-until-ms)]
                       (watch-then-scene (MemoryWatchSettings :eviction eviction) cell)))
  seen)


(defk lines-named [lines name]
  {:pre [(: lines tuple) (: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行から名 name の行の欄を出した順に引くため。"
  (tuple (gfor #(msg fields) lines :if (= msg name) fields)))


;; 会話の受けの無い間に clis へ貸した空きが、走っている task より先に返る形(11-09 23:00 JST の node-a の形を縮めた): ほかの Pod 10 GiB・
;; worker の本体と常駐の job 1.5 GiB・jobs の層に長い task 3.5 GiB・使われていない CLI と daemon 1 GiB。会話は受けない(clis の上限は
;; 空きの半分まで広がるが使われない)。180 秒からほかの Pod が 30 秒で 2 GiB 跳ね、300 秒から戻る。
(val IDLE-LENT (Scene :others (* 10 GIB) :spike (* 2 GIB) :spike-from 180000 :spike-span 30000 :spike-until 300000
                      :resident (int (* 1.5 GIB)) :jobs (int (* 3.5 GIB)) :idle GIB :admit-until-ms 0))


(deftest test-an-outside-spike-takes-the-unused-cli-lending-before-the-running-task
  (<- seen SceneSeen (scene-watched IDLE-LENT True 400000))
  ;; 跳ねの前は clis に床を越えて貸していた。
  (assert (> (max seen.limits) FLOOR) (/ (max seen.limits) GIB))
  ;; 跳ねの間は使われていない貸しを返して縮み、jobs の層の上限は task の使いより下がらない — task も会話の CLI も止まらない。
  (assert (= #(seen.task-kills seen.cli-kills) #(0 0)) seen)
  (assert (< (min (cut seen.limits 1 None)) (max seen.limits)) seen.limits)
  (<- squeezes tuple (lines-named seen.lines JOBS-SQUEEZE-LOG))
  (assert (= squeezes #()) squeezes)
  (assert (<= seen.highest ALLOCATABLE) #((/ seen.highest GIB) (/ ALLOCATABLE GIB))))


;; 平常の node-a(11-06 19:00〜21:30 JST の機体の使い 17.5〜20.6 GiB / 28.3 GiB の形): ほかの Pod 10 GiB・worker の本体と常駐の job 1.5 GiB・
;; task 1 GiB・使われていない CLI と daemon 0.5 GiB — Pod の和 13 GiB・追い出しまでの空き 約 6.8 GiB。180 秒からほかの Pod が 30 秒で
;; 4 GiB 跳ね(node-c の 11-06 21:00 の probe の形を node-a の大きさに縮めた)、300 秒から戻る。受けるのは 240 秒まで(跳ねの後に縮むのを見る)。
(val SPIKE (Scene :others (* 10 GIB) :spike (* 4 GIB) :spike-from 180000 :spike-span 30000 :spike-until 300000
                  :resident (int (* 1.5 GIB)) :jobs GIB :idle (// GIB 2) :admit-until-ms 240000))


(deftest test-the-cli-layer-takes-more-cli-turns-and-kills-none-through-an-outside-spike
  (<- seen SceneSeen (scene-watched SPIKE True 400000))
  ;; 会話の CLI は 1 本も殺されない(上限はどの刻も会話の要求以上)。Pod の和は allocatable を越えない(kubelet は追い出さない)。
  (assert (= seen.cli-kills 0) seen)
  (assert (>= seen.lowest-room 0) (/ seen.lowest-room GIB))
  (assert (<= seen.highest ALLOCATABLE) #((/ seen.highest GIB) (/ ALLOCATABLE GIB)))
  ;; 床だけの前の形(1 本)より多くの会話を同時に受けた。
  (assert (>= seen.most 3) seen.most)
  ;; 上限は床と天井の間で、跳ねの間に縮み、跳ねが戻った後に広がり直す。
  (assert (all (gfor l seen.limits (<= FLOOR l CEILING))) seen.limits)
  (val peak (max seen.limits))
  (val last (get seen.limits -1))
  (assert (> peak FLOOR) (/ peak GIB))
  (<- told tuple (lines-named seen.lines CLIS-LENT-LOG))
  (assert (>= (len told) 1) told)
  (assert (> (get (get told 0) "clis_limit_mib") (// FLOOR MIB)) told)
  (assert (>= last (min seen.limits)) (/ last GIB))
  ;; この跳ねは、clis の使われていない貸しを返しても線の空きに収まらない(Pod の和が allocatable を越える)ので、jobs の層が縮んだ(task が
  ;; jobs の層の中で止まった)— 会話の CLI の使いは守った。収まる跳ねで task を止めない形は上の IDLE-LENT の検。
  (assert (= seen.task-kills 1) seen.task-kills)
  ;; 前の形(天井の宣言が無い — clis は床 1536M のまま)の同じ機体は、会話を 1 本しか受けない。
  (<- before SceneSeen (scene-watched SPIKE False 400000))
  (assert (= #(before.most before.cli-kills) #(1 0)) before)
  (assert (= before.limits #()) before.limits))


;; --- jobs の層の天井(worker/core/eviction_rules の頭の註の jobs の層の天井)----------------------------------------------------------
;; 実例(node-c の 11-10): 機体の空き 約 31 GiB・container の中の空き 約 8 GiB の間も、jobs の層の上限は宣言の静的な 10 GiB で止まり、
;; 要り 3584〜5632 MiB の task が 1 本ずつしか置かれなかった。模擬は node-a の大きさで同じ形を作る: Pod の和 3 GiB(線の空き 約 16.8 GiB)・
;; container 3 GiB(container の中の空き 16 GiB)で、線と container だけなら jobs の層は静的な 10752M を越えて伸びられる。

(val JOBS-CAP (* 10752 MIB))
(val JOBS-CEILING (* 16 GIB))


(defk roomy-settings [ceiling]
  {:pre [(: ceiling (| int None))] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の線の設定に jobs の層の天井 ceiling(None = 宣言しない — 前の形)を足すため。"
  (<- layers tuple (node-a-layers ""))
  (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-a" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                    :jobs-cap-bytes JOBS-CAP :jobs-ceiling-bytes ceiling
                    :clis (CliLayerSettings :dir CLIS :floor-bytes FLOOR :ceiling-bytes CEILING :container-dir OWN
                                            :turn-room-bytes NODE-A-TURN-ROOM :layers layers)))


(deftest test-a-declared-jobs-ceiling-lets-the-jobs-layer-grow-past-its-static-value-up-to-the-container-room
  (val pods (* 3 GIB))
  (val room (- ALLOCATABLE pods))
  (<- start MemoryFiles (node-a-files pods (// GIB 2) GIB (* 3 GIB)))
  (<- ceiling-settings EvictionSettings (roomy-settings JOBS-CEILING))
  (<- plain-settings EvictionSettings (roomy-settings None))
  (val ceiled (EvictionCell))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                          (ticked-files ceiling-settings ceiled 2)))
  ;; clis は使い 1 GiB + 余白 2 GiB まで広がり、床を越えた 1.5 GiB を貸す。jobs の層の上限は、線の空き(使い 0.5 + 空き − 余裕 1 − 貸し
  ;; 1.5)と container の中の空き(使い 0.5 + (22 − 3 − 余裕 1 − held 2) − clis の残り 2)の小さい方 = 14.5 GiB で、静的な 10752M を越える。
  (<- clis-max str (content-at files (+ CLIS "/memory.max")))
  (assert (= (int clis-max) (+ GIB NODE-A-TURN-ROOM)) (// (int clis-max) MIB))
  (val line-limit (- (+ (// GIB 2) room) GIB (- (int clis-max) FLOOR)))
  (val box-limit (+ (// GIB 2) (- (* 16 GIB) NODE-A-TURN-ROOM)))
  (assert (> line-limit box-limit) #((// line-limit MIB) (// box-limit MIB)))
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (= (int jobs-max) box-limit) (// (int jobs-max) MIB))
  (assert (> (int jobs-max) JOBS-CAP) (// (int jobs-max) MIB))
  ;; 置き先に名乗る受けも同じ空きまで(task の層の空きが静的な値で止まらない)。
  (assert (= ceiled.jobs-intake box-limit) (// ceiled.jobs-intake MIB))
  ;; 2 つの層が上限まで伸びても、container の working set は上限 − 余裕 − held の内。
  (assert (<= (+ (- (* 3 GIB) (// GIB 2) GIB) (int jobs-max) (int clis-max)) (- CONTAINER-MAX GIB (* 2 GIB))))
  ;; 失敗ケース(前の形 — 天井の宣言が無い): 同じ機体で jobs の層の上限は静的な 10752M のまま。
  (val plain (EvictionCell))
  (<- before MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                           (ticked-files plain-settings plain 2)))
  (<- old-jobs str (content-at before (+ JOBS "/memory.max")))
  (assert (= #((int old-jobs) plain.jobs-intake) #(JOBS-CAP JOBS-CAP)) #((// (int old-jobs) MIB) (// plain.jobs-intake MIB))))


(deftest test-the-jobs-ceiling-is-not-used-while-the-line-or-the-container-cannot-be-read
  ;; 線を読めない回は、天井を宣言していても jobs の層の上限は静的な値(container の中の空きを数えずに広げない)。
  (<- start MemoryFiles (node-a-files (* 3 GIB) (// GIB 2) GIB (* 3 GIB)))
  (<- ceiling-settings EvictionSettings (roomy-settings JOBS-CEILING))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (unread-api) slog-discard-handler]
                          (ticked-files ceiling-settings (EvictionCell) 1)))
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (= (int jobs-max) JOBS-CAP) (// (int jobs-max) MIB))
  ;; container の上限を読めない回(container の memory.max の file が無い)も、線の空きが大きくても静的な値で切る。
  (val boxless (MemoryFiles :dirs start.dirs :files (tuple (gfor f start.files :if (!= f.path (+ OWN "/memory.max")) f))))
  (<- open-files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler boxless) (node-a-api) slog-discard-handler]
                               (ticked-files ceiling-settings (EvictionCell) 1)))
  (<- open-jobs str (content-at open-files (+ JOBS "/memory.max")))
  (assert (<= (int open-jobs) JOBS-CAP) (// (int open-jobs) MIB)))


(deftest test-the-jobs-ceiling-is-declared-per-machine-only-beside-the-cli-ceiling
  (<- clis (| CliLayerSettings None) (cli-layer-settings-of {"WORKER_CLI_LAYER_CEILING" "6G"} OWN NODE-A-LAYOUT "jobs"))
  (<- ceiling (| int None) (jobs-ceiling-bytes {"WORKER_JOBS_LAYER_CEILING" "16G"} JOBS-CAP clis))
  (<- absent (| int None) (jobs-ceiling-bytes {} JOBS-CAP clis))
  (assert (= #(ceiling absent) #(JOBS-CEILING None)) #(ceiling absent))
  ;; 失敗ケース: 綴りが読めない・max・静的な値より小さい・jobs の層の memory.max が無い・会話の CLI の層を掛け直さない(container の中の
  ;; 空きを数えない)宣言は起動を断る。
  (for [#(declared cap with-clis) #(#("lots" JOBS-CAP True) #("max" JOBS-CAP True) #("8G" JOBS-CAP True) #("16G" None True)
                                    #("16G" JOBS-CAP False))]
    (var refused False)
    (try
      (<- (jobs-ceiling-bytes {"WORKER_JOBS_LAYER_CEILING" declared} cap (if with-clis clis None)))
      (except [ValueError]
        (:= refused True)))
    (assert refused #(declared cap with-clis))))


;; --- 使われていない貸しの返しと container の中の限る層(card の実例 node-c の 11-11 16:4x JST)------------------------------------------
;; node-c の jobs の層の受けは、clis の上限 16 GiB のうち使われていない 6771 MiB と、commands・gate・exec の取り置き 5120 MiB(使い約 92 MiB)を
;; 引いた値で名乗られ、受けに入らない task は空きを縮めないので貸しも取り置きも戻らなかった。模擬は node-a の大きさで同じ形を作る。

(defk with-content [files path text]
  {:pre [(: files MemoryFiles) (: path str) (: text str)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の file の path の中身を text に替えた file の組を作るため(読みの形を検ごとに替える)。"
  (MemoryFiles :dirs files.dirs :files (tuple (gfor f files.files (if (= f.path path) (MemoryFile :path path :content (.encode text)) f)))))


(defk busy-then-idle [settings cell]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "会話の CLI が 5 GiB を使う回の後に、CLI が止まって 1 GiB へ減った回(clis・container・Pod の和が 4 GiB ずつ減る)を回し、2 つの回の
   clis の上限と jobs の層の受けを読むため(答え = #(前の上限 前の受け 後の上限 後の受け))。"
  (<- (eviction-noted settings cell True 5000))
  (val busy #(cell.clis-limit cell.jobs-intake))
  (<- (WriteText (+ CLIS "/memory.current") (str GIB)))
  (<- (WriteText (+ OWN "/memory.current") (str (* 3 GIB))))
  (<- (WriteText (+ PODS "/memory.current") (str (* 3 GIB))))
  (<- (eviction-noted settings cell True 10000))
  #(#* busy cell.clis-limit cell.jobs-intake))


(deftest test-unused-cli-lending-goes-back-to-the-jobs-intake-while-a-placed-task-waits-for-room
  ;; 線の空きの大きい node-a(Pod の和 7 GiB — CLI が止まった後は 3 GiB): 会話の CLI が 5 GiB を使い、clis の上限は天井 6 GiB。置き先が要り 4 GiB の task を置き、
  ;; まだ 0.5 GiB しか使っていない(jobs の層の需要 — 取り置き 3.5 GiB)。
  (val cell (EvictionCell))
  (<- needs tuple (task-needs-of #({"id" "t1" "memoryMiB" 4096})))
  (setv cell.needs needs cell.uses #((JobUse :job "task/t1" :pid 20 :bytes (// GIB 2) :processes 1)))
  (<- start MemoryFiles (node-a-files (* 7 GIB) (// GIB 2) (* 5 GIB) (* 7 GIB)))
  (<- settings EvictionSettings (roomy-settings JOBS-CEILING))
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                   (busy-then-idle settings cell)))
  (val busy-limit (get seen 0))
  (val busy-intake (get seen 1))
  (val idle-limit (get seen 2))
  (val idle-intake (get seen 3))
  (assert (= busy-limit CEILING) (// busy-limit MIB))
  ;; CLI が止まった次の回の上限は、使い 1 GiB + 余白 2 GiB(前の回の 6 GiB を持ち越さない)。
  (assert (= idle-limit (+ GIB NODE-A-TURN-ROOM)) (// idle-limit MIB))
  ;; 返った貸しは同じ回の jobs の層の受け(置き先に名乗る上限)に入る: container の中の空き(22 − 3 − 余裕 1 − held 2 = 16 GiB)から余白
  ;; だけを引いた 0.5 + 16 − 2 GiB。前の形(前の回の上限 6 GiB を持ち越す)なら 0.5 + 16 − 5 GiB で、要り 4 GiB の task の残り 3.5 GiB の外に
  ;; 新しい受けは 8 GiB しか残らなかった。
  (assert (= idle-intake (+ (// GIB 2) (- (* 16 GIB) NODE-A-TURN-ROOM))) (// idle-intake MIB))
  (assert (= (- idle-intake (+ (// GIB 2) (- (* 16 GIB) (- CEILING GIB)))) (- CEILING (+ GIB NODE-A-TURN-ROOM)))
          #((// idle-intake MIB) (// busy-intake MIB)))
  ;; 置いた task の取り置きは、jobs の層の上限が覆う(clis へ貸さない)。
  (assert (>= (- cell.jobs-limit (// GIB 2)) cell.pending) #((// cell.jobs-limit MIB) (// cell.pending MIB))))


(val COMMAND-USE (* 300 MIB))
(val EXEC-USE (* 100 MIB))
(val COMMAND-ROOM (* 256 MIB))


(defk layered-files [start]
  {:pre [(: start MemoryFiles)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の file に、命令の層の使い COMMAND-USE と exec の層の使い EXEC-USE(どちらも anon — container の working set に数え済み)を
   入れるため。"
  (<- commands MemoryFiles (with-content start (+ COMMANDS "/memory.current") (str COMMAND-USE)))
  (<- commands-stat MemoryFiles (with-content commands (+ COMMANDS "/memory.stat")
                                              (+ (.format STAT COMMAND-USE) "shmem 0\nslab_reclaimable 0\n")))
  (<- exec MemoryFiles (with-content commands-stat (+ EXEC "/memory.current") (str EXEC-USE)))
  (<- exec-stat MemoryFiles (with-content exec (+ EXEC "/memory.stat") (+ (.format STAT EXEC-USE) "shmem 0\nslab_reclaimable 0\n")))
  exec-stat)


(defk layered-settings [floors]
  {:pre [(: floors str)] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の線の設定を、機体の宣言の層の余地 floors(WORKER_LAYER_FLOORS の綴り)で作るため。"
  (<- layers tuple (node-a-layers floors))
  (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-a" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                    :jobs-cap-bytes JOBS-CAP :jobs-ceiling-bytes JOBS-CEILING
                    :clis (CliLayerSettings :dir CLIS :floor-bytes FLOOR :ceiling-bytes CEILING :container-dir OWN
                                            :turn-room-bytes NODE-A-TURN-ROOM :layers layers)))


(deftest test-the-limited-layers-are-relimited-by-their-use-and-held-once-out-of-the-container-room
  ;; container 3 GiB の working set に、命令の層 300 MiB と exec の層 100 MiB の使いが入っている。命令の層だけ余地 256 MiB を宣言した。
  (val container (* 3 GIB))
  (<- start MemoryFiles (node-a-files (* 3 GIB) (// GIB 2) GIB container))
  (<- files MemoryFiles (layered-files start))
  (<- settings EvictionSettings (layered-settings "commands=256M"))
  (val cell (EvictionCell))
  (<- after MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (node-a-api) slog-discard-handler]
                          (ticked-files settings cell 1)))
  ;; 命令の層の上限は回収で返らない量 + 余地。余地の宣言の無い exec は静的な値のまま。
  (<- commands-max str (content-at after (+ COMMANDS "/memory.max")))
  (<- exec-max str (content-at after (+ EXEC "/memory.max")))
  (assert (= #((int commands-max) (int exec-max)) #((+ COMMAND-USE COMMAND-ROOM) (* 512 MIB))) #(commands-max exec-max))
  (assert (= cell.box-limits #((BoxLayerLimit :name "commands" :limit-bytes (+ COMMAND-USE COMMAND-ROOM)))) cell.box-limits)
  ;; container の中の空きは、層ごとに「今掛けている上限 − 層の working set」だけを引く — 使いは container の working set に 1 度だけ数える。
  (val held (+ COMMAND-ROOM (- (* 512 MIB) EXEC-USE)))
  (assert (= cell.box-held held) (// cell.box-held MIB))
  (assert (= cell.box (- CONTAINER-MAX container GIB held)) (// cell.box MIB))
  ;; 失敗ケース(前の形): 静的な値の和 2 GiB を取り置き、使い 400 MiB を working set と 2 度引いた。今の形の空きはその差だけ大きく、jobs の
  ;; 層の受けに入る。
  (assert (= (- cell.box (- CONTAINER-MAX container GIB (* 2 GIB))) (- (* 2 GIB) held)) (// cell.box MIB)))


(deftest test-the-limited-layers-go-back-to-their-static-limits-while-the-room-cannot-be-counted
  ;; 線を読めない回は、余地の宣言の在る層も静的な値に戻す(jobs の層が静的な値に戻るので、限る層の静的な値の和で container に収める)。
  (<- start MemoryFiles (node-a-files (* 3 GIB) (// GIB 2) GIB (* 3 GIB)))
  (<- files MemoryFiles (layered-files start))
  (<- settings EvictionSettings (layered-settings "commands=256M"))
  (val lowered (EvictionCell))
  (<- unread MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (unread-api) slog-discard-handler]
                           (ticked-files settings lowered 1)))
  (<- commands-max str (content-at unread (+ COMMANDS "/memory.max")))
  (assert (= (int commands-max) FLOOR) (// (int commands-max) MIB))
  ;; 層を読めない回(命令の層の memory.stat が無い)は、その層の上限の全部を取り置き、訳を言う(JOBS-LIMIT-FAILED-LOG の訳)。
  (val statless (MemoryFiles :dirs files.dirs :files (tuple (gfor f files.files :if (!= f.path (+ COMMANDS "/memory.stat")) f))))
  (val cell (EvictionCell))
  (<- (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler statless) (node-a-api) slog-discard-handler]
        (ticked-files settings cell 1)))
  (assert (= cell.box-held (+ FLOOR (- (* 512 MIB) EXEC-USE))) (// cell.box-held MIB))
  (assert (in "commands" cell.told-failure) cell.told-failure))


(deftest test-the-demand-room-and-the-layer-floors-are-declared-per-machine
  (val env {"WORKER_CLI_LAYER_CEILING" "6G" "WORKER_CLI_TURN_BYTES" "1536M" "WORKER_CLI_TURN_SLOTS" "3"
            "WORKER_LAYER_FLOORS" "commands=256M exec=128M"})
  (<- clis (| CliLayerSettings None) (cli-layer-settings-of env OWN NODE-A-LAYOUT "jobs"))
  (assert (= clis.turn-room-bytes (* 3 1536 MIB)) clis.turn-room-bytes)
  (assert (= clis.layers #((BoxLayerSettings :name "commands" :dir COMMANDS :cap-bytes FLOOR :floor-bytes COMMAND-ROOM)
                           (BoxLayerSettings :name "exec" :dir EXEC :cap-bytes (* 512 MIB) :floor-bytes (* 128 MIB))))
          clis.layers)
  (<- default-room int (cli-turn-room-bytes {}))
  (assert (= default-room TURN-ROOM) default-room)
  ;; 失敗ケース: 綴りの読めない余白・本数、jobs・clis・層の宣言に無い層・静的な値より大きい余地・2 度の宣言、天井の無い機体の宣言は起動を断る。
  (for [extra #({"WORKER_CLI_TURN_BYTES" "max"} {"WORKER_CLI_TURN_SLOTS" "two"} {"WORKER_LAYER_FLOORS" "jobs=1G"}
                {"WORKER_LAYER_FLOORS" "clis=1G"} {"WORKER_LAYER_FLOORS" "gate=1G"} {"WORKER_LAYER_FLOORS" "exec=1G"}
                {"WORKER_LAYER_FLOORS" "exec=1M exec=2M"} {"WORKER_LAYER_FLOORS" "exec"})]
    (var refused False)
    (try
      (<- (cli-layer-settings-of (| {"WORKER_CLI_LAYER_CEILING" "6G"} extra) OWN NODE-A-LAYOUT "jobs"))
      (except [ValueError]
        (:= refused True)))
    (assert refused extra))
  (for [stray #({"WORKER_LAYER_FLOORS" "commands=256M"} {"WORKER_CLI_TURN_SLOTS" "2"})]
    (var stray-refused False)
    (try
      (<- (cli-layer-settings-of stray OWN NODE-A-LAYOUT "jobs"))
      (except [ValueError]
        (:= stray-refused True)))
    (assert stray-refused stray)))


;; --- 上に載る系が clis に残させる余白(eviction_rules の頭の註の帳面の 2')------------------------------------------------------------

;; 上に載る系が書く余白の file(模擬の path)と、その値(3 GiB — 上に載る系の急ぎの仕事 3 本ぶん)。
(val KEPT-FILE "/run/upper/clis-kept-bytes")
(val KEPT (* 3 GIB))


(deftest test-the-kept-room-stays-above-the-cli-use-before-placed-tasks-and-the-demand-room
  ;; 空きが余る回: 使い 1 GiB の上に、残させる余白 3 GiB と、残りの空き(10 − 3 GiB)の半分の内の需要の余白(4 GiB の内)を残す。
  (val wide (* 16 GIB))
  (<- roomy int (clis-limit GIB (* 10 GIB) None FLOOR wide TURN-ROOM 0 KEPT))
  (assert (= roomy (+ GIB KEPT (min TURN-ROOM (int (* 7 GIB CLIS-ROOM-SHARE))))) (// roomy MIB))
  ;; ターンの外が空きを埋めた回(空き 3 GiB・置いた task の取り置き 2 GiB): 残させる余白は取り置きより先に残る(需要の余白は 0)。残させない
  ;; 前の形は取り置きの外の 1 GiB の半分だけで、床に戻る。
  (<- tight int (clis-limit GIB (* 3 GIB) None FLOOR wide TURN-ROOM (* 2 GIB) KEPT))
  (<- unkept int (clis-limit GIB (* 3 GIB) None FLOOR wide TURN-ROOM (* 2 GIB)))
  (assert (= #(tight unkept) #((+ GIB KEPT) FLOOR)) #((// tight MIB) (// unkept MIB)))
  ;; 空きが残させる余白より小さい回は残る空きまで(線を越えて貸さない)。container の中の空きでも同じく切る。線を越えた回は使いのまま。
  (<- short int (clis-limit GIB GIB None FLOOR wide TURN-ROOM 0 KEPT))
  (<- boxed int (clis-limit GIB (* 10 GIB) GIB FLOOR wide TURN-ROOM 0 KEPT))
  (<- over int (clis-limit (* 2 GIB) (- GIB) None FLOOR wide TURN-ROOM 0 KEPT))
  (assert (= #(short boxed over) #((* 2 GIB) (* 2 GIB) (* 2 GIB))) #((// short MIB) (// boxed MIB) (// over MIB)))
  ;; 天井は越えない。
  (<- capped int (clis-limit GIB (* 10 GIB) None FLOOR CEILING TURN-ROOM 0 KEPT))
  (assert (= capped CEILING) (// capped MIB))
  ;; 床を越えて貸した量(jobs の層の上限から引く)に残させる余白が入る — jobs の層の受けから先に取る。
  (<- lent int (clis-lent tight GIB FLOOR))
  (assert (= lent (- (+ GIB KEPT) FLOOR)) (// lent MIB)))


(deftest test-the-kept-file-is-declared-per-machine-and-passed-to-the-job-children
  (val env {"WORKER_CLI_LAYER_CEILING" "6G" "WORKER_CLI_KEPT_FILE" KEPT-FILE})
  (<- clis (| CliLayerSettings None) (cli-layer-settings-of env OWN NODE-A-LAYOUT "jobs"))
  (assert (= clis.kept-file KEPT-FILE) clis.kept-file)
  (<- environ dict (cli-ceiling-environ env))
  (assert (= environ {CLI-CEILING-CHILD-VAR (str CEILING) CLI-KEPT-FILE-CHILD-VAR KEPT-FILE}) environ)
  ;; 宣言の無い機体は読まず、子へも渡さない。
  (<- plain (| CliLayerSettings None) (cli-layer-settings-of {"WORKER_CLI_LAYER_CEILING" "6G"} OWN NODE-A-LAYOUT "jobs"))
  (assert (= plain.kept-file "") plain.kept-file)
  ;; 天井の無い機体の宣言・絶対でない path は起動を断る。
  (for [declared #({"WORKER_CLI_KEPT_FILE" KEPT-FILE} {"WORKER_CLI_LAYER_CEILING" "6G" "WORKER_CLI_KEPT_FILE" "run/kept"})]
    (var refused False)
    (try
      (<- (cli-layer-settings-of declared OWN NODE-A-LAYOUT "jobs"))
      (except [ValueError]
        (:= refused True)))
    (assert refused declared)))


(deftest test-the-worker-reads-the-kept-file-every-tick-and-counts-a-missing-file-as-zero
  ;; 平常の node-a(test-the-worker-writes-the-cli-layer-limit-and-lends-it-out-of-the-jobs-layer と同じ使い)に、上に載る系が 3 GiB を書いた file。
  (val pods (* 12 GIB))
  (val free (- (- ALLOCATABLE pods) GIB))
  (<- base MemoryFiles (node-a-files pods (// GIB 2) GIB (* 3 GIB)))
  (val start (MemoryFiles :dirs (+ base.dirs #("/run" "/run/upper"))
                          :files (+ base.files #((MemoryFile :path KEPT-FILE :content (.encode (.format "{}\n" KEPT)))))))
  (<- plain EvictionSettings (node-a-settings True))
  (val settings (replace plain :clis (replace plain.clis :kept-file KEPT-FILE)))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                          (ticked-files settings (EvictionCell) 2)))
  (<- written str (content-at files (+ CLIS "/memory.max")))
  (val kept-max (+ GIB KEPT (min NODE-A-TURN-ROOM (int (* (- free KEPT) CLIS-ROOM-SHARE)))))
  (assert (= (int written) kept-max) #((// (int written) MIB) (// kept-max MIB)))
  ;; jobs の層の上限は残させた余白の分も貸しとして引く(jobs の受けから先に取る)。
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (= (int jobs-max) (- (+ (// GIB 2) free) (- kept-max FLOOR))) (// (int jobs-max) MIB))
  ;; file がまだ無い(上に載る系が書く前)回は 0 — 残させない前の形と同じ上限。
  (<- bare MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler base) (node-a-api) slog-discard-handler]
                         (ticked-files settings (EvictionCell) 2)))
  (<- unkept str (content-at bare (+ CLIS "/memory.max")))
  (assert (= (int unkept) (+ GIB (min NODE-A-TURN-ROOM (int (* free CLIS-ROOM-SHARE))))) (// (int unkept) MIB)))
