;; 会話の CLI の層(clis)の memory の上限を、追い出しまでの空きと container の中の空きから掛け直す — 模擬の検。
;;
;; 実例(card [id] の調べ・11-06 19:00〜21:30 JST): helios と atlas が会話を受ける本数は機体の空きでなく、clis の静的な
;; memory.max(helios 5G・atlas 1536M)で止まっていた。atlas は機体の空き 8〜10 GiB のうち会話 1 本ぶんしか使えず、zeus が混んだ間に
;; 始まった会話 48 本のうち helios と atlas が受けたのは 12 本だった。
;;   - 判断(worker/core/eviction_rules の clis-limit・clis-lent・box-free・jobs-box-limit): 広げる量は 2 つの空きの小さい方の半分・前の
;;     回に広げた上限は jobs の層を 0 まで縮めれば収まる間は残す・床(宣言の静的な値)と clis の今の使いより下げない・天井で止める。
;;   - worker の読み(worker/protocol/eviction_line の eviction-noted): clis の層の memory.max を書き、床を越えて貸した量を jobs の上限から
;;     引き、線を読めない回は床へ戻す。
;;   - 模擬の機体(atlas の値): 本物の見張りの背景の task を仮想時計の下で 5 秒ごとに回し、kubepods・root・container・jobs・clis の層の
;;     cgroup の file は模擬の kernel が答える。会話は turn-host の受けの形(余白 = clis の上限 − clis の使い・余白 ÷ 1 GiB − 120 秒の内に
;;     受けた本数 ≥ 1 なら 1 本受ける)で始まり、60 秒で 1 GiB へ伸びる。模擬の kernel は層の使いが見張りの書いた memory.max を越えた
;;     会話の CLI と task を層の中で OOM kill する。ほかの Pod が 30 秒で 4 GiB 跳ねても(11-06 21:00 の helios の probe の形を atlas の
;;     大きさに縮めた)、会話の CLI は 1 本も殺されず、Pod の和は allocatable を越えず、上限は跳ねの後に床の側へ縮む。床だけの worker
;;     (天井の宣言が無い — 前の形)は同じ機体で会話を 1 本しか受けない(対照)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
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
(import doeff_cluster._worker.core.memory_rules [JobRoot MIB])
(import doeff_cluster._worker.core.eviction_rules [CLIS-ROOM-SHARE clis-limit clis-lent box-free jobs-box-limit layer-memory-max
                                                  layout-held-bytes])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell CliLayerSettings eviction-noted CLIS-LENT-LOG])
(import doeff_cluster._worker.protocol.memory_watch [MemoryWatchSettings MemoryCell memory-watch-loop])
(import doeff_cluster._worker.entry.main [cli-layer-settings-of cli-ceiling-environ CLI-CEILING-CHILD-VAR])

(val GIB (* 1024 MIB))
;; atlas の Node の宣言(test_eviction_line.hy と同じ 11-01 の値)と、cgroup の層の宣言(nodes/atlas/cgroup.yaml)・container の上限 22Gi。
(val CAPACITY (* 29662296 1024))
(val ALLOCATABLE (* 20749400 1024))
(val PODS-MAX (- CAPACITY (* 7 GIB)))
(val ATLAS-LAYOUT (+ "worker:memory.min=256M,cpu.weight=1000 services:memory.min=256M,cpu.weight=1000 jobs:memory.max=10752M,cpu.weight=100 "
                     "clis:memory.max=1536M,cpu.weight=100 commands:memory.max=1536M,cpu.weight=100 exec:memory.max=512M,cpu.weight=1000"))
(val HELIOS-LAYOUT (+ "worker:memory.min=1G,cpu.weight=1000 services:memory.min=24G,cpu.weight=1000 jobs:memory.max=8G,cpu.weight=100 "
                      "clis:memory.max=10G,cpu.weight=100 commands:memory.max=1G,cpu.weight=100 exec:memory.max=512M,cpu.weight=1000"))
(val CONTAINER-MAX (* 22 GIB))
(val FLOOR (* 1536 MIB))
(val CEILING (* 6 GIB))
;; 模擬の機体の path(worker の container の cgroup は systemd の driver の形)。
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN "/jobs"))
(val CLIS (+ OWN "/clis"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/atlas")
;; 会話 1 本に受け手(turn-host)が見込む量(上に載る系の機体の宣言 TURN_HOST_TURN_RAM_MIB)と、受けた本数を数える窓
;; (cli_host_room_rules の 120 秒)。
(val NEED GIB)
(val RECENT-MS 120000)


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-the-cli-layer-grows-by-half-the-room-between-its-floor-and-its-ceiling
  ;; 空き 6 GiB・container の中の空きは大きい: clis の今の使い 1 GiB の上に 3 GiB を広げる(残りの 3 GiB は jobs の層へ)。
  (<- grown int (clis-limit GIB (* 6 GIB) (* 12 GIB) FLOOR CEILING None 0))
  (assert (= grown (+ GIB (int (* 6 GIB CLIS-ROOM-SHARE)))) (/ grown GIB))
  ;; 広げても天井で止まる。空きが無ければ床(今までの静的な上限)。
  (<- capped int (clis-limit GIB (* 20 GIB) (* 20 GIB) FLOOR CEILING None 0))
  (<- none int (clis-limit (* 512 MIB) (- GIB) (* 12 GIB) FLOOR CEILING None 0))
  (assert (= #(capped none) #(CEILING FLOOR)) #(capped none))
  ;; container の中の空きが線の空きより小さければ、そちらの半分。container に上限が無ければ線の空きだけ。
  (<- boxed int (clis-limit GIB (* 6 GIB) (* 2 GIB) FLOOR CEILING None 0))
  (<- unboxed int (clis-limit GIB (* 6 GIB) None FLOOR CEILING None 0))
  (assert (= #(boxed unboxed) #((* 2 GIB) (* 4 GIB))) #((/ boxed GIB) (/ unboxed GIB)))
  ;; 使いが天井を越えていても、使いより下げない。
  (<- over int (clis-limit (* 7 GIB) (* 6 GIB) None FLOOR CEILING (* 7 GIB) 0))
  (assert (= over (* 7 GIB)) (/ over GIB)))


(deftest test-a-widened-cli-layer-keeps-its-room-while-the-jobs-layer-can-give-way
  ;; 前の回に 4 GiB まで広げ、会話が 3 GiB まで伸びた。ほかの Pod が跳ねて空きが 0.5 GiB になっても、jobs の層(2 GiB)を縮めれば収まる
  ;; ので上限は 4 GiB のまま(受けたばかりの会話の伸びを OOM で止めない)。
  (<- kept int (clis-limit (* 3 GIB) (// GIB 2) (* 12 GIB) FLOOR CEILING (* 4 GIB) (* 2 GIB)))
  (assert (= kept (* 4 GIB)) (/ kept GIB))
  ;; 空きが −1.5 GiB: jobs の層を 0 まで縮めても 0.5 GiB しか残らないので、その分だけ残す。
  (<- shrunk int (clis-limit (* 3 GIB) (int (* -1.5 GIB)) (* 12 GIB) FLOOR CEILING (* 4 GIB) (* 2 GIB)))
  (assert (= shrunk (int (* 3.5 GIB))) (/ shrunk GIB))
  ;; jobs の層を縮めても足りない: clis の今の使いまで(使いより下げない)。
  (<- frozen int (clis-limit (* 3 GIB) (* -4 GIB) (* 12 GIB) FLOOR CEILING (* 4 GIB) GIB))
  (assert (= frozen (* 3 GIB)) (/ frozen GIB))
  ;; 床を越えて貸した量 = 上限 − 床と今の使いの大きい方(jobs の層の上限から引く)。
  (<- lent int (clis-lent (* 4 GIB) (* 3 GIB) FLOOR))
  (<- inside int (clis-lent FLOOR (* 512 MIB) FLOOR))
  (assert (= #(lent inside) #(GIB 0)) #(lent inside)))


(deftest test-the-container-room-counts-the-other-limited-layers-and-bounds-the-jobs-layer
  ;; jobs と clis の外の限る層の宣言の和: atlas は commands 1536M + exec 512M・helios は commands 1G + exec 512M。
  (<- atlas-held int (layout-held-bytes ATLAS-LAYOUT #("jobs" "clis")))
  (<- helios-held int (layout-held-bytes HELIOS-LAYOUT #("jobs" "clis")))
  (assert (= #(atlas-held helios-held) #((* 2 GIB) (* 1536 MIB))) #(atlas-held helios-held))
  ;; container の中の空き = 上限 − working set − 余裕 − held。上限の無い container は None。
  (<- box int (box-free CONTAINER-MAX (* 5 GIB) GIB atlas-held))
  (<- open (| int None) (box-free None (* 5 GIB) GIB atlas-held))
  (assert (= #(box open) #((* 14 GIB) None)) #(box open))
  ;; jobs の層の上限は、clis の上限までの残りを container の中の空きから引いた分まで。
  (<- jobs int (jobs-box-limit GIB (* 14 GIB) (* 5 GIB) (* 2 GIB)))
  (assert (= jobs (* 12 GIB)) (/ jobs GIB)))


(deftest test-the-ceiling-is-declared-per-machine-and-passed-to-the-job-children
  (val env {"WORKER_CLI_LAYER_CEILING" "6G"})
  (<- clis (| CliLayerSettings None) (cli-layer-settings-of env OWN ATLAS-LAYOUT "jobs"))
  (assert (= #(clis.dir clis.floor-bytes clis.ceiling-bytes clis.container-dir clis.held-bytes)
             #(CLIS FLOOR CEILING OWN (* 2 GIB)))
          clis)
  (<- environ dict (cli-ceiling-environ env))
  (assert (= environ {CLI-CEILING-CHILD-VAR (str CEILING)}) environ)
  ;; 宣言の無い機体は掛け直さず、子へも渡さない。
  (<- absent (| CliLayerSettings None) (cli-layer-settings-of {} OWN ATLAS-LAYOUT "jobs"))
  (<- quiet dict (cli-ceiling-environ {}))
  (assert (= #(absent quiet) #(None {})) #(absent quiet))
  ;; 天井が床より小さい・clis の層の床が無い宣言は起動を断る。
  (for [#(ceiling layout) #(#("1G" ATLAS-LAYOUT) #("6G" "jobs:memory.max=1G"))]
    (var refused False)
    (try
      (<- (cli-layer-settings-of {"WORKER_CLI_LAYER_CEILING" ceiling} OWN layout "jobs"))
      (except [ValueError]
        (:= refused True)))
    (assert refused #(ceiling layout))))


;; --- worker の読み(eviction-noted)--------------------------------------------------------------------------------------------------

(defhandler atlas-api []
  "atlas の Node の宣言を答える偽の API server。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "29662296Ki"} "allocatable" {"memory" "20749400Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))))


;; page cache の無い cgroup の memory.stat の中身の形(anon の byte を入れる)。
(val STAT "anon {}\nfile 0\ninactive_file 0\n")


(defk atlas-files [pods jobs clis container]
  {:pre [(: pods int) (: jobs int) (: clis int) (: container int)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の atlas の cgroup と ServiceAccount の file を作るため: kubepods・jobs・clis・container の使い(page cache 0)と、root は Pod の外に
   2 GiB。"
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
               (MemoryFile :path TOKEN :content b"token\n")))
  (val dirs (sorted (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                          :if (!= d f.path) d)))
  (MemoryFiles :dirs (tuple dirs) :files files))


(defk atlas-settings [clis]
  {:pre [(: clis bool)] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の atlas の線の設定を作るため。clis = 会話の CLI の層を掛け直すか(偽 = 天井の宣言の無い前の形)。"
  (<- jobs-cap (| int None) (layer-memory-max ATLAS-LAYOUT "jobs"))
  (<- held int (layout-held-bytes ATLAS-LAYOUT #("jobs" "clis")))
  (EvictionSettings :api "https://api.test" :token-file TOKEN :node "atlas" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                    :jobs-cap-bytes jobs-cap
                    :clis (if clis (CliLayerSettings :dir CLIS :floor-bytes FLOOR :ceiling-bytes CEILING :container-dir OWN :held-bytes held)
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
  ;; 平常の atlas: Pod の和 12 GiB(空き 7.79 GiB)・jobs 0.5 GiB・clis 1 GiB・container 3 GiB。余裕は宣言の無い機体の 1 GiB。
  (val pods (* 12 GIB))
  (val room (- ALLOCATABLE pods))
  (val cell (EvictionCell))
  (<- start MemoryFiles (atlas-files pods (// GIB 2) GIB (* 3 GIB)))
  (<- settings EvictionSettings (atlas-settings True))
  (<- files MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (atlas-api) slog-discard-handler]
                          (ticked-files settings cell 2)))
  ;; clis の上限 = 1 GiB + (空き − 余裕) の半分。jobs の上限 = jobs の使い + 空き − 余裕 − 床を越えて貸した量。
  (val free (- room GIB))
  (val clis-max (+ GIB (int (* free CLIS-ROOM-SHARE))))
  (<- written str (content-at files (+ CLIS "/memory.max")))
  (assert (= (int written) clis-max) (/ (int written) GIB))
  (<- jobs-max str (content-at files (+ JOBS "/memory.max")))
  (assert (= (int jobs-max) (- (+ (// GIB 2) free) (- clis-max FLOOR))) (/ (int jobs-max) GIB))
  ;; 2 つの層が上限まで伸びても、Pod の和は線 − 余裕の内(同じ空きを重ねて貸さない)。重ねて数えるのは、今までと同じ床の内の残り
  ;; (床 1.5 GiB − clis の使い 1 GiB)だけ。
  (assert (<= (+ (- pods (// GIB 2) GIB) (int jobs-max) clis-max) (+ (- ALLOCATABLE GIB) (- FLOOR GIB))))
  (assert (= #(cell.clis-limit cell.lent) #(clis-max (- clis-max FLOOR))) #(cell.clis-limit cell.lent))
  ;; 前の形(天井の宣言が無い)は clis の層に書かず、jobs の上限は空きの全部。
  (<- old-settings EvictionSettings (atlas-settings False))
  (<- before MemoryFiles (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (atlas-api) slog-discard-handler]
                           (ticked-files old-settings (EvictionCell) 1)))
  (<- untouched str (content-at before (+ CLIS "/memory.max")))
  (<- old-jobs str (content-at before (+ JOBS "/memory.max")))
  (assert (= #((int untouched) (int old-jobs)) #(FLOOR (+ (// GIB 2) free))) #(untouched old-jobs)))


(deftest test-an-unreadable-line-puts-the-cli-layer-back-on-its-floor-but-not-below-its-use
  ;; Node の宣言を読めない機体(API server が無い答え手 — 線を読めない): clis の上限は床と clis の今の使いの大きい方。
  (<- small MemoryFiles (atlas-files (* 12 GIB) (// GIB 2) GIB (* 3 GIB)))
  (<- big MemoryFiles (atlas-files (* 12 GIB) (// GIB 2) (* 3 GIB) (* 5 GIB)))
  (<- settings EvictionSettings (atlas-settings True))
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
  "模擬の atlas の memory の台本(byte と ms): others = 始めのほかの Pod の working set・spike / spike-from / spike-span / spike-until =
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
  "模擬の atlas の刻ごとの様子(頭の註): 台本・clis と jobs の層の上限・受けた会話の始まりの刻・数え。"
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


(defhandler atlas-world [#^ World world #^ MemoryCell cell #^ int run-until-ms]
  "見張りの外側の模擬の atlas(頭の註): Node の宣言を答える偽の API server・kubepods と root と container と jobs と clis の層の cgroup の
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
                  True (raise (ValueError (.format "模擬の atlas に無い file {}" path))))))
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
  "模擬の atlas の file のうち変わらない物: /proc/meminfo・PSI・worker の container の memory・ServiceAccount の token。"
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
  "模擬の atlas の process の表(見張りの止めの単位はどれも min-victim-bytes 未満で、見張りは止めない — 止めるのは模擬の kernel だけ)。"
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
  "本物の見張りを模擬の atlas と仮想時計の下で止めの合図まで回すため。clis = 会話の CLI の層を掛け直すか(偽 = 前の形 — 床のまま)。"
  (<- eviction EvictionSettings (atlas-settings clis))
  (val cell (MemoryCell))
  (<- rows tuple (scene-rows))
  (<- files MemoryFiles (machine-files))
  (<- seen SceneSeen (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files)
                                     (scripted-proc-table-handler rows) (atlas-world (World scene FLOOR) cell run-until-ms)]
                       (watch-then-scene (MemoryWatchSettings :eviction eviction) cell)))
  seen)


(defk lines-named [lines name]
  {:pre [(: lines tuple) (: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行から名 name の行の欄を出した順に引くため。"
  (tuple (gfor #(msg fields) lines :if (= msg name) fields)))


;; 平常の atlas(11-06 19:00〜21:30 JST の機体の使い 17.5〜20.6 GiB / 28.3 GiB の形): ほかの Pod 10 GiB・worker の本体と常駐の job 1.5 GiB・
;; task 1 GiB・使われていない CLI と daemon 0.5 GiB — Pod の和 13 GiB・追い出しまでの空き 約 6.8 GiB。180 秒からほかの Pod が 30 秒で
;; 4 GiB 跳ね(helios の 11-06 21:00 の probe の形を atlas の大きさに縮めた)、300 秒から戻る。受けるのは 240 秒まで(跳ねの後に縮むのを見る)。
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
  ;; 上限は床と天井の間で、跳ねの間に縮み(広げた分は jobs の層を縮めて残した後)、跳ねが戻った後に広がり直す。
  (assert (all (gfor l seen.limits (<= FLOOR l CEILING))) seen.limits)
  (val peak (max seen.limits))
  (val last (get seen.limits -1))
  (assert (> peak FLOOR) (/ peak GIB))
  (<- told tuple (lines-named seen.lines CLIS-LENT-LOG))
  (assert (>= (len told) 1) told)
  (assert (> (get (get told 0) "clis_limit_mib") (// FLOOR MIB)) told)
  (assert (>= last (min seen.limits)) (/ last GIB))
  ;; 跳ねの間に縮んだのは jobs の層(task が jobs の層の中で止まった)— 会話の CLI の広げた上限は残した。
  (assert (= seen.task-kills 1) seen.task-kills)
  ;; 前の形(天井の宣言が無い — clis は床 1536M のまま)の同じ機体は、会話を 1 本しか受けない。
  (<- before SceneSeen (scene-watched SPIKE False 400000))
  (assert (= #(before.most before.cli-kills) #(1 0)) before)
  (assert (= before.limits #()) before.limits))
